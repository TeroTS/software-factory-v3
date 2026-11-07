import json
import os
import pwd
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from pathlib import Path


@unittest.skipUnless(
    os.environ.get("FACTORY_SANDBOX_TEST") == "1",
    "Real Linux sandbox is checked by make preflight",
)
class SandboxTests(unittest.TestCase):
    # Query the real pinned CLI without model access; bound the process even on bad output.
    def read_app_server(
        self, root, home, overrides=(), method="config/read", params=None
    ):
        (home / ".codex").mkdir(exist_ok=True)
        with tempfile.TemporaryFile(mode="w+") as errors:
            process = subprocess.Popen(
                ["codex", *overrides, "app-server", "--listen", "stdio://"],
                cwd=root,
                env={
                    **os.environ,
                    "HOME": str(home),
                    "CODEX_HOME": str(home / ".codex"),
                },
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=errors,
                text=True,
            )
            timer = threading.Timer(15, process.kill)
            timer.start()
            try:
                responses = []
                for request in (
                    {
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "clientInfo": {"name": "factory-preflight", "version": "1"},
                            "capabilities": {"experimentalApi": True},
                        },
                    },
                    {
                        "id": 2,
                        "method": method,
                        "params": params
                        if params is not None
                        else {"includeLayers": True, "cwd": str(root)},
                    },
                ):
                    process.stdin.write(json.dumps(request) + "\n")
                    process.stdin.flush()
                    for line in process.stdout:
                        response = json.loads(line)
                        if response.get("id") == request["id"]:
                            self.assertNotIn("error", response)
                            responses.append(response["result"])
                            break
                    else:
                        errors.seek(0)
                        self.fail(f"Codex did not return {method}: " + errors.read())
                self.assertIn("/0.166.0 ", responses[0]["userAgent"])
                return responses[1]
            finally:
                timer.cancel()
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)

    # Confirm the pinned CLI exposes fresh structured-output/result-file options without model inference.
    def test_fresh_review_cli_capabilities(self):
        help_text = subprocess.check_output(
            ["codex", "exec", "--help"], text=True, timeout=15
        )
        for option in (
            "--ephemeral",
            "--output-schema",
            "--output-last-message",
            "--sandbox",
        ):
            self.assertIn(option, help_text)

    # Managed policy wins over even trusted project/user/session settings on the pinned CLI.
    def test_factory_config_loading_and_override_precedence(self):
        policy = Path("/etc/codex/managed_config.toml")
        self.assertTrue(policy.is_file(), "Factory Codex policy missing from image")
        self.assertEqual(policy.stat().st_uid, 0)
        self.assertFalse(os.access(policy, os.W_OK))
        self.assertFalse(os.access(policy.parent, os.W_OK))
        expected = tomllib.loads(policy.read_text())
        with tempfile.TemporaryDirectory(dir="/workspace") as directory:
            root = Path(directory)
            home = root / "home"
            (home / ".codex").mkdir(parents=True)
            (root / ".git").mkdir()
            (root / ".codex").mkdir()
            for attempt in (False, True):
                with self.subTest(override_attempt=attempt):
                    overrides = []
                    if attempt:
                        hostile = """model = "override-model"
model_provider = "openai"
sandbox_mode = "danger-full-access"
web_search = "live"
approval_policy = "on-request"
[sandbox_workspace_write]
network_access = false
[shell_environment_policy]
inherit = "none"
ignore_default_excludes = false
"""
                        (root / ".codex/config.toml").write_text(hostile)
                        (home / ".codex/config.toml").write_text(
                            hostile
                            + f'\n[projects.{json.dumps(str(root))}]\ntrust_level = "trusted"\n'
                        )
                        overrides = [
                            "-c",
                            'model="cli-override"',
                            "-c",
                            'sandbox_mode="danger-full-access"',
                        ]
                    result = self.read_app_server(root, home, overrides)
                    for key, value in expected.items():
                        if isinstance(value, dict):
                            for name, setting in value.items():
                                self.assertEqual(result["config"][key][name], setting)
                        else:
                            self.assertEqual(result["config"][key], value)
                    managed = result["layers"][0]
                    self.assertEqual(
                        managed["name"],
                        {
                            "type": "legacyManagedConfigTomlFromFile",
                            "file": str(policy),
                        },
                    )
                    self.assertEqual(managed["config"], expected)
                    if attempt:
                        project = next(
                            layer
                            for layer in result["layers"]
                            if layer["name"]["type"] == "project"
                        )
                        self.assertIsNone(project.get("disabledReason"))
                        self.assertEqual(
                            project["config"]["sandbox_mode"], "danger-full-access"
                        )

    # Check native skill discovery and canonical permissions as the actual factory user, offline.
    def test_factory_skills_are_discoverable_and_read_only(self):
        bundle = Path("/opt/factory/skills")
        references = {
            "implement": (),
            "code-review": (),
            "pr": ("CREDITS.md",),
            "tdd": ("tests.md", "mocking.md"),
        }
        expected = set(references)
        self.assertEqual(os.getuid(), pwd.getpwnam("factory").pw_uid)
        self.assertTrue(bundle.is_dir(), "Factory skill bundle missing from image")
        self.assertEqual({path.name for path in bundle.iterdir()}, expected)
        self.assertEqual(
            {
                str(path.relative_to(bundle))
                for path in bundle.rglob("*")
                if path.is_file()
            },
            {
                f"{name}/{filename}"
                for name, files in references.items()
                for filename in ("SKILL.md", "agents/openai.yaml", *files)
            },
        )
        self.assertEqual((Path.home() / ".agents/skills").resolve(), bundle)
        for path in (bundle.parent, bundle, *bundle.rglob("*")):
            with self.subTest(path=path):
                self.assertEqual(path.stat().st_uid, 0)
                self.assertFalse(os.access(path, os.W_OK))
                self.assertTrue(os.access(path, os.R_OK))
        with tempfile.TemporaryDirectory(dir="/workspace") as directory:
            root = Path(directory)
            result = self.read_app_server(
                root,
                Path.home(),
                method="skills/list",
                params={"cwds": [str(root)], "forceReload": True},
            )
            self.assertEqual(len(result["data"]), 1)
            discovered = result["data"][0]
            self.assertEqual(discovered["errors"], [])
            skills = {
                skill["name"]: skill
                for skill in discovered["skills"]
                if skill["name"] in expected
            }
            self.assertEqual(set(skills), expected)
            for name, skill in skills.items():
                self.assertEqual(
                    Path(skill["path"]).resolve(), bundle / name / "SKILL.md"
                )
                self.assertTrue(skill["enabled"])

    # Verify the actual ARM64 image runtime and both invocation names, including
    # script shebangs, before accepting the interpreter selection.
    def test_selected_runtime_and_repository_interpreter(self):
        self.assertEqual(sys.version_info[:2], (3, 13))
        self.assertEqual(
            subprocess.check_output(["uname", "-m"], text=True).strip(), "aarch64"
        )
        for executable in (sys.executable, "python3.13", "python3"):
            with self.subTest(executable=executable):
                version = subprocess.check_output(
                    [executable, "-c", "import sys; print(sys.version_info[:2])"],
                    text=True,
                ).strip()
                self.assertEqual(version, "(3, 13)")
        with tempfile.TemporaryDirectory(dir="/workspace") as directory:
            script = Path(directory) / "verify"
            script.write_text(
                "#!/usr/bin/env python3\nimport sys\nassert sys.version_info[:2] == (3, 13)\n"
            )
            script.chmod(0o755)
            subprocess.run([str(script)], check=True, timeout=5)

    # Exercises the real Linux sandbox's local edit/test/commit workflow without
    # credentials, checking tool compatibility without disabling Codex isolation.
    def test_real_sandbox_search_edit_tests_and_git_metadata(self):
        with tempfile.TemporaryDirectory(dir="/workspace") as directory:
            root = Path(directory)
            (root / ".codex").mkdir()
            (root / ".git").mkdir()
            script = """from pathlib import Path
import subprocess
import sys

assert sys.version_info[:2] == (3, 13)
assert subprocess.check_output(["python3", "-c", "import sys; print(sys.version_info[:2])"], text=True).strip() == "(3, 13)"

def run(*args):
    return subprocess.check_output(args, text=True)

run("git", "init", "-b", "main")
run("git", "config", "user.name", "Sandbox Fixture")
run("git", "config", "user.email", "fixture@example.invalid")
Path("greeting.py").write_text("def message():\\n    return 'hello'\\n")
Path("tests").mkdir()
Path("tests/test_greeting.py").write_text("import unittest\\nfrom greeting import message\\nclass Tests(unittest.TestCase):\\n    def test_message(self):\\n        self.assertEqual(message(), 'hello')\\n")
Path("scripts").mkdir()
Path("scripts/setup").write_text("#!/bin/sh\\nexit 0\\n")
Path("scripts/verify").write_text("#!/bin/sh\\nexec python3 -m unittest discover -s tests -v\\n")
for script in ("setup", "verify"):
    Path("scripts", script).chmod(0o755)
run("./scripts/setup")
run("./scripts/verify")
run("git", "add", ".")
run("git", "commit", "-m", "baseline")
base = run("git", "rev-parse", "HEAD").strip()
run("git", "switch", "-c", "factory/sandbox-preflight")
assert "def message" in run("rg", "def message", "greeting.py")
for path in ("greeting.py", "tests/test_greeting.py"):
    file = Path(path)
    file.write_text(file.read_text().replace("'hello'", "'hello world'"))
run("./scripts/verify")
run("git", "add", ".")
run("git", "commit", "-m", "task")
run("git", "merge-base", "--is-ancestor", base, "HEAD")
assert not run("git", "status", "--porcelain").strip()
print("sandbox-search-edit-test-git-ok")
"""
            result = subprocess.run(
                [
                    "codex",
                    "sandbox",
                    "-c",
                    'sandbox_mode="workspace-write"',
                    "-c",
                    "sandbox_workspace_write.network_access=true",
                    "-c",
                    f'sandbox_workspace_write.writable_roots=["{root / ".git"}"]',
                    "--",
                    sys.executable,
                    "-c",
                    script,
                ],
                cwd=root,
                env={
                    "PATH": os.environ["PATH"],
                    "HOME": directory,
                    "CODEX_HOME": str(root / ".codex"),
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("sandbox-search-edit-test-git-ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
