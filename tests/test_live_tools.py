import contextlib
import importlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class LiveToolsTests(unittest.TestCase):
    def module(self, name):
        return importlib.import_module("tools." + name)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.job = {
            "repository": "owner/repository",
            "baseSha": "a" * 40,
            "task": "Change greeting and tests",
        }
        self.job_file = Path(self.directory.name) / "job.json"
        self.job_file.write_text(json.dumps(self.job))
        self.arn = "arn:aws:states:us-east-1:172318459814:execution:software-factory-poc:fixed-name"
        self.output = {
            "microvmId": "microvm-test",
            "status": "ok",
            "termination": "TERMINATED",
        }
        self.execution = {
            "executionArn": self.arn,
            "status": "SUCCEEDED",
            "input": self.job_file.read_text(),
            "output": json.dumps(self.output),
        }
        self.history = []
        for resource, parameters, result in (
            ("runMicrovm", {"ImageVersion": "5.0"}, {"MicrovmId": "microvm-test"}),
            ("createMicrovmAuthToken", {}, {}),
            (
                "invoke",
                {"Headers": {"X-aws-proxy-auth": "never-print-jwe"}},
                {"StatusCode": 200, "ResponseBody": {"status": "pending"}},
            ),
            (
                "invoke",
                {"Headers": {"X-aws-proxy-auth": "never-print-jwe"}},
                {"StatusCode": 200, "ResponseBody": {"status": "ok"}},
            ),
            ("terminateMicrovm", {}, {}),
        ):
            self.history.extend(
                [
                    {
                        "type": "TaskScheduled",
                        "taskScheduledEventDetails": {
                            "resource": resource,
                            "parameters": json.dumps(parameters),
                        },
                    },
                    {
                        "type": "TaskSucceeded",
                        "taskSucceededEventDetails": {"output": json.dumps(result)},
                    },
                ]
            )

    def test_image_validation_precedes_review_and_deployment(self):
        module = self.module("validate_deploy_image")
        live = module.live
        events = []
        with (
            patch.object(live, "local", side_effect=lambda *a: events.append(a)),
            patch.object(live, "operator", return_value=Mock()),
            patch.object(live, "idle", side_effect=lambda *a: events.append(("idle",))),
            patch.object(live, "cdk", side_effect=lambda s, *a: events.append(a)),
            patch.object(
                live, "confirm", side_effect=lambda *a: events.append(("confirm",))
            ),
            patch.object(
                live, "stack_outputs", return_value={"LatestActiveVersion": "5.0"}
            ),
            patch.object(
                live,
                "verify_source",
                side_effect=lambda *a: events.append(("verified",)),
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            module.main([])
        self.assertEqual(events[0], ("make", "verify", "preflight", "synth"))
        deploys = [e for e in events if e[0] == "deploy"]
        self.assertEqual([e[1] for e in deploys], ["FactoryArtifacts", "FactoryImage"])
        self.assertLess(events.index(("confirm",)), events.index(deploys[0]))
        self.assertEqual(events[-1], ("verified",))
        with (
            patch.object(live, "local", side_effect=RuntimeError("preflight failed")),
            patch.object(live, "cdk") as cdk,
        ):
            with self.assertRaises(RuntimeError):
                module.main([])
            cdk.assert_not_called()

    def test_declined_review_never_deploys(self):
        module = self.module("validate_deploy_image")
        with (
            patch.object(module.live, "local"),
            patch.object(module.live, "operator"),
            patch.object(module.live, "idle"),
            patch.object(module.live, "cdk") as cdk,
            patch("builtins.input", return_value="no"),
            self.assertRaises(ValueError),
        ):
            module.main([])
        self.assertEqual([c.args[1] for c in cdk.call_args_list], ["diff"])

    def test_infrastructure_refuses_stale_image_before_deploy(self):
        module = self.module("deploy_infra")
        with (
            patch.object(module.live, "local"),
            patch.object(module.live, "operator"),
            patch.object(module.live, "idle"),
            patch.object(module.live, "verify_source", side_effect=ValueError("stale")),
            patch.object(module.live, "cdk") as cdk,
            self.assertRaises(ValueError),
        ):
            module.main(["--version", "5.0"])
        cdk.assert_not_called()

    def test_infrastructure_requires_explicit_pin_and_checks_readback(self):
        module = self.module("deploy_infra")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            module.main([])
        with (
            patch.object(module.live, "local"),
            patch.object(module.live, "operator"),
            patch.object(module.live, "idle"),
            patch.object(module.live, "verify_source"),
            patch.object(module.live, "workflow_definition", return_value={}),
            patch.object(module.live, "validate_definition"),
            patch.object(module.live, "confirm"),
            patch.object(module.live, "cdk") as cdk,
            patch.object(
                module.live, "verify_workflow", side_effect=ValueError("drift")
            ) as verify,
            self.assertRaises(ValueError),
        ):
            module.main(["--version", "5.0"])
        self.assertEqual([c.args[1] for c in cdk.call_args_list], ["diff", "deploy"])
        self.assertIn("FactoryWorkflow:ImageVersion=5.0", cdk.call_args.args)
        verify.assert_called_once()

    def test_invalid_prospective_definition_stops_before_infrastructure_deployment(
        self,
    ):
        module = self.module("deploy_infra")
        with (
            patch.object(module.live, "local"),
            patch.object(module.live, "operator"),
            patch.object(module.live, "idle"),
            patch.object(module.live, "verify_source"),
            patch.object(module.live, "workflow_definition", return_value={}),
            patch.object(
                module.live,
                "validate_definition",
                side_effect=ValueError("invalid ASL"),
            ),
            patch.object(module.live, "verify_workflow"),
            patch.object(module.live, "confirm"),
            patch.object(module.live, "cdk") as cdk,
            self.assertRaisesRegex(ValueError, "invalid ASL"),
        ):
            module.main(["--version", "5.0"])
        cdk.assert_not_called()

    def test_source_verification_uses_existing_exact_artifact_guard(self):
        live = self.module("live")
        template = {
            "Resources": {
                "Image": {
                    "Type": "AWS::Lambda::MicrovmImage",
                    "Properties": {
                        "Name": "software-factory-poc",
                        "CodeArtifact": {"Uri": "s3://new"},
                    },
                }
            }
        }
        image = {
            "imageArn": live.IMAGE_ARN,
            "state": "UPDATED",
            "latestActiveImageVersion": "5.0",
        }
        version = {
            "imageArn": live.IMAGE_ARN,
            "imageVersion": "5.0",
            "state": "SUCCESSFUL",
            "status": "ACTIVE",
            "codeArtifact": {"uri": "s3://new"},
        }
        with patch.object(live, "template", return_value=template):
            with patch.object(live, "aws", side_effect=[image, version]):
                self.assertEqual(live.verify_source(Mock(), "5.0"), version)
            with (
                patch.object(
                    live,
                    "aws",
                    side_effect=[
                        image,
                        {**version, "codeArtifact": {"uri": "s3://old"}},
                    ],
                ),
                self.assertRaises(ValueError),
            ):
                live.verify_source(Mock(), "5.0")

    def test_pagination_and_busy_guard_do_not_ignore_later_pages(self):
        live = self.module("live")
        with (
            patch.object(
                live,
                "aws",
                side_effect=[
                    {"executions": [], "nextToken": "page2"},
                    {"executions": [{"executionArn": "other"}]},
                ],
            ) as aws,
            self.assertRaises(ValueError),
        ):
            live.idle(Mock())
        self.assertEqual(aws.call_args.kwargs["nextToken"], "page2")
        with (
            patch.object(
                live,
                "aws",
                side_effect=[{"executions": []}, {"items": [{"state": "RUNNING"}]}],
            ),
            self.assertRaises(ValueError),
        ):
            live.idle(Mock())

    def test_missing_workflow_allows_image_preparation_but_access_errors_do_not(self):
        from botocore.exceptions import ClientError

        live = self.module("live")
        with patch.object(
            live,
            "aws",
            side_effect=[
                ClientError(
                    {"Error": {"Code": "StateMachineDoesNotExist"}}, "ListExecutions"
                ),
                {"items": []},
            ],
        ):
            live.idle(Mock())
        with (
            patch.object(
                live,
                "aws",
                side_effect=ClientError(
                    {"Error": {"Code": "AccessDeniedException"}}, "ListExecutions"
                ),
            ),
            self.assertRaises(ClientError),
        ):
            live.idle(Mock())

    def test_cdk_credentials_stay_in_local_subprocess_environment(self):
        live = self.module("live")
        session = Mock()
        session.get_credentials.return_value.get_frozen_credentials.return_value = Mock(
            access_key="local-access", secret_key="local-secret", token="local-token"
        )
        with (
            patch.object(live.subprocess, "run") as run,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            live.cdk(session, "diff", "FactoryImage")
        self.assertNotIn("local-secret", output.getvalue())
        self.assertNotIn("local-secret", str(run.call_args.args))
        self.assertEqual(
            run.call_args.kwargs["env"]["AWS_SECRET_ACCESS_KEY"], "local-secret"
        )

    def run_job(
        self, existing=True, final=None, vm_state="TERMINATED", start_error=None
    ):
        module = self.module("run_happy_path")
        live = module.live
        session = Mock()
        self.requests = []
        descriptions = iter(
            [self.execution] if existing else [None, final or self.execution]
        )

        def aws(s, service, operation, **params):
            self.requests.append((operation, params))
            if operation == "describe_execution":
                result = (
                    self.execution
                    if self.execution["status"] == "RUNNING"
                    else next(descriptions)
                )
                if result is None:
                    from botocore.exceptions import ClientError

                    raise ClientError(
                        {
                            "Error": {
                                "Code": "ExecutionDoesNotExist",
                                "Message": "not found",
                            }
                        },
                        operation,
                    )
                return result
            if operation == "start_execution":
                if start_error:
                    raise start_error
                return {"executionArn": self.arn}
            if operation == "get_microvm":
                return {
                    "microvmId": "microvm-test",
                    "state": vm_state,
                    "imageVersion": "5.0",
                }
            if operation == "get_microvm_image_version":
                return {"environmentVariables": {"PR_TARGET_BRANCH": "main"}}
            raise AssertionError(operation)

        with (
            patch.object(live, "aws", side_effect=aws),
            patch.object(live, "collect", return_value=self.history),
            patch.object(live, "idle") as idle,
            patch.object(live, "confirm") as confirm,
            patch.object(live, "local"),
            patch.object(
                live,
                "verify_workflow",
                return_value={
                    "environmentVariables": {
                        "AUTHORIZED_REPOSITORY": "owner/repository",
                        "PR_TARGET_BRANCH": "main",
                    }
                },
            ),
            patch.object(live, "output", return_value="a" * 40),
            patch.object(
                module,
                "inspect_publication",
                return_value="https://github.com/owner/repository/pull/1",
            ),
            patch.object(module.time, "sleep"),
            patch.dict("os.environ", {}, clear=False),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            module.execute(session, "fixed-name", self.job_file)
        self.assertNotIn("never-print-jwe", output.getvalue())
        self.assertIn("microvm-test", output.getvalue())
        self.assertEqual(confirm.call_count, 0 if existing else 1)
        self.assertEqual(idle.call_count, 0 if existing else 2)

    def test_new_job_starts_exact_identity_once_and_proves_cleanup(self):
        self.run_job(existing=False)
        starts = [p for op, p in self.requests if op == "start_execution"]
        self.assertEqual(
            starts,
            [
                {
                    "stateMachineArn": self.module("live").MACHINE_ARN,
                    "name": "fixed-name",
                    "input": self.job_file.read_text(),
                }
            ],
        )
        self.assertNotIn("terminate_microvm", [op for op, p in self.requests])

    def test_resume_existing_execution_never_submits_again(self):
        self.run_job()
        self.assertNotIn("start_execution", [op for op, p in self.requests])

    def test_changed_input_and_failed_execution_never_start_again(self):
        for execution in (
            {**self.execution, "input": "different input"},
            {**self.execution, "status": "FAILED"},
        ):
            with self.subTest(execution=execution):
                original = self.execution
                self.execution = execution
                with self.assertRaises(ValueError):
                    self.run_job()
                self.assertNotIn("start_execution", [op for op, p in self.requests])
                self.execution = original

    def test_uncertain_acknowledgement_stops_after_one_submission(self):
        with self.assertRaises(TimeoutError):
            self.run_job(
                existing=False, start_error=TimeoutError("uncertain acknowledgement")
            )
        self.assertEqual([op for op, p in self.requests].count("start_execution"), 1)

    def test_unauthorized_job_stops_before_submission(self):
        self.job_file.write_text(
            json.dumps({**self.job, "repository": "other/repository"})
        )
        with self.assertRaises(ValueError):
            self.run_job(existing=False)
        self.assertNotIn("start_execution", [op for op, p in self.requests])

    def test_unconfirmed_cleanup_and_observation_timeout_never_cancel_or_terminate(
        self,
    ):
        with self.assertRaisesRegex(ValueError, "Cleanup unconfirmed"):
            self.run_job(vm_state="RUNNING")
        self.execution = {**self.execution, "status": "RUNNING"}
        with self.assertRaisesRegex(ValueError, "Cleanup unconfirmed"):
            self.run_job(vm_state="RUNNING")
        operations = [op for op, p in self.requests]
        self.assertEqual(operations.count("describe_execution"), 182)
        self.assertNotIn("start_execution", operations)
        self.assertNotIn("stop_execution", operations)
        self.assertNotIn("terminate_microvm", operations)

    def test_history_rejects_bad_token_status_duplicate_launch_and_missing_id(self):
        module = self.module("run_happy_path")
        self.assertEqual(module.check_history(self.history), ("microvm-test", "5.0", 2))
        for mutate in (
            lambda h: h[6]["taskScheduledEventDetails"].update(
                parameters=json.dumps({"Headers": {"X-aws-proxy-auth": "other"}})
            ),
            lambda h: h[7]["taskSucceededEventDetails"].update(
                output=json.dumps(
                    {"StatusCode": 200, "ResponseBody": {"status": "error"}}
                )
            ),
            lambda h: h.append(h[0]),
            lambda h: h[1]["taskSucceededEventDetails"].update(output="{}"),
        ):
            history = json.loads(json.dumps(self.history))
            mutate(history)
            with self.assertRaises(ValueError):
                module.check_history(history)

    def test_publication_checks_actual_logs_branch_ancestry_and_pr(self):
        module = self.module("run_happy_path")
        live = module.live
        self.job["baseSha"] = self.job["baseSha"].upper()
        logs = "Command completed: git switch -c factory/task-abc; actual exit code 0\nAgent process actual exit code 0\nIndependently verify repository script and publication\nCommand completed: ./scripts/verify; actual exit code 0\nVerified repository script, nonempty commit, baseline ancestry and one agent-published unmerged PR; human correctness review required"
        pr = {
            "number": 1,
            "state": "OPEN",
            "mergedAt": None,
            "baseRefName": "main",
            "headRefName": "factory/task-abc",
            "headRefOid": "b" * 40,
        }
        comparison = {
            "merge_base_commit": {"sha": "a" * 40},
            "ahead_by": 1,
            "files": [{"filename": "greeting.py"}],
        }
        for base, head, messages in (
            ("a" * 40, "b" * 40, logs),
            ("c" * 40, "b" * 40, logs),
            ("a" * 40, "c" * 40, logs),
            ("a" * 40, "b" * 40, "missing evidence"),
        ):
            with self.subTest(base=base, head=head, messages=messages):
                compare = {**comparison, "merge_base_commit": {"sha": base}}
                with (
                    patch.object(module, "worker_logs", return_value=messages),
                    patch.object(
                        live,
                        "output",
                        side_effect=[
                            json.dumps([pr]),
                            json.dumps({"object": {"sha": head}}),
                            json.dumps(compare),
                            "diff",
                        ],
                    ),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    if base == "a" * 40 and head == "b" * 40 and messages == logs:
                        self.assertEqual(
                            module.inspect_publication(
                                Mock(), "microvm-test", "5.0", self.job, "main"
                            ),
                            "https://github.com/owner/repository/pull/1",
                        )
                    else:
                        with self.assertRaises(ValueError):
                            module.inspect_publication(
                                Mock(), "microvm-test", "5.0", self.job, "main"
                            )

    def test_workflow_readback_and_prospective_validation_use_resolved_synthesis(self):
        live = self.module("live")
        expected = {"States": {"LaunchVM": {"Parameters": {"ImageVersion": "5.0"}}}}
        value = {
            "Fn::Join": [
                "",
                [
                    '{"States":{"LaunchVM":{"Parameters":{"ImageVersion":"',
                    {"Ref": "ImageVersion"},
                    '"}}}}',
                ],
            ]
        }
        source = {
            "Resources": {"Workflow": {"Properties": {"DefinitionString": value}}}
        }
        actual = {
            "status": "ACTIVE",
            "type": "STANDARD",
            "definition": json.dumps(expected),
        }
        connection = "arn:aws:events:us-east-1:172318459814:connection/example/id"
        connection_state = {"ConnectionState": "AUTHORIZED"}
        image = {"environmentVariables": {"PR_TARGET_BRANCH": "main"}}

        def aws(s, service, operation, **params):
            if operation == "describe_state_machine":
                return actual
            if operation == "describe_stacks":
                return {"Stacks": [{"Outputs": []}]}
            if operation == "validate_state_machine_definition":
                self.assertEqual(json.loads(params["definition"]), expected)
                return {"result": "OK", "diagnostics": []}
            if operation == "describe_connection":
                self.assertEqual(params["Name"], "example")
                return connection_state
            raise AssertionError(operation)

        with (
            patch.object(live, "aws", side_effect=aws),
            patch.object(live, "template", return_value=source),
            patch.object(
                live, "stack_outputs", return_value={"ConnectionArn": connection}
            ),
            patch.object(live, "verify_source", return_value=image),
        ):
            live.validate_definition(
                Mock(), live.workflow_definition(Mock(), "5.0", connection)
            )
            self.assertEqual(live.verify_workflow(Mock(), "5.0"), image)
            with self.assertRaisesRegex(ValueError, "requested pin"):
                live.verify_workflow(Mock(), "6.0")
            connection_state["ConnectionState"] = "DEAUTHORIZED"
            with self.assertRaisesRegex(ValueError, "not authorized"):
                live.verify_workflow(Mock(), "5.0")
            connection_state["ConnectionState"] = "AUTHORIZED"
            actual["definition"] = json.dumps({**expected, "unexpected": True})
            with self.assertRaisesRegex(ValueError, "differs"):
                live.verify_workflow(Mock(), "5.0")
        imports = {"export": "import-value"}
        for intrinsic, result in (
            ({"Fn::ImportValue": "export"}, "import-value"),
            ({"Fn::GetAtt": ["Connection", "Arn"]}, connection),
            ({"Ref": "AWS::Partition"}, "aws"),
        ):
            self.assertEqual(
                live.resolve_definition(intrinsic, imports, connection, "5.0"), result
            )
        with self.assertRaises(ValueError):
            live.resolve_definition({"Fn::Unknown": []}, {}, connection, "5.0")
        with (
            patch.object(
                live,
                "aws",
                return_value={"result": "FAIL", "diagnostics": [{"severity": "ERROR"}]},
            ),
            self.assertRaises(ValueError),
        ):
            live.validate_definition(Mock(), expected)

    def test_sdk_boundary_and_wrong_account_guard_need_no_live_credentials(self):
        import boto3
        from botocore.stub import Stubber

        live = self.module("live")
        client = boto3.client(
            "sts",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        response = {
            "Account": "172318459814",
            "Arn": "arn:aws:iam::172318459814:user/test",
            "UserId": "user",
        }
        session = Mock()
        session.client.return_value = client
        with Stubber(client) as stub:
            stub.add_response("get_caller_identity", response, {})
            self.assertEqual(live.aws(session, "sts", "get_caller_identity"), response)
            stub.assert_no_pending_responses()
        self.assertEqual(
            session.client.call_args.kwargs["config"].retries, {"total_max_attempts": 1}
        )
        with (
            patch.object(live.boto3, "Session"),
            patch.object(live, "aws", return_value={"Account": "wrong"}),
            self.assertRaises(ValueError),
        ):
            live.operator("profile")

    def test_cli_boundaries_report_safe_failures_and_require_explicit_job_inputs(self):
        live = self.module("live")
        for error in (
            RuntimeError("never-print-secret"),
            ValueError("never-print-secret"),
            KeyboardInterrupt(),
        ):
            with contextlib.redirect_stderr(io.StringIO()) as output:
                self.assertEqual(live.run(Mock(side_effect=error)), 1)
            self.assertNotIn("never-print-secret", output.getvalue())
            self.assertIn("same name", output.getvalue())
        with contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(
                live.run(
                    Mock(side_effect=live.OperatorError("safe validation failure"))
                ),
                1,
            )
        self.assertIn("safe validation failure", output.getvalue())
        self.assertEqual(live.run(Mock()), 0)
        module = self.module("run_happy_path")
        with (
            patch.object(live, "operator") as operator,
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            module.main([])
        operator.assert_not_called()
        with (
            patch.object(live, "operator", return_value="session"),
            patch.object(module, "execute") as execute,
        ):
            module.main(
                [
                    "--name",
                    "fixed-name",
                    "--job-file",
                    str(self.job_file),
                    "--profile",
                    "operator",
                ]
            )
        execute.assert_called_once_with(
            "session", "fixed-name", self.job_file.resolve()
        )

    def test_local_commands_outputs_and_profile_credentials_are_not_injected_into_images(
        self,
    ):
        import sys

        live = self.module("live")
        self.assertEqual(
            live.output(sys.executable, "-c", "print('command-ok')").strip(),
            "command-ok",
        )
        live.local(
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('Makefile').is_file()",
        )
        with patch.object(
            live,
            "aws",
            return_value={
                "Stacks": [
                    {"Outputs": [{"OutputKey": "Version", "OutputValue": "5.0"}]}
                ]
            },
        ):
            self.assertEqual(
                live.stack_outputs(Mock(), "FactoryImage"), {"Version": "5.0"}
            )
        session = Mock()
        session.get_credentials.return_value.get_frozen_credentials.return_value = Mock(
            access_key="access", secret_key="secret", token=None
        )
        with (
            patch.dict(
                "os.environ",
                {"AWS_PROFILE": "old-profile", "AWS_SESSION_TOKEN": "old-token"},
            ),
            patch.object(live.subprocess, "run") as run,
        ):
            live.cdk(session, "diff", "FactoryImage")
        self.assertNotIn("AWS_PROFILE", run.call_args.kwargs["env"])
        self.assertNotIn("AWS_SESSION_TOKEN", run.call_args.kwargs["env"])
        with (
            patch.object(live.boto3, "Session", return_value=session),
            patch.object(live, "aws", return_value={"Account": live.ACCOUNT}),
        ):
            self.assertIs(live.operator("operator"), session)

    def test_invalid_job_and_history_outputs_never_become_success(self):
        self.job_file.write_text("x" * 16385)
        with self.assertRaisesRegex(ValueError, "16 KB"):
            self.run_job(existing=False)
        self.assertEqual(self.requests, [])
        self.job_file.write_text(json.dumps(self.job))
        self.execution["output"] = json.dumps({**self.output, "microvmId": "other"})
        with self.assertRaisesRegex(ValueError, "Final output"):
            self.run_job()
        self.execution["output"] = json.dumps(self.output)
        self.history = []
        with self.assertRaisesRegex(ValueError, "No unique VM ID"):
            self.run_job()

    def test_baseline_mismatch_and_describe_access_error_never_submit(self):
        from botocore.exceptions import ClientError

        self.job_file.write_text(json.dumps({**self.job, "baseSha": "c" * 40}))
        with self.assertRaisesRegex(ValueError, "baseline"):
            self.run_job(existing=False)
        self.assertNotIn("start_execution", [op for op, p in self.requests])
        module = self.module("run_happy_path")
        with (
            patch.object(
                module.live,
                "aws",
                side_effect=ClientError(
                    {"Error": {"Code": "AccessDeniedException"}}, "DescribeExecution"
                ),
            ) as aws,
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(ClientError),
        ):
            module.execute(Mock(), "fixed-name", self.job_file)
        self.assertEqual(aws.call_count, 1)

    def test_infrastructure_success_prints_verified_pin_without_starting_job(self):
        module = self.module("deploy_infra")
        with (
            patch.object(module.live, "local"),
            patch.object(module.live, "operator"),
            patch.object(module.live, "idle"),
            patch.object(module.live, "verify_source"),
            patch.object(module.live, "workflow_definition", return_value={}),
            patch.object(module.live, "validate_definition"),
            patch.object(module.live, "confirm"),
            patch.object(module.live, "cdk"),
            patch.object(module.live, "verify_workflow"),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            module.main(["--version", "5.0"])
        self.assertIn("No Job started", output.getvalue())

    def test_worker_logs_paginate_and_fail_closed_when_missing(self):
        module = self.module("run_happy_path")
        streams = [{"logStreamName": "2026/11/02[5.0]microvm-test"}]
        with (
            patch.object(module.live, "collect", return_value=streams),
            patch.object(
                module.live,
                "aws",
                side_effect=[
                    {"events": [{"message": "page one"}], "nextForwardToken": "next"},
                    {
                        "events": [
                            {
                                "message": "Verified repository script, nonempty commit, baseline ancestry"
                            }
                        ],
                        "nextForwardToken": "next",
                    },
                ],
            ) as aws,
        ):
            self.assertIn("page one", module.worker_logs(Mock(), "microvm-test", "5.0"))
            self.assertEqual(aws.call_args.kwargs["nextToken"], "next")
        with (
            patch.object(module.live, "collect", return_value=[]),
            patch.object(module.time, "sleep") as sleep,
            self.assertRaises(ValueError),
        ):
            module.worker_logs(Mock(), "microvm-test", "5.0")
        self.assertEqual(sleep.call_count, 5)


if __name__ == "__main__":
    unittest.main()
