import re
import subprocess
import tomllib
import unittest
from pathlib import Path


class ImageRuntimeTests(unittest.TestCase):
    # Keep versioned startup and the repository's python3 command on the selected
    # runtime without modifying the interpreter used by operating-system packages.
    def test_image_selects_python313_for_startup_and_repository_commands(self):
        dockerfile = (
            Path(__file__).resolve().parents[2] / "image/Dockerfile"
        ).read_text()
        self.assertIn("dnf install -y python3.13 ", dockerfile)
        self.assertIn("ln -s /usr/bin/python3.13 /usr/local/bin/python3", dockerfile)
        self.assertIn('CMD ["python3.13", "-m", "src.worker.server"]', dockerfile)
        self.assertIn("COPY src/__init__.py /opt/factory/src/__init__.py", dockerfile)
        self.assertIn("COPY src/worker /opt/factory/src/worker", dockerfile)

    def test_image_installs_pinned_docker_for_factory_scripts(self):
        root = Path(__file__).resolve().parents[2]
        dockerfile = (root / "image/Dockerfile").read_text()
        self.assertIn("ARG DOCKER_VERSION=29.9.3\n", dockerfile)
        self.assertIn("ARG COMPOSE_VERSION=5.7.0\n", dockerfile)
        self.assertIn("docker-${DOCKER_VERSION}.tgz", dockerfile)
        self.assertIn("v${COMPOSE_VERSION}/docker-compose-linux-aarch64", dockerfile)
        self.assertIn("groupadd docker", dockerfile)
        self.assertIn("usermod -aG docker factory", dockerfile)
        self.assertIn("HOME=/home/factory", dockerfile)
        self.assertIn("USER root", dockerfile)
        self.assertNotIn("AWS_REGION=", dockerfile)
        self.assertNotIn("AWS_DEFAULT_REGION=", dockerfile)
        self.assertNotIn("compose_experiment", dockerfile)
        self.assertNotIn("compose_probe", dockerfile)

    # Keep Codex's default pin in a build argument used by the actual installation.
    def test_image_installs_codex_at_build_argument_version(self):
        dockerfile = (
            Path(__file__).resolve().parents[2] / "image/Dockerfile"
        ).read_text()
        self.assertIn("ARG CODEX_VERSION=0.166.0\n", dockerfile)
        self.assertIn('"@openai/codex@${CODEX_VERSION}"', dockerfile)

    # Static policy is packaged separately from credentials and owned by root in the image.
    def test_image_packages_factory_codex_policy(self):
        root = Path(__file__).resolve().parents[2]
        dockerfile = (root / "image/Dockerfile").read_text()
        self.assertIn(
            "COPY image/managed_config.toml /etc/codex/managed_config.toml", dockerfile
        )
        self.assertIn(
            "!image/managed_config.toml", (root / ".dockerignore").read_text()
        )
        config = tomllib.loads((root / "image/managed_config.toml").read_text())
        self.assertEqual(
            config,
            {
                "model": "us.openai.gpt-6.1-sol",
                "model_provider": "amazon-bedrock-runtime",
                "sandbox_mode": "workspace-write",
                "web_search": "disabled",
                "approval_policy": "never",
                "sandbox_workspace_write": {"network_access": True},
                "shell_environment_policy": {
                    "inherit": "all",
                    "ignore_default_excludes": True,
                },
            },
        )

    def test_factory_skill_sources_have_complete_local_references_and_metadata(self):
        root = Path(__file__).resolve().parents[2]
        bundle = root / "image/skills"
        expected = {"implement", "code-review", "pr", "tdd"}
        self.assertEqual({path.name for path in bundle.iterdir()}, expected)
        for name in expected:
            with self.subTest(skill=name):
                skill = bundle / name / "SKILL.md"
                text = skill.read_text()
                frontmatter = text.split("---", 2)[1]
                self.assertIn(f"name: {name}\n", frontmatter)
                self.assertRegex(frontmatter, r"description: .+")
                self.assertNotIn("disable-model-invocation: true", frontmatter)
                metadata = (bundle / name / "agents/openai.yaml").read_text()
                self.assertIn("interface:\n", metadata)
                self.assertNotIn("allow_implicit_invocation: false", metadata)
        implement = (bundle / "implement/agents/openai.yaml").read_text()
        self.assertIn("allow_implicit_invocation: true", implement)
        for path in bundle.rglob("*.md"):
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", path.read_text()):
                if "://" in target or target.startswith("#"):
                    continue
                resolved = (path.parent / target.split("#", 1)[0]).resolve()
                with self.subTest(source=path, reference=target):
                    self.assertTrue(resolved.is_relative_to(bundle))
                    self.assertTrue(resolved.is_file())

    def test_factory_skill_sources_are_not_git_ignored(self):
        root = Path(__file__).resolve().parents[2]
        included = [
            str(path.relative_to(root))
            for path in (root / "image/skills").rglob("*")
            if path.is_file()
        ]
        excluded = ".agents/skills/unrelated/SKILL.md"
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--stdin"],
            cwd=root,
            input="\n".join([*included, excluded]) + "\n",
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout.splitlines(), [excluded])

    def test_image_installs_only_locked_runtime_dependencies(self):
        root = Path(__file__).resolve().parents[2]
        dockerfile = (root / "image/Dockerfile").read_text()
        self.assertIn("python3.13-pip", dockerfile)
        self.assertIn("COPY image/requirements.txt", dockerfile)
        self.assertIn("python3.13 -m pip install --no-cache-dir -r", dockerfile)
        lock = tomllib.loads((root / "uv.lock").read_text())
        versions = {package["name"]: package["version"] for package in lock["package"]}
        requirements = (root / "image/requirements.txt").read_text()
        self.assertIn("boto3==", requirements)
        for line in requirements.splitlines():
            if line and not line.startswith((" ", "#")):
                name, version = line.split("==")
                self.assertEqual(version, versions[name])
        for name in ("ruff", "coverage", "aws-cdk-lib", "constructs"):
            self.assertNotIn(name + "==", requirements)
        self.assertIn("!image/requirements.txt", (root / ".dockerignore").read_text())


if __name__ == "__main__":
    unittest.main()
