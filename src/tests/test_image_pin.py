import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class ImagePinTests(unittest.TestCase):
    def setUp(self):
        from src.tools.verify_image import verify

        self.verify = verify
        self.image = {
            "imageArn": "image-arn",
            "state": "UPDATED",
            "latestActiveImageVersion": "1.0",
        }
        self.version = {
            "imageArn": "image-arn",
            "imageVersion": "1.0",
            "state": "SUCCESSFUL",
            "status": "ACTIVE",
            "codeArtifact": {"uri": "s3://artifacts/image/source.zip"},
        }

    # Checks the deployment pin against synthesized source using read-only calls,
    # so verification cannot provision or update the image.
    def test_read_only_cli_checks_synthesized_source_and_prints_pin(self):
        from src.tools.verify_image import main

        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "image.json"
            template.write_text(
                json.dumps(
                    {
                        "Resources": {
                            "Image": {
                                "Type": "AWS::Lambda::MicrovmImage",
                                "Properties": {
                                    "Name": "software-factory-poc",
                                    "CodeArtifact": {
                                        "Uri": "s3://artifacts/image/source.zip"
                                    },
                                },
                            }
                        }
                    }
                )
            )
            with (
                patch.dict(
                    "os.environ",
                    {
                        "AWS_ACCOUNT_ID": "111111111111",
                        "AWS_REGION": "eu-west-1",
                        "AWS_PROFILE": "test-operator",
                    },
                ),
                patch(
                    "sys.argv",
                    ["verify_image", "--version", "1.0", "--template", str(template)],
                ),
                patch(
                    "src.tools.verify_image.subprocess.check_output",
                    side_effect=[json.dumps(self.image), json.dumps(self.version)],
                ) as read,
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                main()
            self.assertEqual(read.call_count, 2)
            for call in read.call_args_list:
                command = call.args[0]
                self.assertIn(
                    "arn:aws:lambda:eu-west-1:111111111111:microvm-image:software-factory-poc",
                    command,
                )
                self.assertEqual(command[command.index("--region") + 1], "eu-west-1")
                self.assertEqual(
                    command[command.index("--profile") + 1], "test-operator"
                )
            self.assertEqual(
                [call.args[0][2] for call in read.call_args_list],
                ["get-microvm-image", "get-microvm-image-version"],
            )
            self.assertIn("FactoryWorkflow:ImageVersion=1.0", output.getvalue())

    # Reject missing destination settings before any AWS CLI read.
    def test_cli_requires_explicit_destination(self):
        from src.tools.verify_image import main

        with (
            patch.dict("os.environ", {}, clear=True),
            patch(
                "sys.argv", ["verify_image", "--version", "1.0", "--profile", "test"]
            ),
            patch("src.tools.verify_image.subprocess.check_output") as read,
            contextlib.redirect_stderr(io.StringIO()) as output,
            self.assertRaises(SystemExit),
        ):
            main()
        read.assert_not_called()
        self.assertIn("AWS_ACCOUNT_ID", output.getvalue())
        self.assertIn("AWS_REGION", output.getvalue())

    # Rejects stale, mismatched, or unusable builds so the workflow cannot pin
    # a version that fails the current-source and active-build checks.
    def test_only_exact_successful_active_current_source_is_pinnable(self):
        self.verify(self.image, self.version, "1.0", "s3://artifacts/image/source.zip")
        for update in (
            {"state": "FAILED"},
            {"status": "INACTIVE"},
            {"imageVersion": "older"},
            {"imageArn": "other"},
            {"codeArtifact": {"uri": "s3://old-artifact"}},
        ):
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.verify(
                    self.image,
                    {**self.version, **update},
                    "1.0",
                    "s3://artifacts/image/source.zip",
                )
        for update in ({"state": "UPDATING"}, {"latestActiveImageVersion": "older"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.verify(
                    {**self.image, **update},
                    self.version,
                    "1.0",
                    "s3://artifacts/image/source.zip",
                )


if __name__ == "__main__":
    unittest.main()
