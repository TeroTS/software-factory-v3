import contextlib
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


class PublicationTests(unittest.TestCase):
    def setUp(self):
        from src.worker import task

        self.task = task

    # Requires exactly one open, unmerged PR matching the task commit and configured target.
    def test_pr_must_match_branch_commit_target_and_be_open(self):
        pr = {
            "number": 2,
            "state": "OPEN",
            "mergedAt": None,
            "baseRefName": "release",
            "headRefName": "factory/task-test",
            "headRefOid": "b" * 40,
        }
        self.task.verify_pr([pr], "factory/task-test", "b" * 40, "release")
        for changes in (
            {"state": "CLOSED"},
            {"mergedAt": "now"},
            {"headRefName": "wrong"},
            {"headRefOid": "wrong"},
            {"baseRefName": "main"},
            {"number": 0},
            {"number": -1},
            {"number": "2"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.task.verify_pr(
                    [{**pr, **changes}],
                    "factory/task-test",
                    "b" * 40,
                    "release",
                )
        for prs in ([], [pr, pr], [{}]):
            with self.subTest(prs=prs), self.assertRaises(ValueError):
                self.task.verify_pr(prs, "factory/task-test", "b" * 40, "release")


class WorkerFixture(unittest.TestCase):
    def setUp(self, origin=None, workspace=None):
        from src.worker import task

        self.task = task
        config_patch = patch.object(
            task,
            "CODEX_CONFIG",
            task.CODEX_CONFIG
            if task.CODEX_CONFIG.is_file()
            else Path(__file__).resolve().parents[2] / "image/managed_config.toml",
        )
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.origin = Path(origin) if origin else self.root / "origin"
        if origin is None:
            (self.origin / "scripts").mkdir(parents=True)
            (self.origin / "greeting.txt").write_text("hello\n")
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
            "WORKSPACE": str(workspace or self.root / "workspace"),
            "EXPECTED_RUNTIME_ROLE": "software-factory-poc-runtime",
            "GITHUB_SECRET_ARN": "secret-arn",
            "AUTHORIZED_REPOSITORY": "example/authorized-project",
            "PR_TARGET_BRANCH": "release",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        }
        if origin is None:
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
            self.assertNotIn("baseRefOid", args[args.index("--json") + 1].split(","))
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
            }
        ]


