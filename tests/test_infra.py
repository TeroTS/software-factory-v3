import importlib
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
        self.assertIsNotNone(
            importlib.util.find_spec("infra.stacks"), "Native CDK stacks missing"
        )
        from infra.stacks import Artifacts, Image, Workflow

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

    # Confirms the application entry point produces a local assembly, preserving
    # credential-free synthesis for reviewing infrastructure before deployment.
    def test_application_synthesizes_three_native_stacks_without_aws_lookups(self):
        from infra.app import main

        entry = Path(self.directory.name) / "entry"
        with patch("infra.app.App", return_value=App(outdir=str(entry))):
            main()
        self.assertTrue((entry / "manifest.json").is_file())

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
                            "Value": "arn:aws:secretsmanager:us-east-1:172318459814:secret:software-factory-poc/github-token-sXayGQ",
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
        from infra.stacks import stage_source

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
                "worker/server.py",
                "worker/task.py",
            },
        )
        root = Path(__file__).resolve().parents[1]
        self.assertTrue((staged / "image/managed_config.toml").is_file())
        self.assertEqual(
            (staged / "image/managed_config.toml").read_bytes(),
            (root / "image/managed_config.toml").read_bytes(),
        )
        self.assertTrue((staged / "image/requirements.txt").is_file())
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

    # Ensures synthesized cleanup preserves workflow state after a termination
    # request, so the VM ID remains available for confirming actual termination.
    def test_synthesized_termination_retains_vm_id_for_confirmation(self):
        resource = next(
            iter(
                self.workflow_template.find_resources(
                    "AWS::StepFunctions::StateMachine"
                ).values()
            )
        )
        encoded = resource["Properties"]["DefinitionString"]
        text = (
            encoded
            if isinstance(encoded, str)
            else "".join(
                piece if isinstance(piece, str) else "symbolic-value"
                for piece in encoded["Fn::Join"][1]
            )
        )
        deployed = json.loads(text)
        self.assertEqual(
            deployed["States"]["TerminateVM"].get("ResultPath"), "$.terminationRequest"
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
        role = next(iter(roles.values()))["Properties"]
        self.assertIn(
            "states.amazonaws.com", json.dumps(role["AssumeRolePolicyDocument"])
        )
        text = json.dumps(template)
        for action in (
            "lambda:RunMicrovm",
            "lambda:GetMicrovm",
            "lambda:CreateMicrovmAuthToken",
            "lambda:TerminateMicrovm",
            "iam:PassRole",
            "events:RetrieveConnectionCredentials",
            "states:InvokeHTTPEndpoint",
        ):
            self.assertIn(action, text)
        self.assertNotIn("AdministratorAccess", text)
        self.assertNotIn("states:GetExecutionHistory", text)

    # Checks Lambda role trust and bounded secret/model permissions without creating
    # a replacement secret, protecting scoped runtime access and secret preservation.
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
            "arn:aws:secretsmanager:us-east-1:172318459814:secret:software-factory-poc/github-token-sXayGQ",
        )
        inference = next(s for s in statements if "bedrock:InvokeModel" in s["Action"])
        self.assertEqual(len(inference["Resource"]), 5)
        self.assertNotIn('"Resource": "*"', json.dumps(statements))
        self.assertNotIn("AWS::SecretsManager::Secret", json.dumps(template))


if __name__ == "__main__":
    unittest.main()
