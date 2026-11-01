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
COMMAND = "./scripts/verify"


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
        self.evidence.verify_pr([pr], "factory/task-test", "b" * 40, "a" * 40, "main")
        for changes in (
            {"state": "CLOSED"},
            {"mergedAt": "now"},
            {"headRefOid": "wrong"},
            {"baseRefName": "other"},
            {"baseRefOid": "wrong"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.evidence.verify_pr(
                    [{**pr, **changes}], "factory/task-test", "b" * 40, "a" * 40, "main"
                )
        with self.assertRaises(ValueError):
            self.evidence.verify_pr(
                [pr, pr], "factory/task-test", "b" * 40, "a" * 40, "main"
            )


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(
            importlib.util.find_spec("worker.task"), "Replacement worker missing"
        )
        from worker import task

        self.task = task
        config_patch = patch.object(
            task,
            "CODEX_CONFIG",
            task.CODEX_CONFIG
            if task.CODEX_CONFIG.is_file()
            else Path(__file__).resolve().parents[1] / "image/managed_config.toml",
        )
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.origin = self.root / "origin"
        (self.origin / "tests").mkdir(parents=True)
        (self.origin / "greeting.txt").write_text("hello\n")
        (self.origin / "scripts").mkdir()
        (self.origin / "scripts/setup").write_text(
            "#!/bin/sh\nprintf 'setup-passed\\n'\n"
        )
        (self.origin / "scripts/verify").write_text(
            "#!/bin/sh\nset -e\ntest -s greeting.txt\nprintf 'verify-passed\\n'\n"
        )
        for script in ("setup", "verify"):
            (self.origin / "scripts" / script).chmod(0o755)
        self.env = {
            **os.environ,
            "WORKSPACE": str(self.root / "workspace"),
            "EXPECTED_RUNTIME_ROLE": "software-factory-poc-runtime",
            "GITHUB_SECRET_ARN": "secret-arn",
            "AUTHORIZED_REPOSITORY": "example/authorized-project",
            "PR_TARGET_BRANCH": "release",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        }
        self.git("init", "-b", "release")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        self.git("add", ".")
        self.git("commit", "-m", "baseline")
        self.sha = self.git("rev-parse", "HEAD").strip()
        self.job = {
            "repository": "example/authorized-project",
            "baseSha": self.sha,
            "task": "Update the greeting",
        }
        self.prs = []
        self.agent_calls = 0
        self.clone_url = "https://github.com/example/authorized-project.git"
        environment_patch = patch.dict(os.environ, self.env)
        environment_patch.start()
        self.addCleanup(environment_patch.stop)
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
        self.assertEqual(
            args,
            [
                "codex",
                "exec",
                "--json",
                "--ephemeral",
                "-C",
                str(cwd),
                "--add-dir",
                str(cwd / ".git"),
                "-",
            ],
        )
        self.assertIn("gh pr create", prompt)
        self.assertNotIn(env["GH_TOKEN"], prompt)
        self.assertIn("same invocation", prompt)
        self.assertIn("./scripts/verify", prompt)
        self.assertIn("targeting release", prompt)
        self.assertNotIn("calculator", prompt)
        self.assertNotIn("multiply", prompt)
        (cwd / "greeting.txt").write_text("hello world\n")
        self.external(["./scripts/verify"], cwd, env)
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
                "baseRefName": "release",
                "headRefName": branch,
                "headRefOid": head,
                "baseRefOid": self.sha,
            }
        ]
        # Chronology is deliberately not supplied: generic completion checks Git/PR and scripts.
        return [event("printf factory-shell-ok", output="factory-shell-ok")]

    # Missing image policy must fail before invoking the agent rather than loading unsafe defaults.
    def test_missing_factory_configuration_stops_before_agent(self):
        with (
            patch.object(self.task, "CODEX_CONFIG", self.root / "missing.toml"),
            patch.object(self.task, "capture", return_value="codex-cli 0.166.0"),
            patch.object(self.task, "run_agent") as agent,
            self.assertRaisesRegex(ValueError, "factory Codex configuration"),
        ):
            self.task.invoke_agent(self.job, self.origin, "branch", self.env, [])
        agent.assert_not_called()

    # Exercises real local checkout, tests, and Git with simulated external services,
    # checking one agent invocation, one PR, and secret-free passing-test logs.
    def test_real_checkout_tests_git_and_simulated_agent_publication(self):
        stages = (
            "prepare_environment",
            "prepare_repository",
            "invoke_agent",
            "verify_completion",
        )
        for name in stages:
            self.assertTrue(hasattr(self.task, name), "Worker stage missing: " + name)
        calls = []
        results = {}

        def observe(name, function):
            def run(*args):
                calls.append((name, args))
                result = function(*args)
                results[name] = result
                return result

            return run

        with contextlib.ExitStack() as stack:
            for name in (*stages, "credentials"):
                stack.enter_context(
                    patch.object(
                        self.task,
                        name,
                        side_effect=observe(name, getattr(self.task, name)),
                    )
                )
            self.run_staged_job(calls, results)

    def run_staged_job(self, calls, results):
        with (
            patch.dict(os.environ, self.env),
            patch.object(self.task, "capture", side_effect=self.external),
            patch.object(self.task, "run_agent", side_effect=self.agent),
            contextlib.redirect_stdout(io.StringIO()) as logs,
        ):
            self.task.execute(self.job)
        self.assertEqual(
            [name for name, _ in calls],
            [
                "prepare_environment",
                "credentials",
                "prepare_repository",
                "invoke_agent",
                "verify_completion",
                "credentials",
            ],
        )
        env, initial_access = results["prepare_environment"]
        repository, branch = results["prepare_repository"]
        secrets = calls[0][1][0]
        self.assertEqual(initial_access, "ASIA" + "X" * 16)
        self.assertEqual(calls[2][1], (self.job, env, secrets))
        self.assertEqual(calls[3][1], (self.job, repository, branch, env, secrets))
        self.assertEqual(
            calls[4][1],
            (
                self.job,
                repository,
                branch,
                env,
                secrets,
            ),
        )
        self.assertEqual(calls[5][1], (env, secrets))
        self.assertEqual(self.agent_calls, 1)
        self.assertEqual(len(self.prs), 1)
        for stubber in self.stubbers.values():
            stubber.assert_no_pending_responses()
        self.assertIn("exit code 0", logs.getvalue())
        for secret in ("github_pat_synthetic_token", "secret-access", "session-token"):
            self.assertNotIn(secret, logs.getvalue())

    # Stops at each failed stage or final credential check without leaking its exception.
    def test_stage_failures_stop_coordination_and_final_success(self):
        stages = (
            "prepare_environment",
            "prepare_repository",
            "invoke_agent",
            "verify_completion",
            "credentials",
        )
        for name in stages:
            self.assertTrue(hasattr(self.task, name), "Worker stage missing: " + name)
        for failed in stages:
            calls = []

            def stage(name, calls=calls, failed=failed):
                def run(*args):
                    calls.append(name)
                    if name == failed:
                        raise ValueError("secret-value")
                    return {
                        "prepare_environment": ({}, "initial-access"),
                        "prepare_repository": (self.root, "branch"),
                        "invoke_agent": [],
                    }.get(name)

                return run

            with self.subTest(failed=failed), contextlib.ExitStack() as stack:
                for name in stages:
                    stack.enter_context(
                        patch.object(self.task, name, side_effect=stage(name))
                    )
                stack.enter_context(
                    patch("sys.stdin", io.StringIO(json.dumps(self.job)))
                )
                logs = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                self.assertEqual(self.task.main(), 1)
                self.assertEqual(calls, list(stages[: stages.index(failed) + 1]))
                self.assertNotIn("secret-value", logs.getvalue())
                self.assertNotIn("Verified repository script", logs.getvalue())

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

    # Rejects an agent result without the initial sandbox probe; process exit alone
    # does not satisfy probe, independent script, or Git/PR checks.
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
            (["git", "status"], " M greeting.txt"),
            (["git", "diff"], ""),
            (["git", "branch"], "release\n"),
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
            + ')); print(json.dumps({"type":"item.completed","item":{"type":"command_execution","aggregated_output":"session-token"}}))'
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
            'import sys; sys.stdin.read(); print(\'{"type":"turn.failed","error":"session-token"}\')',
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
        with (
            contextlib.redirect_stdout(io.StringIO()) as logs,
            self.assertRaises(ValueError),
        ):
            self.task.run_agent(
                [os.sys.executable, "-c", code], "prompt", self.root, self.env, []
            )
        self.assertNotIn("rotated-private-value", logs.getvalue())
        self.assertNotIn("rotated-session-value", logs.getvalue())

    def test_scripts_fail_before_agent_for_missing_nonexecutable_and_nonzero(self):
        for index, (script, failure) in enumerate(
            (script, failure)
            for script in ("setup", "verify")
            for failure in ("missing", "nonexecutable", "nonzero")
        ):
            self.sdk()
            path = self.origin / "scripts" / script
            original = path.read_text()
            if failure == "missing":
                path.unlink()
            elif failure == "nonexecutable":
                path.chmod(0o644)
            else:
                path.write_text(
                    "#!/bin/sh\necho github_pat_synthetic_token >&2\nexit 7\n"
                )
            self.git("add", ".")
            self.git("commit", "-m", "script failure")
            sha = self.git("rev-parse", "HEAD").strip()
            env = {**self.env, "WORKSPACE": str(self.root / ("script-" + str(index)))}
            with (
                self.subTest(script=script, failure=failure),
                patch.dict(os.environ, env),
                patch.object(self.task, "capture", side_effect=self.external),
                patch.object(self.task, "run_agent", side_effect=self.agent),
                contextlib.redirect_stdout(io.StringIO()) as logs,
                self.assertRaises(
                    (ValueError, subprocess.CalledProcessError)
                ) as failure_result,
            ):
                self.task.execute({**self.job, "baseSha": sha})
            if failure == "nonzero":
                self.assertEqual(failure_result.exception.returncode, 7)
            else:
                self.assertIsInstance(failure_result.exception, ValueError)
                self.assertIn("./scripts/" + script, str(failure_result.exception))
                self.assertIn(
                    "Missing or non-executable repository script: ./scripts/" + script,
                    logs.getvalue(),
                )
            self.assertEqual(self.agent_calls, 0)
            self.assertEqual(self.prs, [])
            self.assertNotIn("github_pat_synthetic_token", logs.getvalue())
            path.write_text(original)
            path.chmod(0o755)

    def test_final_verification_failure_rejects_published_work(self):
        def agent(*args):
            result = self.agent(*args)
            repository = args[2]
            (repository / "greeting.txt").write_text("")
            return result

        with (
            patch.object(self.task, "capture", side_effect=self.external),
            patch.object(self.task, "run_agent", side_effect=agent),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            self.task.execute(self.job)
        self.assertEqual(self.agent_calls, 1)
        self.assertEqual(len(self.prs), 1)

    def test_final_script_unavailable_rejects_published_work(self):
        for index, failure in enumerate(("missing", "nonexecutable")):
            self.sdk()

            def agent(*args, failure=failure):
                result = self.agent(*args)
                script = args[2] / "scripts/verify"
                if failure == "missing":
                    script.unlink()
                else:
                    script.chmod(0o644)
                return result

            with (
                self.subTest(failure=failure),
                patch.dict(
                    os.environ, {"WORKSPACE": str(self.root / ("final-" + str(index)))}
                ),
                patch.object(self.task, "capture", side_effect=self.external),
                patch.object(self.task, "run_agent", side_effect=agent),
                contextlib.redirect_stdout(io.StringIO()) as logs,
                self.assertRaisesRegex(ValueError, "Missing or non-executable"),
            ):
                self.task.execute(self.job)
            self.assertIn(
                "Missing or non-executable repository script: ./scripts/verify",
                logs.getvalue(),
            )
            self.assertEqual(len(self.prs), 1)

    def test_sandbox_probe_failure_stops_before_completion(self):
        for events in (
            [],
            [event("printf factory-shell-ok", 1, "factory-shell-ok")],
            [event("echo other", output="other")],
        ):
            with (
                self.subTest(events=events),
                patch.object(self.task, "capture", side_effect=self.external),
                patch.object(self.task, "run_agent", return_value=events),
                self.assertRaisesRegex(ValueError, "Initial sandbox shell"),
            ):
                self.task.invoke_agent(self.job, self.origin, "branch", self.env, [])

    def test_invalid_target_branch_stops_before_scripts_or_agent(self):
        with (
            patch.dict(os.environ, {"PR_TARGET_BRANCH": "-invalid"}),
            patch.object(self.task, "capture", side_effect=self.external),
            patch.object(self.task, "run_agent") as agent,
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            self.task.execute(self.job)
        agent.assert_not_called()

    def test_unauthorized_job_stops_before_credentials(self):
        with (
            patch.object(self.task, "prepare_environment") as prepare,
            self.assertRaises(ValueError),
        ):
            self.task.execute({**self.job, "repository": "example/other"})
        prepare.assert_not_called()

    def test_script_order_and_independent_verification(self):
        calls = []

        def capture(args, cwd, env, secrets=None):
            calls.append(args[0])
            return self.external(args, cwd, env, secrets)

        def agent(*args):
            calls.append("agent")
            return self.agent(*args)

        with (
            patch.object(self.task, "capture", side_effect=capture),
            patch.object(self.task, "run_agent", side_effect=agent),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.task.execute(self.job)
        self.assertEqual(
            [
                call
                for call in calls
                if call in ("./scripts/setup", "./scripts/verify", "agent")
            ],
            ["./scripts/setup", "./scripts/verify", "agent", "./scripts/verify"],
        )

    # Legacy acceptance tests remain dedicated to the obsolete module until US-12.
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

        (self.origin / "calculator.py").write_text(BASE)
        (self.origin / "tests/test_calculator.py").write_text(TESTS)
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
