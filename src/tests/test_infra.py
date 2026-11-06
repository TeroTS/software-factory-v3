import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aws_cdk import App, Environment
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from aws_cdk.assertions import Match, Template


class InfrastructureTests(unittest.TestCase):
    def setUp(self):
        from src.infra.artifacts_stack import Artifacts
        from src.infra.image_stack import Image
        from src.infra.workflow_stack import Workflow

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = App(outdir=self.directory.name)
        env = Environment(account="172318459814", region="us-east-1")
        self.artifacts = Artifacts(self.app, "FactoryArtifacts", env=env)
        self.image = Image(
            self.app,
            "FactoryImage",
            artifact_bucket=self.artifacts.bucket_name,
            env=env,
        )
        self.workflow = Workflow(self.app, "FactoryWorkflow", image=self.image, env=env)
        self.image_template = Template.from_stack(self.image)
        self.workflow_template = Template.from_stack(self.workflow)

    def test_image_owns_empty_github_secret_and_outputs_arn(self):
        self.assertEqual(
            len(self.image_template.find_resources("AWS::SecretsManager::Secret")), 1
        )
        template = self.image_template.to_json()
        secret = template["Resources"]["GitHubToken"]
        for property_name in ("Name", "SecretString", "GenerateSecretString"):
            with self.subTest(property_name=property_name):
                self.assertNotIn(property_name, secret["Properties"])
        self.assertEqual(secret["DeletionPolicy"], "Retain")
        self.assertEqual(secret["UpdateReplacePolicy"], "Retain")
        self.assertEqual(
            template["Outputs"]["GitHubSecretArn"]["Value"], {"Ref": "GitHubToken"}
        )
        self.workflow_template.resource_count_is("AWS::SecretsManager::Secret", 0)
        receiver_secret_arn = self.workflow.resolve(self.image.github_secret_arn)
        self.assertIn("Fn::ImportValue", receiver_secret_arn)
        self.workflow_template.has_resource_properties(
            "AWS::Lambda::Function",
            {"Environment": {"Variables": {"GITHUB_SECRET_ARN": receiver_secret_arn}}},
        )

    def test_ingress_construct_scopes_and_logical_ids_remain_stable(self):
        expected = {
            "JobsTable": "JobsTable1970BC16",
            "ReceiverLogs": "ReceiverLogsDEB6297F",
            "ReceiverRole": "ReceiverRoleD4C38D72",
            "IssueReceiver": "IssueReceiver854600CC",
            "DispatcherLogs": "DispatcherLogsCDE1995F",
            "DispatcherRole": "DispatcherRoleBDE14D07",
            "Dispatcher": "DispatcherD4A12972",
            "JobNotifications": "JobNotifications",
        }
        resources = self.workflow_template.to_json()["Resources"]
        for name, logical_id in expected.items():
            with self.subTest(name=name):
                construct = self.workflow.node.find_child(name)
                self.assertIs(construct.node.scope, self.workflow)
                resource = (
                    construct
                    if name == "JobNotifications"
                    else construct.node.default_child
                )
                self.assertEqual(self.workflow.get_logical_id(resource), logical_id)
                self.assertIn(logical_id, resources)

    # Confirms the application entry point produces a local assembly, preserving
    # credential-free synthesis for reviewing infrastructure before deployment.
    def test_application_synthesizes_three_native_stacks_without_aws_lookups(self):
        from src.infra.app import main

        entry = Path(self.directory.name) / "entry"
        with patch("src.infra.app.App", return_value=App(outdir=str(entry))):
            main()
        self.assertTrue((entry / "manifest.json").is_file())

    def test_signed_webhook_and_stream_dispatch_reuse_standard_workflow(self):
        template = self.workflow_template
        template.resource_count_is("AWS::Lambda::Function", 2)
        template.has_resource_properties("AWS::Lambda::Url", {"AuthType": "NONE"})
        template.resource_count_is("AWS::SQS::Queue", 0)
        template.resource_count_is("AWS::Pipes::Pipe", 0)
        template.resource_count_is("AWS::Events::Connection", 1)
        template.has_resource_properties(
            "AWS::DynamoDB::Table",
            {
                "KeySchema": [
                    {"AttributeName": "PK", "KeyType": "HASH"},
                    {"AttributeName": "SK", "KeyType": "RANGE"},
                ],
                "AttributeDefinitions": [
                    {"AttributeName": "PK", "AttributeType": "S"},
                    {"AttributeName": "SK", "AttributeType": "S"},
                ],
                "StreamSpecification": {"StreamViewType": "NEW_IMAGE"},
            },
        )
        table_id, table_resource = next(
            iter(template.find_resources("AWS::DynamoDB::Table").items())
        )
        self.assertNotIn("GlobalSecondaryIndexes", table_resource["Properties"])
        states = self.workflow.node.find_child("CompleteQueuedJob").to_state_json()
        self.assertEqual(
            states["Parameters"]["TableName"], table_resource["Properties"]["TableName"]
        )
        table_arn = {"Fn::GetAtt": [table_id, "Arn"]}
        stream_arn = {"Fn::GetAtt": [table_id, "StreamArn"]}
        mapping = next(
            iter(template.find_resources("AWS::Lambda::EventSourceMapping").values())
        )["Properties"]
        self.assertEqual(mapping["EventSourceArn"], stream_arn)
        self.assertEqual(mapping["StartingPosition"], "TRIM_HORIZON")
        self.assertEqual(
            [
                json.loads(item["Pattern"])
                for item in mapping["FilterCriteria"]["Filters"]
            ],
            [
                {
                    "eventName": ["INSERT"],
                    "dynamodb": {"NewImage": {"status": {"S": ["PENDING"]}}},
                },
                {
                    "eventName": ["MODIFY"],
                    "dynamodb": {
                        "NewImage": {"status": {"S": ["COMPLETED", "FAILED"]}}
                    },
                },
            ],
        )
        template.has_resource_properties(
            "AWS::Lambda::Function",
            {
                "Environment": {
                    "Variables": {
                        "AUTHORIZED_REPOSITORY": Match.any_value(),
                        "PR_TARGET_BRANCH": Match.any_value(),
                        "GITHUB_SECRET_ARN": Match.any_value(),
                        "WEBHOOK_SECRET": {"Ref": "WebhookSigningSecret"},
                        "JOB_TABLE_NAME": Match.any_value(),
                    }
                }
            },
        )
        secret = template.to_json()["Parameters"]["WebhookSigningSecret"]
        self.assertTrue(secret["NoEcho"])
        self.assertNotIn("Default", secret)
        text = json.dumps(template.to_json())
        self.assertNotIn("AWS::SecretsManager::Secret", text)
        self.assertNotIn('"Resource": "*"', text)
        self.assertNotIn("states:StartSyncExecution", text)
        self.assertIn("dynamodb:PutItem", text)
        self.assertIn("dynamodb:Query", text)
        self.assertIn("dynamodb:UpdateItem", text)
        self.assertIn("states:StartExecution", text)
        self.assertNotIn("pipes.amazonaws.com", text)
        self.assertNotIn("JobQueueUrl", text)
        self.assertNotIn("JobDeadLetterQueueUrl", text)
        self.assertNotIn("sqs:", text)
        self.assertNotIn("AWSLambdaBasicExecutionRole", text)
        self.assertNotIn("LOCAL_AWS_ENDPOINT", text)
        self.assertNotIn("LOCAL_GITHUB_ENDPOINT", text)
        template.has_resource_properties(
            "AWS::Lambda::Function",
            {
                "Environment": {
                    "Variables": {
                        "AUTHORIZED_REPOSITORY": self.workflow.resolve(
                            self.image.repository
                        ),
                        "PR_TARGET_BRANCH": self.workflow.resolve(
                            self.image.target_branch
                        ),
                    }
                }
            },
        )
        functions = [
            resource["Properties"]
            for resource in template.find_resources("AWS::Lambda::Function").values()
        ]
        function = next(
            item
            for item in functions
            if "WEBHOOK_SECRET" in item["Environment"]["Variables"]
        )
        dispatcher = next(
            item
            for item in functions
            if "STATE_MACHINE_ARN" in item["Environment"]["Variables"]
        )
        role_scopes = {
            function["Role"]["Fn::GetAtt"][0]: {
                "secretsmanager:GetSecretValue": self.workflow.resolve(
                    self.image.github_secret_arn
                ),
                "dynamodb:PutItem": table_arn,
            },
            dispatcher["Role"]["Fn::GetAtt"][0]: {
                "dynamodb:Query": table_arn,
                "dynamodb:UpdateItem": table_arn,
                "dynamodb:DescribeStream": stream_arn,
                "dynamodb:GetRecords": stream_arn,
                "dynamodb:GetShardIterator": stream_arn,
                "states:StartExecution": {"Ref": "Workflow"},
            },
        }
        machine = template.find_resources("AWS::StepFunctions::StateMachine")[
            "Workflow"
        ]["Properties"]
        execution_role = machine["RoleArn"]["Fn::GetAtt"][0]
        queue_actions = [
            (statement["Action"], statement["Resource"])
            for policy in template.find_resources("AWS::IAM::Policy").values()
            if {"Ref": execution_role} in policy["Properties"].get("Roles", [])
            for statement in policy["Properties"]["PolicyDocument"]["Statement"]
            if "dynamodb:" in json.dumps(statement["Action"])
        ]
        self.assertEqual(queue_actions, [("dynamodb:UpdateItem", table_arn)])
        for role_id, expected in role_scopes.items():
            actual = {}
            for policy in template.find_resources("AWS::IAM::Policy").values():
                properties = policy["Properties"]
                if {"Ref": role_id} not in properties.get("Roles", []):
                    continue
                for statement in properties["PolicyDocument"]["Statement"]:
                    actions = statement["Action"]
                    for action in [actions] if isinstance(actions, str) else actions:
                        if not action.startswith("logs:"):
                            actual[action] = statement["Resource"]
            self.assertEqual(actual, expected)
        template.has_resource_properties(
            "AWS::Lambda::Permission",
            {
                "Action": "lambda:InvokeFunctionUrl",
                "Principal": "*",
                "FunctionUrlAuthType": "NONE",
            },
        )
        template.has_resource_properties(
            "AWS::Lambda::Permission",
            {
                "Action": "lambda:InvokeFunction",
                "Principal": "*",
                "InvokedViaFunctionUrl": True,
            },
        )

    # Checks private artifact storage and the native ARM64 image configuration,
    # protecting the approved Gate 2 design from custom providers or helper Lambdas.
    def test_native_image_full_configuration_and_private_artifacts(self):
        Template.from_stack(self.artifacts).has_resource_properties(
            "AWS::S3::Bucket",
            {
                "PublicAccessBlockConfiguration": {
                    "BlockPublicAcls": True,
                    "BlockPublicPolicy": True,
                    "IgnorePublicAcls": True,
                    "RestrictPublicBuckets": True,
                },
                "BucketEncryption": Match.any_value(),
            },
        )
        self.image_template.resource_count_is("AWS::Lambda::MicrovmImage", 1)
        self.image_template.has_resource_properties(
            "AWS::Lambda::MicrovmImage",
            {
                "AdditionalOsCapabilities": [],
                "BaseImageVersion": "3",
                "Resources": [{"MinimumMemoryInMiB": 2048}],
                "CpuConfigurations": [{"Architecture": "ARM_64"}],
                "Hooks": {
                    "Port": 8080,
                    "MicrovmImageHooks": {
                        "Ready": "ENABLED",
                        "ReadyTimeoutInSeconds": 60,
                        "Validate": "ENABLED",
                        "ValidateTimeoutInSeconds": 60,
                    },
                    "MicrovmHooks": {
                        "Run": "ENABLED",
                        "RunTimeoutInSeconds": 60,
                        "Terminate": "ENABLED",
                        "TerminateTimeoutInSeconds": 15,
                    },
                },
                "EnvironmentVariables": Match.array_with(
                    [
                        {
                            "Key": "GITHUB_SECRET_ARN",
                            "Value": {"Ref": "GitHubToken"},
                        }
                    ]
                ),
            },
        )
        self.app.synth()
        manifest = json.loads(
            (Path(self.directory.name) / "FactoryImage.assets.json").read_text()
        )
        targets = [
            destination["bucketName"]
            for asset in manifest["files"].values()
            for destination in asset["destinations"].values()
        ]
        self.assertIn(self.artifacts.bucket_name, targets)
        for stack in (self.artifacts, self.image, self.workflow):
            resources = Template.from_stack(stack).to_json()["Resources"]
            self.assertFalse(
                any(
                    resource["Type"].startswith("Custom::")
                    for resource in resources.values()
                )
            )
            if stack is not self.workflow:
                self.assertFalse(
                    any(
                        resource["Type"] == "AWS::Lambda::Function"
                        for resource in resources.values()
                    )
                )

    def test_repository_authorization_and_target_are_deployment_controlled(self):
        template = self.image_template.to_json()
        self.assertEqual(
            template["Parameters"]["AuthorizedRepository"]["Default"],
            "TeroTS/factory-smoke-test",
        )
        self.assertEqual(template["Parameters"]["PrTargetBranch"]["Default"], "main")
        self.image_template.has_resource_properties(
            "AWS::Lambda::MicrovmImage",
            {
                "EnvironmentVariables": Match.array_with(
                    [
                        {
                            "Key": "AUTHORIZED_REPOSITORY",
                            "Value": {"Ref": "AuthorizedRepository"},
                        },
                        {"Key": "PR_TARGET_BRANCH", "Value": {"Ref": "PrTargetBranch"}},
                    ]
                )
            },
        )

    def test_staged_image_contains_pinned_runtime_dependencies(self):
        from src.infra.image_stack import stage_source

        staged = Path(self.directory.name) / "factory-source"
        (staged / "stale.py").write_text("obsolete generated source\n")
        stage_source(self.directory.name)
        self.assertEqual(
            {
                str(path.relative_to(staged))
                for path in staged.rglob("*")
                if path.is_file()
            },
            {
                "Dockerfile",
                "image/requirements.txt",
                "image/managed_config.toml",
                "src/__init__.py",
                "src/worker/__init__.py",
                "src/worker/server.py",
                "src/worker/task.py",
                "src/worker/prompts.py",
                "src/worker/review.py",
                "image/skills/implement/SKILL.md",
                "image/skills/implement/agents/openai.yaml",
                "image/skills/code-review/SKILL.md",
                "image/skills/code-review/agents/openai.yaml",
                "image/skills/pr/SKILL.md",
                "image/skills/pr/CREDITS.md",
                "image/skills/pr/agents/openai.yaml",
                "image/skills/tdd/SKILL.md",
                "image/skills/tdd/tests.md",
                "image/skills/tdd/mocking.md",
                "image/skills/tdd/agents/openai.yaml",
                "image/skills/codebase-design/SKILL.md",
                "image/skills/codebase-design/DEEPENING.md",
                "image/skills/codebase-design/DESIGN-IT-TWICE.md",
                "image/skills/codebase-design/agents/openai.yaml",
            },
        )
        root = Path(__file__).resolve().parents[2]
        for path in (staged / "image/skills").rglob("*"):
            if path.is_file():
                self.assertEqual(
                    path.read_bytes(), (root / path.relative_to(staged)).read_bytes()
                )
        self.assertEqual(
            (staged / "image/managed_config.toml").read_bytes(),
            (root / "image/managed_config.toml").read_bytes(),
        )
        self.assertEqual(
            (staged / "image/requirements.txt").read_bytes(),
            (root / "image/requirements.txt").read_bytes(),
        )

    def test_workflow_uses_high_level_machine_without_image_inspection_permissions(
        self,
    ):
        self.assertIsInstance(
            self.workflow.node.find_child("Workflow"), sfn.StateMachine
        )
        self.assertEqual(
            set(
                self.workflow_template.find_resources(
                    "AWS::StepFunctions::StateMachine"
                )
            ),
            {"Workflow"},
        )
        text = json.dumps(self.workflow_template.to_json())
        for removed in ("lambda:GetMicrovmImage", "lambda:GetMicrovmImageVersion"):
            self.assertNotIn(removed, text)
        self.assertNotIn('"Resource": "*"', text)
        for name in (
            "LaunchVM",
            "GetVMState",
            "GetWorkerToken",
            "TerminateVM",
            "GetTerminationState",
        ):
            self.assertIsInstance(
                self.workflow.node.find_child(name), tasks.CallAwsService
            )
        raw_states = [
            child
            for child in self.workflow.node.children
            if isinstance(child, sfn.CustomState)
        ]
        self.assertEqual([state.node.id for state in raw_states], ["PollWorker"])
        statements = [
            statement
            for policy in self.workflow_template.find_resources(
                "AWS::IAM::Policy"
            ).values()
            for statement in policy["Properties"]["PolicyDocument"]["Statement"]
        ]
        sdk = [
            statement
            for statement in statements
            if isinstance(statement["Action"], str)
            and statement["Action"].startswith("lambda:")
            and statement["Action"] != "lambda:PassNetworkConnector"
        ]
        self.assertEqual(len(sdk), 4)
        self.assertEqual(
            {statement["Action"] for statement in sdk},
            {
                "lambda:RunMicrovm",
                "lambda:GetMicrovm",
                "lambda:CreateMicrovmAuthToken",
                "lambda:TerminateMicrovm",
            },
        )
        for statement in sdk:
            self.assertEqual(
                statement["Resource"],
                self.workflow.resolve(self.image.image.attr_image_arn),
            )
        http = next(
            statement
            for statement in statements
            if statement["Action"] == "states:InvokeHTTPEndpoint"
        )
        self.assertEqual(
            http["Condition"],
            {
                "StringEquals": {"states:HTTPMethod": "GET"},
                "StringLike": {
                    "states:HTTPEndpoint": "https://*.lambda-microvm.us-east-1.on.aws/status"
                },
            },
        )

    # Requires an operator-supplied image pin and a non-auth-overriding connection,
    # while keeping orchestration permissions free of administrator/history access.
    def test_workflow_requires_explicit_pin_and_marker_only_connection(self):
        template = self.workflow_template.to_json()
        self.assertNotIn("Default", template["Parameters"]["ImageVersion"])
        self.workflow_template.has_resource_properties(
            "AWS::StepFunctions::StateMachine", {"StateMachineType": "STANDARD"}
        )
        self.workflow_template.has_resource_properties(
            "AWS::Events::Connection",
            {
                "AuthorizationType": "API_KEY",
                "AuthParameters": {
                    "ApiKeyAuthParameters": {
                        "ApiKeyName": "X-Poc-Connection",
                        "ApiKeyValue": "software-factory-poc",
                    }
                },
            },
        )
        roles = self.workflow_template.find_resources("AWS::IAM::Role")
        role = next(
            role["Properties"]
            for role in roles.values()
            if "states.amazonaws.com"
            in json.dumps(role["Properties"]["AssumeRolePolicyDocument"])
        )
        self.assertIn(
            "states.amazonaws.com", json.dumps(role["AssumeRolePolicyDocument"])
        )
        text = json.dumps(template)
        for action in (
            "iam:PassRole",
            "events:RetrieveConnectionCredentials",
            "states:InvokeHTTPEndpoint",
        ):
            self.assertIn(action, text)
        self.assertNotIn("AdministratorAccess", text)
        self.assertNotIn("states:GetExecutionHistory", text)

    # Checks Lambda role trust and restricts runtime access to the stack-owned token
    # and approved model resources; credentials remain outside synthesized templates.
    def test_runtime_trust_and_secret_model_scope(self):
        template = self.image_template.to_json()
        roles = self.image_template.find_resources("AWS::IAM::Role")
        self.assertEqual(len(roles), 2)
        for role in roles.values():
            trust = json.dumps(role["Properties"]["AssumeRolePolicyDocument"])
            self.assertIn("sts:TagSession", trust)
            self.assertIn("lambda.amazonaws.com", trust)
        policies = self.image_template.find_resources("AWS::IAM::Policy")
        statements = [
            s
            for policy in policies.values()
            for s in policy["Properties"]["PolicyDocument"]["Statement"]
        ]
        secret = next(
            s for s in statements if s["Action"] == "secretsmanager:GetSecretValue"
        )
        self.assertEqual(
            secret["Resource"],
            {"Ref": "GitHubToken"},
        )
        inference = next(s for s in statements if "bedrock:InvokeModel" in s["Action"])
        self.assertEqual(len(inference["Resource"]), 5)
        self.assertNotIn('"Resource": "*"', json.dumps(statements))
        self.assertNotIn("github-token-sXayGQ", json.dumps(template))


if __name__ == "__main__":
    unittest.main()
