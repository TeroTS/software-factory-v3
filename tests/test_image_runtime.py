import tomllib
import unittest
from pathlib import Path


class ImageRuntimeTests(unittest.TestCase):
    # Keep versioned startup and the repository's python3 command on the selected
    # runtime without modifying the interpreter used by operating-system packages.
    def test_image_selects_python313_for_startup_and_repository_commands(self):
        dockerfile = (
            Path(__file__).resolve().parents[1] / "image/Dockerfile"
        ).read_text()
        self.assertIn("dnf install -y python3.13 ", dockerfile)
        self.assertIn("ln -s /usr/bin/python3.13 /usr/local/bin/python3", dockerfile)
        self.assertIn('CMD ["python3.13", "-m", "worker.server"]', dockerfile)

    def test_image_installs_only_locked_runtime_dependencies(self):
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / "image/Dockerfile").read_text()
        self.assertIn("python3.13-pip", dockerfile)
        self.assertIn("COPY image/requirements.txt", dockerfile)
        self.assertIn("python3.13 -m pip install --no-cache-dir -r", dockerfile)
        self.assertNotIn("--require-hashes", dockerfile)
        lock = tomllib.loads((root / "uv.lock").read_text())
        versions = {package["name"]: package["version"] for package in lock["package"]}
        requirements = (root / "image/requirements.txt").read_text()
        self.assertIn("boto3==", requirements)
        self.assertNotIn("--hash", requirements)
        for line in requirements.splitlines():
            if line and not line.startswith((" ", "#")):
                name, version = line.split("==")
                self.assertEqual(version, versions[name])
        for name in ("ruff", "coverage", "aws-cdk-lib", "constructs"):
            self.assertNotIn(name + "==", requirements)
        self.assertIn("!image/requirements.txt", (root / ".dockerignore").read_text())


if __name__ == "__main__":
    unittest.main()
