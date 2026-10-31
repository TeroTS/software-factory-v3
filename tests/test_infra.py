import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aws_cdk import App, Environment
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

    def test_application_synthesizes_three_native_stacks_without_aws_lookups(self):
        from infra.app import main

        entry = Path(self.directory.name) / "entry"
        with patch("infra.app.App", return_value=App(outdir=str(entry))):
            main()
        self.assertTrue((entry / "manifest.json").is_file())

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
            deployed["States"]["Terminate"].get("ResultPath"), "$.terminationRequest"
        )

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
            "lambda:GetMicrovmImageVersion",
            "iam:PassRole",
            "events:RetrieveConnectionCredentials",
            "states:InvokeHTTPEndpoint",
        ):
            self.assertIn(action, text)
        self.assertNotIn("AdministratorAccess", text)
        self.assertNotIn("states:GetExecutionHistory", text)

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
