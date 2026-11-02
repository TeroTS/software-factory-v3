import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class ImagePinTests(unittest.TestCase):
    def setUp(self):
        from tools.verify_image import verify

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
        from tools.verify_image import main

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
                patch(
                    "sys.argv",
                    ["verify_image", "--version", "1.0", "--template", str(template)],
                ),
                patch(
                    "tools.verify_image.subprocess.check_output",
                    side_effect=[json.dumps(self.image), json.dumps(self.version)],
                ) as read,
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                main()
            self.assertEqual(read.call_count, 2)
            self.assertEqual(
                [call.args[0][2] for call in read.call_args_list],
                ["get-microvm-image", "get-microvm-image-version"],
            )
            self.assertIn("FactoryWorkflow:ImageVersion=1.0", output.getvalue())

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