class WorkerTests(WorkerFixture):
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
            contextlib.redirect_stdout(io.StringIO()) as logs,
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

    # Advancing the real target branch must not invalidate work descended from the pinned baseline.
    def test_target_branch_can_advance_during_agent_work(self):
        def agent(*args):
            self.agent(*args)
            (self.origin / "target-only.txt").write_text("another change\n")
            self.git("add", ".")
            self.git("commit", "-m", "advance target")
            target_head = self.git("rev-parse", "release").strip()
            self.assertNotEqual(target_head, self.sha)
            self.prs[0]["baseRefOid"] = target_head

        with (
            patch.object(self.task, "capture", side_effect=self.external),
            patch.object(self.task, "run_agent", side_effect=agent),
            contextlib.redirect_stdout(io.StringIO()) as logs,
        ):
            self.task.execute(self.job)
        self.assertEqual(self.agent_calls, 1)
        self.assertIn("Verified repository script", logs.getvalue())

    # Reject a task whose final commit does not descend from the pinned baseline.
    def test_completion_requires_pinned_baseline_ancestry(self):
        def agent(*args):
            self.agent(*args)
            repository, env = args[2:4]
            tree = self.external(
                ["git", "rev-parse", "HEAD^{tree}"], repository, env
            ).strip()
            orphan = subprocess.check_output(
                ["git", "commit-tree", tree, "-m", "unrelated task"],
                cwd=repository,
                env=env,
                text=True,
            ).strip()
            self.external(["git", "update-ref", "HEAD", orphan], repository, env)

        with (
            patch.object(self.task, "capture", side_effect=self.external),
            patch.object(self.task, "run_agent", side_effect=agent),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(subprocess.CalledProcessError) as failure,
        ):
            self.task.execute(self.job)
        self.assertEqual(
            failure.exception.cmd[:3], ["git", "merge-base", "--is-ancestor"]
        )

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

    # Treat output as opaque diagnostics, not events; retain sanitized stdout/stderr after exit.
    def test_agent_output_is_not_interpreted_and_is_redacted(self):
        credentials = 'github_pat_synthetic_token {"SecretAccessKey":"rotated-secret","SessionToken":"rotated-session","AccessKeyId":"ASIAXXXXXXXXXXXXXXXX"}'
        nested = json.dumps(
            {"message": json.dumps({"SessionToken": "nested-private-value"})}
        )
        code = (
            'import sys; assert sys.stdin.read() == "prompt"; '
            'print("plain output session-token"); '
            'print(\'{"type":"turn.failed"}\'); '
            f"print({credentials!r}); print({nested!r}); "
            'print("diagnostic SecretAccessKey=rotated-private-value", file=sys.stderr)'
        )
        with contextlib.redirect_stdout(io.StringIO()) as logs:
            result = self.task.run_agent(
                [os.sys.executable, "-c", code],
                "prompt",
                self.root,
                self.env,
                ["session-token", "github_pat_synthetic_token"],
            )
        self.assertIsNone(result)
        self.assertIn("plain output [REDACTED]", logs.getvalue())
        self.assertIn('{"type":"turn.failed"}', logs.getvalue())
        self.assertIn("diagnostic SecretAccessKey=[REDACTED]", logs.getvalue())
        self.assertIn("actual exit code 0", logs.getvalue())
        for value in (
            "session-token",
            "github_pat_synthetic_token",
            "rotated-private-value",
            "rotated-secret",
            "rotated-session",
            "ASIAXXXXXXXXXXXXXXXX",
            "nested-private-value",
        ):
            with self.subTest(secret=value):
                self.assertNotIn(value, logs.getvalue())

    # A nonzero process exit fails even with success-looking output, preserving actual exit diagnostics.
    def test_agent_process_failure_logs_redacted_output_and_propagates_exit(self):
        code = (
            'import sys; sys.stdin.read(); print(\'{"type":"turn.completed"}\'); '
            'print("session-token", file=sys.stderr); sys.exit(7)'
        )
        with (
            contextlib.redirect_stdout(io.StringIO()) as logs,
            self.assertRaises(subprocess.CalledProcessError) as failure,
        ):
            self.task.run_agent(
                [os.sys.executable, "-c", code],
                "prompt",
                self.root,
                self.env,
                ["session-token"],
            )
        self.assertEqual(failure.exception.returncode, 7)
        self.assertIn("actual exit code 7", logs.getvalue())
        self.assertNotIn("session-token", logs.getvalue())

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
        for index, failure in enumerate(("missing", "nonexecutable", "nonzero")):
            self.sdk()

            def agent(*args, failure=failure):
                result = self.agent(*args)
                script = args[2] / "scripts/verify"
                if failure == "missing":
                    script.unlink()
                elif failure == "nonexecutable":
                    script.chmod(0o644)
                else:
                    (args[2] / "greeting.txt").write_text("")
                return result

            with (
                self.subTest(failure=failure),
                patch.dict(
                    os.environ, {"WORKSPACE": str(self.root / ("final-" + str(index)))}
                ),
                patch.object(self.task, "capture", side_effect=self.external),
                patch.object(self.task, "run_agent", side_effect=agent),
                contextlib.redirect_stdout(io.StringIO()) as logs,
                self.assertRaises(
                    subprocess.CalledProcessError
                    if failure == "nonzero"
                    else ValueError
                ) as error,
            ):
                self.task.execute(self.job)
            if failure == "nonzero":
                self.assertEqual(error.exception.returncode, 1)
            else:
                self.assertIn("Missing or non-executable", str(error.exception))
                self.assertIn(
                    "Missing or non-executable repository script: ./scripts/verify",
                    logs.getvalue(),
                )
            self.assertEqual(self.agent_calls, index + 1)
            self.assertEqual(len(self.prs), 1)

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


if __name__ == "__main__":
    unittest.main()
