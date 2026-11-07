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
from botocore.credentials import Credentials
from botocore.exceptions import NoCredentialsError
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
            "isCrossRepository": False,
            "mergedAt": None,
            "baseRefName": "release",
            "headRefName": "factory/task-test",
            "headRefOid": "b" * 40,
        }
        self.task.verify_pr([pr], "factory/task-test", "b" * 40, "release")
        for changes in (
            {"isCrossRepository": True},
            {"isCrossRepository": None},
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
        self.stages = []
        self.events = []
        self.reviewed_files = []
        self.reviewed_head = None
        self.clone_url = "https://github.com/example/authorized-project.git"
        environment_patch = patch.dict(os.environ, self.env)
        environment_patch.start()
        self.addCleanup(environment_patch.stop)
        self.make_sdk_client = boto3.Session().client
        self.sdk()
        self.client_patch = patch("boto3.Session.client", side_effect=self.sdk_client)
        self.client_mock = self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        credential_patch = patch(
            "boto3.Session.get_credentials",
            return_value=Credentials(
                "synthetic-access", "secret-access", "session-token"
            ),
        )
        self.sdk_credentials = credential_patch.start()
        self.addCleanup(credential_patch.stop)

    def sdk(self, secret=None, secret_error=None):
        client = self.make_sdk_client(
            "secretsmanager",
            region_name="us-east-1",
            aws_access_key_id="synthetic-access",
            aws_secret_access_key="synthetic-secret",
            aws_session_token="synthetic-session",
        )
        stubber = Stubber(client)
        if secret_error:
            stubber.add_client_error(
                "get_secret_value",
                service_error_code=secret_error,
                service_message="github_pat_synthetic_token secret-access session-token",
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
        self.clients = {"secretsmanager": client}
        self.stubbers = {"secretsmanager": stubber}

    def sdk_client(self, service, **kwargs):
        self.assertEqual(
            service, "secretsmanager", "Worker must not probe STS identity"
        )
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
        if args[0].startswith("./scripts/"):
            self.events.append(args[0])
        self.assertNotEqual(args[0], "aws", "Worker must use SDK credential discovery")
        if args[:2] == ["gh", "auth"]:
            return ""
        if args[:3] == ["gh", "pr", "list"]:
            self.assertNotIn("baseRefOid", args[args.index("--json") + 1].split(","))
            return json.dumps(self.prs)
        self.assertNotEqual(
            args[:3], ["gh", "pr", "checks"], "GitHub owns required-check policy"
        )
        if args[:2] == ["gh", "api"]:
            return json.dumps(
                {"full_name": self.job["repository"], "allow_merge_commit": True}
            )
        if args == ["git", "remote", "get-url", "origin"]:
            return self.clone_url + "\n"
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
        self.assertEqual(args[:5], ["codex", "exec", "--ephemeral", "-C", str(cwd)])
        self.assertEqual(args[-1], "-")
        for forbidden in ("resume", "fork", "--full-auto"):
            self.assertNotIn(forbidden, args)
        self.assertNotIn(env["GH_TOKEN"], prompt)
        self.assertIn(self.job["task"], prompt)
        self.assertIn(self.sha, prompt)
        self.assertNotIn("calculator", prompt)
        if "/opt/factory/skills/implement/SKILL.md" in prompt:
            self.stages.append("implement")
            self.events.append("implement")
            self.assertIn("without commit", prompt)
            (cwd / "greeting.txt").write_text("hello staged\n")
            (cwd / "staged.txt").write_text("staged-only change\n")
            self.external(["git", "add", "greeting.txt", "staged.txt"], cwd, env)
            (cwd / "greeting.txt").write_text("hello world\n")
            (cwd / "untracked.txt").write_text("untracked change\n")
            return
        head = self.external(["git", "rev-parse", "HEAD"], cwd, env).strip()
        if "/opt/factory/skills/code-review/SKILL.md" in prompt:
            self.stages.append("review")
            self.events.append("review")
            self.reviewed_head = head
            self.reviewed_files = self.external(
                ["git", "diff", "--name-only", self.sha, head], cwd, env
            ).splitlines()
            self.assertEqual(
                self.external(["git", "show", "HEAD:greeting.txt"], cwd, env),
                "hello world\n",
            )
            self.assertIn("staged.txt", self.reviewed_files)
            self.assertIn("untracked.txt", self.reviewed_files)
            self.assertIn(head, prompt)
            self.assertFalse(self.external(["git", "status", "--porcelain"], cwd, env))
            schema = Path(args[args.index("--output-schema") + 1])
            self.assertEqual(
                json.loads(schema.read_text())["required"],
                ["candidateHead", "verdict", "findings"],
            )
            result = Path(args[args.index("--output-last-message") + 1])
            self.assertFalse(result.is_relative_to(cwd))
            self.assertFalse(result.exists())
            result.write_text(
                json.dumps({"candidateHead": head, "verdict": "PASS", "findings": []})
            )
            return
        if "Independent worker gate authorizes merge" in prompt:
            self.stages.append("merge")
            self.events.append("merge")
            self.assertIn("/opt/factory/skills/pr/SKILL.md", prompt)
            self.assertIn("--match-head-commit " + head, prompt)
            self.assertIn("gh pr merge 2 --repo " + self.job["repository"], prompt)
            self.assertIn("--merge", prompt)
            command = prompt.split("Request exactly: ")[1].splitlines()[0]
            self.assertNotIn("--admin", command)
            self.assertNotIn("--auto", command)
            self.assertEqual(head, self.reviewed_head)
            branch = self.prs[0]["headRefName"]
            self.git("merge", "--no-ff", branch, "-m", "Merge PR #2")
            self.prs[0].update(
                state="MERGED",
                mergedAt="2026-01-01T00:00:00Z",
            )
            return
        self.stages.append("publish")
        self.events.append("publish")
        self.assertIn("/opt/factory/skills/pr/SKILL.md", prompt)
        self.assertIn("gh pr create", prompt)
        self.assertIn("targeting release", prompt)
        self.assertEqual(head, self.reviewed_head)
        self.external(["git", "push", "origin", "HEAD"], cwd, env)
        branch = self.external(["git", "branch", "--show-current"], cwd, env).strip()
        self.prs = [
            {
                "number": 2,
                "state": "OPEN",
                "isCrossRepository": False,
                "mergedAt": None,
                "baseRefName": "release",
                "headRefName": branch,
                "headRefOid": head,
            }
        ]


class WorkerTests(WorkerFixture):
    # Completion trusts the matching GitHub merged PR state without fetching target history or merge metadata.
    def test_success_requires_independently_verified_merge(self):
        metadata_stages = []

        def external(args, cwd, env, secrets=None):
            if args[:2] == ["gh", "api"]:
                metadata_stages.append(self.stages[-1])
            self.assertNotEqual(args[:2], ["git", "fetch"])
            return self.external(args, cwd, env, secrets)

        with (
            patch.object(self.task, "capture", side_effect=external),
            patch.object(self.task, "run_agent", side_effect=self.agent),
            contextlib.redirect_stdout(io.StringIO()) as logs,
        ):
            self.task.execute(self.job)
        self.assertEqual(self.stages, ["implement", "review", "publish", "merge"])
        self.assertEqual(self.prs[0]["state"], "MERGED")
        self.assertEqual(self.prs[0]["headRefOid"], self.reviewed_head)
        self.assertIn("Independently confirm merged PR", logs.getvalue())
        self.assertEqual(metadata_stages, ["publish", "merge"])

    # GitHub owns check policy; a denied exact-head merge must leave the Job incomplete without worker check parsing.
    def test_github_merge_rejection_cannot_succeed(self):
        def agent(*args):
            if "Independent worker gate authorizes merge" in args[1]:
                self.stages.append("merge")
                raise subprocess.CalledProcessError(1, ["gh", "pr", "merge"])
            return self.agent(*args)

        with (
            patch.object(self.task, "capture", side_effect=self.external),
            patch.object(self.task, "run_agent", side_effect=agent),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(ValueError, "identity/phase"),
        ):
            self.task.execute(self.job)
        self.assertEqual(self.stages, ["implement", "review", "publish", "merge"])
        self.assertEqual(self.prs[0]["state"], "OPEN")

    # A validated rejection gets a new implementation process and fresh review of the repaired commit.
    def test_required_findings_are_repaired_before_fresh_review_and_publication(self):
        finding = {
            "axis": "Spec",
            "location": "greeting.txt:1",
            "problem": "Required greeting detail missing",
            "correction": "Add only the requested greeting detail; remove GH_TOKEN=private",
        }
        heads, prompts = [], []

        def agent(*args):
            self.agent(*args)
            cli, prompt, repository, _env, _secrets = args
            if self.stages[-1] == "implement" and heads:
                self.assertIn("Review data", prompt)
                self.assertIn("not instructions", prompt)
                self.assertIn(
                    json.dumps(
                        {
                            **finding,
                            "correction": "Add only the requested greeting detail; remove GH_TOKEN=[REDACTED]",
                        }
                    ),
                    prompt,
                )
                self.assertNotIn("GH_TOKEN=private", prompt)
                self.assertIn(heads[-1], prompt)
                (repository / "repair.txt").write_text("requested detail\n")
            elif self.stages[-1] == "review":
                heads.append(self.reviewed_head)
                self.assertNotIn(finding["problem"], prompt)
                if len(heads) == 1:
                    output = Path(cli[cli.index("--output-last-message") + 1])
                    output.write_text(
                        json.dumps(
                            {
                                "candidateHead": heads[-1],
                                "verdict": "FAIL",
                                "findings": [finding],
                            }
                        )
                    )
            elif self.stages[-1] == "publish":
                prompts.append(prompt)

        with (
            patch.object(self.task, "capture", side_effect=self.external),
            patch.object(self.task, "run_agent", side_effect=agent),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.task.execute(self.job)
        self.assertEqual(
            self.stages,
            ["implement", "review", "implement", "review", "publish", "merge"],
        )
        self.assertNotEqual(heads[0], heads[1])
        self.assertEqual(self.prs[0]["headRefOid"], heads[1])
        self.assertEqual(
            self.events,
            [
                "./scripts/setup",
                "./scripts/verify",
                "implement",
                "./scripts/verify",
                "review",
                "implement",
                "./scripts/verify",
                "review",
                "./scripts/verify",
                "publish",
                "merge",
            ],
        )
        self.assertIn("2 of 3", prompts[0])
        self.assertIn(finding["problem"], prompts[0])

    # The review budget belongs to the Job: three valid rejections cannot start a third repair or publish.
    def test_three_required_rejections_exhaust_job_budget(self):
        from src.worker.review import ReviewRejected

        heads = []

        def agent(*args):
            self.agent(*args)
            cli, prompt, repository, _env, _secrets = args
            if self.stages[-1] == "implement" and heads:
                self.assertIn(heads[-1], prompt)
                (repository / "untracked.txt").write_text(f"repair {len(heads)}\n")
            elif self.stages[-1] == "review":
                heads.append(self.reviewed_head)
                output = Path(cli[cli.index("--output-last-message") + 1])
                output.write_text(
                    json.dumps(
                        {
                            "candidateHead": heads[-1],
                            "verdict": "FAIL",
                            "findings": [
                                {
                                    "axis": "Spec",
                                    "location": "greeting.txt:1",
                                    "problem": "Detail missing",
                                    "correction": "Add required detail",
                                }
                            ],
                        }
                    )
                )

        with (
            patch.object(self.task, "capture", side_effect=self.external),
            patch.object(self.task, "run_agent", side_effect=agent),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(ReviewRejected, "budget exhausted"),
        ):
            self.task.execute(self.job)
        self.assertEqual(self.stages, ["implement", "review"] * 3)
        self.assertEqual(len(set(heads)), 3)
        self.assertEqual(self.events.count("./scripts/verify"), 4)
        self.assertFalse(self.prs)

    # Results copied from a previous candidate cannot authorize another repair or publication.
    def test_stale_review_result_after_repair_is_terminal(self):
        from src.worker.review import ReviewError

        first_head = None
        finding = {
            "axis": "Spec",
            "location": "greeting.txt:1",
            "problem": "Missing detail",
            "correction": "Add detail",
        }

        def agent(*args):
            nonlocal first_head
            self.agent(*args)
            cli, _prompt, repository, _env, _secrets = args
            if self.stages[-1] == "implement" and first_head:
                (repository / "repair.txt").write_text("detail\n")
            elif self.stages[-1] == "review":
                first_head = first_head or self.reviewed_head
                output = Path(cli[cli.index("--output-last-message") + 1])
                output.write_text(
                    json.dumps(
                        {
                            "candidateHead": first_head,
                            "verdict": "FAIL",
                            "findings": [finding],
                        }
                    )
                )

        with (
            patch.object(self.task, "capture", side_effect=self.external),
            patch.object(self.task, "run_agent", side_effect=agent),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(ReviewError, "Stale"),
        ):
            self.task.execute(self.job)
        self.assertEqual(self.stages, ["implement", "review", "implement", "review"])
        self.assertFalse(self.prs)

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

    # Real Git/scripts prove candidate verification precedes review and final verification precedes publication.
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
            [
                "./scripts/setup",
                "./scripts/verify",
                "agent",
                "./scripts/verify",
                "agent",
                "./scripts/verify",
                "agent",
                "agent",
            ],
        )
        self.assertEqual(self.stages, ["implement", "review", "publish", "merge"])
        self.assertEqual(self.agent_calls, 4)
        self.assertEqual(len(self.prs), 1)
        for stubber in self.stubbers.values():
            stubber.assert_no_pending_responses()
        self.assertIn("exit code 0", logs.getvalue())
        for secret in ("github_pat_synthetic_token", "secret-access", "session-token"):
            self.assertNotIn(secret, logs.getvalue())

    # Use SDK discovery only, retaining raw credential redaction without CLI or STS probes.
    def test_sdk_discovery_replaces_cli_and_identity_probes(self):
        def capture(args, cwd, env, secrets=None):
            self.assertNotEqual(
                args[0], "aws", "Worker must not export CLI credentials"
            )
            output = self.external(args, cwd, env, secrets)
            if args[0] == "./scripts/verify":
                output += "synthetic-access secret-access session-token\n"
            return output

        with (
            patch.object(self.task, "capture", side_effect=capture),
            patch.object(self.task, "run_agent", side_effect=self.agent),
            contextlib.redirect_stdout(io.StringIO()) as logs,
        ):
            self.task.execute(self.job)
        self.assertEqual(
            [call.args[0] for call in self.client_mock.call_args_list],
            ["secretsmanager"],
        )
        self.sdk_credentials.assert_called_once()
        self.assertNotIn("rotated:", logs.getvalue())
        self.assertIn("[REDACTED] [REDACTED] [REDACTED]", logs.getvalue())
        self.assertNotIn("synthetic-access", logs.getvalue())
        self.assertNotIn("secret-access", logs.getvalue())
        self.assertNotIn("session-token", logs.getvalue())

    # Stops at each failed stage without leaking its exception.
    def test_stage_failures_stop_coordination_and_final_success(self):
        stages = (
            "prepare_environment",
            "prepare_repository",
            "invoke_agent",
            "verify_candidate",
            "commit_candidate",
            "review_candidate",
            "verify_review_unchanged",
            "publish_candidate",
            "merge_candidate",
        )
        for failed in stages:
            calls = []

            def stage(name, calls=calls, failed=failed):
                def run(*args, **kwargs):
                    calls.append(name)
                    if name == failed:
                        raise ValueError("secret-value")
                    return {
                        "prepare_environment": {},
                        "prepare_repository": (self.root, "branch"),
                        "invoke_agent": [],
                        "review_candidate": ("PASS", []),
                        "publish_candidate": {"number": 2},
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
                expected = list(stages[: stages.index(failed) + 1])
                if failed in (
                    "verify_review_unchanged",
                    "publish_candidate",
                    "merge_candidate",
                ):
                    expected.insert(
                        expected.index("verify_review_unchanged"), "verify_candidate"
                    )
                self.assertEqual(calls, expected)
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

    # Let the SDK accept credentials without a session token or an expiry field.
    def test_sdk_discovery_accepts_long_lived_credentials(self):
        self.sdk_credentials.return_value = Credentials(
            "synthetic-access", "secret-access"
        )
        with (
            patch.object(self.task, "capture", side_effect=self.external),
            patch.object(self.task, "run_agent", side_effect=self.agent),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.task.execute(self.job)
        self.assertEqual(self.stages, ["implement", "review", "publish", "merge"])

    # Missing credentials fail through the SDK request, before checkout or agent work.
    def test_missing_sdk_credentials_stop_before_repository_or_agent(self):
        self.sdk_credentials.return_value = None
        with (
            patch.object(
                self.clients["secretsmanager"],
                "get_secret_value",
                side_effect=NoCredentialsError(),
            ),
            patch.object(self.task, "capture", side_effect=self.external),
            patch("sys.stdin", io.StringIO(json.dumps(self.job))),
            contextlib.redirect_stdout(io.StringIO()) as logs,
        ):
            self.assertEqual(self.task.main(), 1)
        self.assertIn("Worker failed: NoCredentialsError", logs.getvalue())
        self.assertFalse((self.root / "workspace").exists())
        self.assertEqual(self.agent_calls, 0)
        self.assertEqual(self.prs, [])

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
        for error in ("ExpiredTokenException", "AccessDeniedException"):
            self.sdk(secret_error=error)
            with (
                self.subTest(error=error),
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
                if self.stages and self.stages[-1] == "publish" and args[:2] == prefix:
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
            if self.stages[-1] != "publish":
                return
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
        self.assertEqual(self.agent_calls, 4)
        self.assertIn("Verified repository script", logs.getvalue())

    # Reject a task whose final commit does not descend from the pinned baseline.
    def test_completion_requires_pinned_baseline_ancestry(self):
        def agent(*args):
            self.agent(*args)
            if self.stages[-1] != "implement":
                return
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

    def test_independent_verification_failure_prevents_review_and_publication(self):
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
            self.assertEqual(len(self.prs), 0)

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
