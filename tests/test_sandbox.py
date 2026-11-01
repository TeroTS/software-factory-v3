import json
import os
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
    # Read real pinned-Codex layers without contacting a model; bound the process even on bad output.
    def read_config(self, root, home, overrides=()):
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
                        "method": "config/read",
                        "params": {
                            "includeLayers": True,
                            "cwd": str(root),
                        },
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
                        self.fail(
                            "Codex did not return configuration: " + errors.read()
                        )
                self.assertIn("/0.166.0 ", responses[0]["userAgent"])
                return responses[1]
            finally:
                timer.cancel()
                process.terminate()
                process.communicate(timeout=5)

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
                    result = self.read_config(root, home, overrides)
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
Path("calculator.py").write_text("def add(a, b):\\n    return a + b\\n")
Path("tests").mkdir()
Path("tests/test_calculator.py").write_text("import unittest\\nfrom calculator import add\\nclass Tests(unittest.TestCase):\\n    # Preserves addition as the sandbox demonstration baseline.\\n    def test_add(self):\\n        self.assertEqual(add(2, 3), 5)\\n")
run("python3", "-m", "unittest", "discover", "-s", "tests", "-v")
run("git", "add", ".")
run("git", "commit", "-m", "baseline")
base = run("git", "rev-parse", "HEAD").strip()
run("git", "switch", "-c", "factory/sandbox-preflight")
assert "def add" in run("rg", "def add", "calculator.py")
with Path("calculator.py").open("a") as file:
    file.write("\\ndef multiply(a, b):\\n    return a * b\\n")
with Path("tests/test_calculator.py").open("a") as file:
    file.write("    # Covers positive, negative, and zero products required by the demonstration task.\\n    def test_multiply(self):\\n        from calculator import multiply\\n        for a,b in ((2,3),(-2,3),(-2,-3),(0,3),(3,0)):\\n            self.assertEqual(multiply(a,b), a*b)\\n")
run("python3", "-m", "unittest", "discover", "-s", "tests", "-v")
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
