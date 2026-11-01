import contextlib
import importlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import boto3
from botocore.stub import Stubber

BASE = "def add(a, b):\n    return a + b\n"
TESTS = """import unittest
from calculator import add

class CalculatorTests(unittest.TestCase):
    # Preserves the demonstration's addition baseline while multiplication is added.
    def test_add(self):
        self.assertEqual(add(2, 3), 5)
"""
COMMAND = "python3 -m unittest discover -s tests -v"


def event(command, code=0, output=""):
    return {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "command": command,
            "exit_code": code,
            "aggregated_output": output,
        },
    }


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(
            importlib.util.find_spec("worker.evidence"), "Completion verifier missing"
        )
        from worker import evidence

        self.evidence = evidence
        self.events = [
            event("printf gate2-shell-ok", output="gate2-shell-ok"),
            event(COMMAND),
            event("git commit -m task"),
            event("git push -u origin factory/task-test"),
            event("gh pr create --base main"),
        ]

    # Requires successful shell/test evidence before publication and rejects agent
    # errors, preventing a process exit or incomplete command history from proving success.
    def test_passing_tests_precede_separate_publication_commands(self):
        self.evidence.verify_events(self.events)
        for events in (
            [],
            self.events[:2],
            self.events[1:],
            [*self.events[:1], event(COMMAND, 1), *self.events[2:]],
            [self.events[0], *self.events[2:], self.events[1]],
            [event("printf gate2-shell-ok", 101), *self.events[1:]],
            [*self.events, {"type": "error", "message": "inference failed"}],
        ):
            with self.subTest(events=events), self.assertRaises(ValueError):
                self.evidence.verify_events(events)
        wrapped = [
            event(
                "/bin/bash -lc '" + item["item"]["command"] + "'",
                output=item["item"]["aggregated_output"],
            )
            for item in self.events
        ]
        self.evidence.verify_events(wrapped)

    # Allows remote checks to resolve uncertain publication while rejecting edits
    # after testing/commit, protecting duplicate avoidance and the tested result.
    def test_publication_timeouts_accept_agent_checks_of_existing_remote_work(self):
        recovered = [
            self.events[0],
            self.events[1],
            self.events[2],
            event("git push origin branch", 1),
            event("git ls-remote --heads origin branch"),
            event("gh pr create --base main", 1),
            event("gh pr view --json number"),
        ]
        self.evidence.verify_events(recovered)
        with self.assertRaises(ValueError):
            self.evidence.verify_events(
                [*self.events[:3], event("python3 edit.py"), *self.events[2:]]
            )
        with self.assertRaises(ValueError):
            self.evidence.verify_events(
                [*self.events[:2], event("python3 edit.py"), *self.events[2:]]
            )

    # Requires a meaningful discoverable test addition without changing baseline
    # assertions, preventing weakened or unchanged tests from manufacturing completion.
    def test_preserved_baseline_tests_and_meaningful_new_test(self):
        updated = (
            TESTS.replace(
                "from calculator import add", "from calculator import add, multiply"
            )
            + "\n    # Checks multiplication so source evidence includes a meaningful new assertion.\n    def test_multiply(self):\n        self.assertEqual(multiply(2, 3), 6)\n"
        )
        self.evidence.verify_source(
            {"calculator.py": BASE, "tests/test_calculator.py": TESTS},
            {
                "calculator.py": BASE + "\ndef multiply(a, b):\n    return a * b\n",
                "tests/test_calculator.py": updated,
            },
        )
        for changed in (
            updated.replace("add(2, 3), 5", "add(2, 3), 6"),
            TESTS,
            updated.replace("unittest.TestCase", "object"),
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.evidence.verify_source(
                    {"calculator.py": BASE, "tests/test_calculator.py": TESTS},
                    {
                        "calculator.py": BASE
                        + "\ndef multiply(a, b):\n    return a * b\n",
                        "tests/test_calculator.py": changed,
                    },
                )

    # Accepts a new test in a different class despite a shared method name,
    # avoiding false rejection of valid additions that preserve baseline tests.
    def test_new_test_class_can_reuse_baseline_method_names(self):
        updated = (
            TESTS.replace(
                "from calculator import add", "from calculator import add, multiply"
            )
            + "\nclass MultiplicationTests(unittest.TestCase):\n    # Checks multiplication in a separate class to exercise same-name test acceptance.\n    def test_add(self):\n        self.assertEqual(multiply(2, 3), 6)\n"
        )
        self.evidence.verify_source(
            {"calculator.py": BASE, "tests/test_calculator.py": TESTS},
            {
                "calculator.py": BASE + "\ndef multiply(a, b):\n    return a * b\n",
                "tests/test_calculator.py": updated,
            },
        )

    # Requires exactly one open, unmerged PR matching the verified task commit and
    # pinned main baseline, keeping published work available for human review.
    def test_pr_must_match_branch_commit_baseline_and_be_open(self):
        pr = {
            "number": 2,
            "state": "OPEN",
            "mergedAt": None,
            "baseRefName": "main",
            "headRefName": "factory/task-test",
            "headRefOid": "b" * 40,
            "baseRefOid": "a" * 40,
        }
        self.evidence.verify_pr([pr], "factory/task-test", "b" * 40, "a" * 40)
        for changes in (
            {"state": "CLOSED"},
            {"mergedAt": "now"},
            {"headRefOid": "wrong"},
            {"baseRefName": "other"},
            {"baseRefOid": "wrong"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.evidence.verify_pr(
                    [{**pr, **changes}], "factory/task-test", "b" * 40, "a" * 40
                )
        with self.assertRaises(ValueError):
            self.evidence.verify_pr([pr, pr], "factory/task-test", "b" * 40, "a" * 40)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(
            importlib.util.find_spec("worker.task"), "Replacement worker missing"
        )
        from worker import task

        self.task = task
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.origin = self.root / "origin"
        (self.origin / "tests").mkdir(parents=True)
        (self.origin / "calculator.py").write_text(BASE)
        (self.origin / "tests/test_calculator.py").write_text(TESTS)
        self.env = {
            **os.environ,
            "WORKSPACE": str(self.root / "workspace"),
            "EXPECTED_RUNTIME_ROLE": "software-factory-poc-runtime",
            "GITHUB_SECRET_ARN": "secret-arn",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        }
        self.git("init", "-b", "main")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        self.git("add", ".")
        self.git("commit", "-m", "baseline")
        self.sha = self.git("rev-parse", "HEAD").strip()
        self.job = {
            "repository": "TeroTS/factory-smoke-test",
            "baseSha": self.sha,
            "task": "add multiply",
        }
        self.prs = []
        self.agent_calls = 0
        self.clone_url = "https://github.com/TeroTS/factory-smoke-test.git"
        self.sdk()
        self.client_patch = patch("boto3.client", side_effect=self.sdk_client)
        self.client_mock = self.client_patch.start()
        self.addCleanup(self.client_patch.stop)

    def sdk(self, identity=None, secret=None, identity_error=None, secret_error=None):
        self.clients = {}
        self.stubbers = {}
        for service in ("sts", "secretsmanager"):
            client = boto3.Session().client(
                service,
                region_name="us-east-1",
                aws_access_key_id="synthetic-access",
                aws_secret_access_key="synthetic-secret",
                aws_session_token="synthetic-session",
            )
            stubber = Stubber(client)
            if service == "sts":
                response = (
                    identity
                    if identity is not None
                    else {
                        "Account": "172318459814",
                        "Arn": "arn:aws:sts::172318459814:assumed-role/software-factory-poc-runtime/session",
                        "UserId": "synthetic-user",
                    }
                )
                for _ in range(2):
                    if identity_error:
                        stubber.add_client_error(
                            "get_caller_identity",
                            service_message=identity_error,
                            expected_params={},
                        )
                    else:
                        stubber.add_response("get_caller_identity", response, {})
            elif secret_error:
                stubber.add_client_error(
                    "get_secret_value",
                    service_message=secret_error,
                    expected_params={"SecretId": "secret-arn"},
                )
            else:
                # Permit a malformed empty service response to exercise our trust boundary.
                with (
                    patch("botocore.stub.validate_parameters")
                    if secret == {"SecretString": ""}
                    else contextlib.nullcontext()
                ):
                    stubber.add_response(
                        "get_secret_value",
                        secret
                        if secret is not None
                        else {"SecretString": "github_pat_synthetic_token"},
                        {"SecretId": "secret-arn"},
                    )
            stubber.activate()
            self.addCleanup(stubber.deactivate)
            self.addCleanup(client.close)
            self.clients[service] = client
            self.stubbers[service] = stubber

    def sdk_client(self, service, **kwargs):
        self.assertEqual(kwargs["region_name"], "us-east-1")
        config = kwargs["config"]
        self.assertEqual(config.connect_timeout, 5)
        self.assertEqual(config.read_timeout, 10)
        self.assertEqual(config.retries, {"mode": "standard", "total_max_attempts": 1})
        return self.clients[service]

    def git(self, *args):
        return subprocess.check_output(
            ["git", *args],
            cwd=self.origin,
            env=self.env,
            text=True,
            stderr=subprocess.DEVNULL,
        )

    def external(self, args, cwd, env, secrets=None):
        if args[:3] == ["aws", "configure", "export-credentials"]:
            return json.dumps(
                {
                    "AccessKeyId": "ASIA" + "X" * 16,
                    "SecretAccessKey": "secret-access",
                    "SessionToken": "session-token",
                    "Expiration": "2099-01-01T00:00:00Z",
                }
            )
        self.assertNotEqual(args[0], "aws", "Only credential export may use AWS CLI")
        if args[:2] == ["gh", "auth"]:
            return ""
        if args[:3] == ["gh", "pr", "list"]:
            return json.dumps(self.prs)
        if args[:2] == ["codex", "--version"]:
            return "codex-cli 0.166.0"
        if args[:2] == ["git", "clone"]:
            args = [*args]
            args[args.index(self.clone_url)] = str(self.origin)
        result = subprocess.run(
            args, cwd=cwd, env=env, text=True, capture_output=True, check=False
        )
        if result.returncode:
            raise subprocess.CalledProcessError(
                result.returncode, args, result.stdout, result.stderr
            )
        return result.stdout

    def agent(self, args, prompt, cwd, env, secrets):
        self.agent_calls += 1
        self.assertIn('model_provider="amazon-bedrock-runtime"', args)
        self.assertIn('shell_environment_policy.inherit="all"', args)
        self.assertIn("shell_environment_policy.ignore_default_excludes=true", args)
        self.assertIn("sandbox_workspace_write.network_access=true", args)
        self.assertIn("workspace-write", args)
        self.assertIn("gh pr create", prompt)
        self.assertNotIn(env["GH_TOKEN"], prompt)
        self.assertIn("same invocation", prompt)
        (cwd / "calculator.py").write_text(
            BASE + "\ndef multiply(a, b):\n    return a * b\n"
        )
        (cwd / "tests/test_calculator.py").write_text(
            TESTS.replace(
                "from calculator import add", "from calculator import add, multiply"
            )
            + "\n    # Covers positive, negative, and zero products to satisfy demonstration acceptance.\n    def test_multiply(self):\n        self.assertEqual(multiply(2, 3), 6)\n        self.assertEqual(multiply(-2, 3), -6)\n        self.assertEqual(multiply(-2, -3), 6)\n        self.assertEqual(multiply(0, 3), 0)\n        self.assertEqual(multiply(3, 0), 0)\n"
        )
        for cmd in (
            ["git", "add", "."],
            ["git", "commit", "-m", "task"],
            ["git", "push", "origin", "HEAD"],
        ):
            self.external(cmd, cwd, env)
        head = self.external(["git", "rev-parse", "HEAD"], cwd, env).strip()
        branch = self.external(["git", "branch", "--show-current"], cwd, env).strip()
        self.prs = [
            {
                "number": 2,
                "state": "OPEN",
                "mergedAt": None,
                "baseRefName": "main",
                "headRefName": branch,
                "headRefOid": head,
                "baseRefOid": self.sha,
            }
        ]
        return [
            event("printf gate2-shell-ok", output="gate2-shell-ok"),
            event(COMMAND),
            event("git commit -m task"),
            event("git push origin " + branch),
            event("gh pr create --base main"),
        ]

    # Exercises real local checkout, tests, and Git with simulated external services,
    # checking one agent invocation, one PR, and secret-free passing-test logs.
    def test_real_checkout_tests_git_and_simulated_agent_publication(self):
        with (
            patch.dict(os.environ, self.env),
            patch.object(self.task, "capture", side_effect=self.external),
            patch.object(self.task, "run_agent", side_effect=self.agent),
            contextlib.redirect_stdout(io.StringIO()) as logs,
        ):
            self.task.execute(self.job)
        self.assertEqual(self.agent_calls, 1)
        self.assertEqual(len(self.prs), 1)
        for stubber in self.stubbers.values():
            stubber.assert_no_pending_responses()
        self.assertIn("exit code 0", logs.getvalue())
        for secret in ("github_pat_synthetic_token", "secret-access", "session-token"):
            self.assertNotIn(secret, logs.getvalue())

    # Fails an unavailable pinned baseline before any agent call or PR, protecting
    # the controlled-failure demonstration from unintended model use or publication.
    def test_missing_checkout_stops_before_agent(self):
        with (
            patch.dict(os.environ, self.env),
            patch.object(self.task, "capture", side_effect=self.external),
            patch.object(self.task, "run_agent", side_effect=self.agent),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            self.task.execute({**self.job, "baseSha": "0" * 40})
        self.assertEqual(self.agent_calls, 0)
        self.assertEqual(self.prs, [])

    # Rejects an agent result with no command history because successful execution
    # alone cannot prove testing and publication under the completion rules.
    def test_agent_exit_without_command_evidence_is_not_success(self):
        with (
            patch.dict(os.environ, self.env),
            patch.object(self.task, "capture", side_effect=self.external),
            patch.object(self.task, "run_agent", return_value=[]),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(ValueError),
        ):
            self.task.execute(self.job)

    # Rejects administrator identity before secret retrieval or agent work,
    # enforcing execution-role access rather than borrowed operator privileges.
    def test_identity_guard_refuses_administrator_before_secret_or_model(self):
        for account, arn in (
            (
                "172318459814",
                "arn:aws:sts::172318459814:assumed-role/AWSAdministratorAccess/session",
            ),
            (
                "000000000000",
                "arn:aws:sts::000000000000:assumed-role/software-factory-poc-runtime/session",
            ),
            (
                "000000000000",
                "arn:aws:sts::172318459814:assumed-role/software-factory-poc-runtime/session",
            ),
        ):
            self.sdk(identity={"Account": account, "Arn": arn, "UserId": "synthetic"})
            with (
                self.subTest(account=account, arn=arn),
                patch.dict(os.environ, self.env),
                patch.object(self.task, "capture", side_effect=self.external),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaises(ValueError),
            ):
                self.task.execute(self.job)
            self.assertFalse((self.root / "workspace").exists())
            self.assertEqual(self.agent_calls, 0)
        self.assertNotIn(
            "secretsmanager", [call.args[0] for call in self.client_mock.call_args_list]
        )

    # Stops setup before SDK access for missing, expired, or malformed CLI credentials.
    def test_missing_or_expired_cli_credentials_are_rejected(self):
        for override in (
            {"AccessKeyId": ""},
            {"Expiration": "2000-01-01T00:00:00Z"},
            {"Expiration": "invalid"},
        ):

            def invalid(args, cwd, env, secrets=None, override=override):
                output = self.external(args, cwd, env)
                if args[:3] == ["aws", "configure", "export-credentials"]:
                    output = json.dumps({**json.loads(output), **override})
                return output

            with (
                self.subTest(override=override),
                patch.dict(os.environ, self.env),
                patch.object(self.task, "capture", side_effect=invalid),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaises((ValueError, TypeError)),
            ):
                self.task.execute(self.job)
        self.assertEqual(self.agent_calls, 0)
        self.client_mock.assert_not_called()

    def test_invalid_sdk_secret_stops_before_repository_or_agent(self):
        for secret in (
            {},
            {"SecretBinary": b"synthetic"},
            {"SecretString": ""},
            {"SecretString": "  "},
        ):
            self.sdk(secret=secret)
            with (
                self.subTest(secret=secret),
                patch.dict(os.environ, self.env),
                patch.object(self.task, "capture", side_effect=self.external),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaises((ValueError, TypeError)),
            ):
                self.task.execute(self.job)
            self.assertFalse((self.root / "workspace").exists())
        self.assertEqual(self.agent_calls, 0)

    def test_sdk_failures_are_sanitized_and_stop_before_repository_or_model(self):
        for stage in ("identity_error", "secret_error"):
            self.sdk(
                **{stage: "github_pat_synthetic_token secret-access session-token"}
            )
            with (
                self.subTest(stage=stage),
                patch.dict(os.environ, self.env),
                patch.object(self.task, "capture", side_effect=self.external),
                patch("sys.stdin", io.StringIO(json.dumps(self.job))),
                contextlib.redirect_stdout(io.StringIO()) as logs,
            ):
                self.assertEqual(self.task.main(), 1)
            self.assertIn("Worker failed: ClientError", logs.getvalue())
            for value in (
                "github_pat_synthetic_token",
                "secret-access",
                "session-token",
            ):
                self.assertNotIn(value, logs.getvalue())
            self.assertFalse((self.root / "workspace").exists())
        self.assertEqual(self.agent_calls, 0)
        self.assertEqual(self.prs, [])

    # Rejects publication claims contradicted by remote/local Git or PR checks,
    # enforcing meaningful scoped changes on a task branch rather than trusting the agent.
    def test_contradictory_independent_git_and_pr_evidence_rejects_success(self):
        replies = (
            (["git", "ls-remote"], "wrong refs/heads/task"),
            (["git", "status"], " M calculator.py"),
            (["git", "diff"], "calculator.py\ntests/test_calculator.py\ndeploy.yml\n"),
            (["git", "branch"], "main\n"),
            (["git", "rev-parse"], self.sha + "\n"),
            (["gh", "pr"], "[]"),
        )
        for index, (prefix, reply) in enumerate(replies):
            self.sdk()

            def contradict(
                args, cwd, env, secrets=None, index=index, prefix=prefix, reply=reply
            ):
                if self.agent_calls > index and args[:2] == prefix:
                    return reply
                return self.external(args, cwd, env)

            env = {**self.env, "WORKSPACE": str(self.root / ("failure-" + str(index)))}
            with (
                self.subTest(prefix=prefix),
                patch.dict(os.environ, env),
                patch.object(self.task, "capture", side_effect=contradict),
                patch.object(self.task, "run_agent", side_effect=self.agent),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaises(ValueError),
            ):
                self.task.execute(self.job)

    # Removes both supplied secrets and newly seen AWS credential fields,
    # protecting logs when execution-role credentials rotate during a job.
    def test_redactor_handles_known_and_rotated_credentials(self):
        text = 'github_pat_synthetic_token {"SecretAccessKey":"rotated-secret","SessionToken":"rotated-session","AccessKeyId":"ASIAXXXXXXXXXXXXXXXX"}'
        cleaned = self.task.redact(text, ["github_pat_synthetic_token"])
        for value in (
            "github_pat_synthetic_token",
            "rotated-secret",
            "rotated-session",
            "ASIAXXXXXXXXXXXXXXXX",
        ):
            self.assertNotIn(value, cleaned)

    # Keeps successful command stderr visible while redacting secrets, preserving
    # test diagnostics without violating the no-credentials-in-logs rule.
    def test_successful_test_stderr_is_retained_without_secrets(self):
        env = {**self.env, "GH_TOKEN": "test-private-value"}
        code = 'import sys; print("stdout-result"); print("test-output test-private-value", file=sys.stderr)'
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = self.task.logged(
                [os.sys.executable, "-c", code], self.root, env, ["test-private-value"]
            )
        self.assertEqual(result.strip(), "stdout-result")
        self.assertIn("test-output [REDACTED]\n", output.getvalue())
        self.assertNotIn("test-private-value", output.getvalue())

    # Retains streamed events with redacted logs but rejects invalid JSON or nonzero
    # exit, preventing broken agent execution from supplying usable completion evidence.
    def test_real_agent_stream_logs_redacted_output_and_requires_zero_exit(self):
        events = [event("printf gate2-shell-ok", output="gate2-shell-ok")]
        code = (
            "import json,sys; sys.stdin.read(); print(json.dumps("
            + repr(events[0])
            + ')); print("{\\"type\\":\\"error\\",\\"message\\":\\"session-token\\"}")'
        )
        with contextlib.redirect_stdout(io.StringIO()) as logs:
            result = self.task.run_agent(
                [os.sys.executable, "-c", code],
                "prompt",
                self.root,
                self.env,
                ["session-token"],
            )
        self.assertEqual(len(result), 2)
        self.assertNotIn("session-token", logs.getvalue())
        for code in (
            "import sys; sys.stdin.read(); sys.exit(1)",
            'import sys; sys.stdin.read(); print("bad-json")',
        ):
            with (
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaises(ValueError),
            ):
                self.task.run_agent(
                    [os.sys.executable, "-c", code], "prompt", self.root, self.env, []
                )

    # Redacts newly seen AWS secrets inside JSON-encoded error messages,
    # protecting logs even when credential details arrive nested in agent output.
    def test_rotated_credentials_in_nested_agent_error_are_redacted(self):
        error = {
            "type": "error",
            "message": json.dumps(
                {
                    "SecretAccessKey": "rotated-private-value",
                    "SessionToken": "rotated-session-value",
                }
            ),
        }
        code = "import sys; sys.stdin.read(); print(" + repr(json.dumps(error)) + ")"
        with contextlib.redirect_stdout(io.StringIO()) as logs:
            self.task.run_agent(
                [os.sys.executable, "-c", code], "prompt", self.root, self.env, []
            )
        self.assertNotIn("rotated-private-value", logs.getvalue())
        self.assertNotIn("rotated-session-value", logs.getvalue())

    # Rejects correct multiplication backed only by a positive-operand test,
    # enforcing the required negative and zero coverage in the submitted tests.
    def test_acceptance_requires_committed_tests_for_all_operand_categories(self):
        from image.acceptance import verify

        (self.origin / "calculator.py").write_text(
            BASE + "\ndef multiply(a, b):\n    return a * b\n"
        )
        (self.origin / "tests/test_calculator.py").write_text(
            TESTS
            + "\n    # Checks only a positive product, supplying deliberately incomplete acceptance coverage.\n    def test_multiply(self):\n        from calculator import multiply\n        self.assertEqual(multiply(2, 3), 6)\n"
        )
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            verify(self.origin)

    # Rejects a missing multiply function or one that loses negative results,
    # independently checking required behavior rather than trusting submitted tests.
    def test_independent_acceptance_rejects_wrong_multiplication(self):
        from image.acceptance import verify

        with self.assertRaises(AttributeError):
            verify(self.origin)
        (self.origin / "calculator.py").write_text(
            BASE + "\ndef multiply(a, b):\n    return abs(a * b)\n"
        )
        with self.assertRaises(ValueError):
            verify(self.origin)

    # Converts execution failure to a nonzero exit without printing its secret-bearing
    # exception, allowing lifecycle error reporting without credential disclosure.
    def test_main_failure_logs_no_secret_and_exits_nonzero(self):
        with (
            patch.object(self.task, "execute", side_effect=ValueError("secret-value")),
            patch("sys.stdin", io.StringIO(json.dumps(self.job))),
            contextlib.redirect_stdout(io.StringIO()) as logs,
        ):
            self.assertEqual(self.task.main(), 1)
        self.assertNotIn("secret-value", logs.getvalue())


if __name__ == "__main__":
    unittest.main()
