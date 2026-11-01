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


if __name__ == "__main__":
    unittest.main()
