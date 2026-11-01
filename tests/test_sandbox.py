import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(
    os.environ.get("FACTORY_SANDBOX_TEST") == "1",
    "Real Linux sandbox is checked by make preflight",
)
class SandboxTests(unittest.TestCase):
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
