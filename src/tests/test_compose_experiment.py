import http.client
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from image import compose_experiment as runtime_module
from image import compose_probe as probe_module
from src.tools import compose_experiment as live_module

ROOT = Path(__file__).resolve().parents[2]


class ComposeLifecycleTests(unittest.TestCase):
    def test_only_run_starts_one_post_snapshot_probe(self):
        module = runtime_module
        with tempfile.TemporaryDirectory() as directory:
            runtime = module.Runtime(Path(directory))
            server = module.make_server(runtime, ("127.0.0.1", 0))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(20)"]
            )
            try:
                with patch("subprocess.Popen", return_value=child) as launch:
                    connection = http.client.HTTPConnection(*server.server_address)
                    for hook in ("ready", "validate"):
                        connection.request("POST", module.HOOK + hook)
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        response.read()
                    launch.assert_not_called()
                    for expected in (200, 409):
                        connection.request("POST", module.HOOK + "run")
                        response = connection.getresponse()
                        self.assertEqual(response.status, expected)
                        response.read()
                    launch.assert_called_once()
                    connection.request("GET", "/status")
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertIn(b'"pending"', response.read())
                    connection.close()
            finally:
                child.terminate()
                child.wait(timeout=5)
                server.shutdown()
                thread.join()
                server.server_close()


class ComposeProbeTests(unittest.TestCase):
    def test_stack_start_failure_still_removes_volumes_and_orphans(self):
        module = probe_module
        commands = []

        def docker(arguments, **kwargs):
            commands.append(arguments)
            if "logs" in arguments:
                raise subprocess.TimeoutExpired(arguments, 15)
            if "up" in arguments:
                raise subprocess.CalledProcessError(
                    1, arguments, stderr="kernel blocker"
                )
            return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("subprocess.run", side_effect=docker),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            module.run_stack(Path(directory))
        self.assertTrue(any("up" in command for command in commands))
        self.assertTrue(
            any(
                command[-3:] == ["down", "--volumes", "--remove-orphans"]
                for command in commands
            )
        )


class ComposeProbeSuccessTests(unittest.TestCase):
    def test_probe_checks_stack_and_refuses_success_when_a_volume_leaks(self):
        module = probe_module
        for leaked in (False, True):
            with (
                self.subTest(leaked=leaked),
                tempfile.TemporaryDirectory() as directory,
            ):
                workspace = Path(directory)

                def docker(arguments, workspace=workspace, leaked=leaked, **kwargs):
                    if "up" in arguments:
                        (workspace / "from-container").write_text("dns-db-bind-ok")
                    output = "volume-ok\n" if "GET" in arguments else ""
                    if leaked and arguments[:3] == ["docker", "volume", "ls"]:
                        output = "leftover-volume\n"
                    return subprocess.CompletedProcess(
                        arguments, 0, stdout=output, stderr=""
                    )

                with (
                    patch("subprocess.run", side_effect=docker),
                    patch(
                        "urllib.request.urlopen",
                        return_value=io.BytesIO(b"dns-db-bind-ok"),
                    ),
                ):
                    if leaked:
                        with self.assertRaisesRegex(
                            AssertionError, "Leaked Compose volume"
                        ):
                            module.run_stack(workspace)
                        self.assertFalse((workspace / "stack-result.json").exists())
                    else:
                        result = module.run_stack(workspace)
                        self.assertEqual(result["cleanup"], "ok")
                        self.assertIn("dns", result["checks"])
                        self.assertIn("workspace-bind", result["checks"])
                        self.assertTrue((workspace / "stack-result.json").exists())


class ComposeSupervisorTests(unittest.TestCase):
    def test_daemon_failure_records_the_exact_blocker(self):
        module = runtime_module
        daemon = Mock()
        daemon.poll.return_value = 1

        def launch(*args, **kwargs):
            kwargs["stdout"].write("failed to mount overlay: operation not permitted\n")
            kwargs["stdout"].flush()
            return daemon

        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("subprocess.Popen", side_effect=launch),
                patch(
                    "subprocess.run",
                    return_value=subprocess.CompletedProcess(
                        [], 0, stdout="diagnostics", stderr=""
                    ),
                ),
            ):
                result = module.run_probe(Path(directory))
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["stage"], "daemon")
            self.assertIn(
                "failed to mount overlay: operation not permitted", result["daemonLog"]
            )
            self.assertTrue((Path(directory) / "result.json").exists())


class ComposeSupervisorFailureTests(unittest.TestCase):
    def test_timeout_bytes_are_preserved_in_the_failure_evidence(self):
        module = runtime_module
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(
                    ["sh"],
                    60,
                    output=b"kernel-output\n",
                    stderr=b"+ docker compose version\n",
                ),
            ):
                result = module.run_probe(Path(directory))
            self.assertEqual(result["status"], "error")
            self.assertIn("kernel-output", result["failureLog"])
            self.assertIn("+ docker compose version", result["failureLog"])

    def test_cleanup_timeout_keeps_original_error_and_stops_daemon(self):
        module = runtime_module
        daemon = Mock()
        daemon.poll.return_value = None

        def command(arguments, **kwargs):
            if arguments[:2] == ["docker", "info"]:
                raise OSError("readiness socket failure")
            if "down" in arguments:
                raise subprocess.TimeoutExpired(arguments, 60)
            return subprocess.CompletedProcess(
                arguments, 0, stdout="diagnostics", stderr=""
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "compose-probe").mkdir()
            (root / "compose-probe/compose.yaml").write_text("services: {}")
            with (
                patch.dict(os.environ, {"WORKSPACE": directory}),
                patch("subprocess.Popen", return_value=daemon),
                patch("subprocess.run", side_effect=command),
            ):
                try:
                    result = module.run_probe(root / "evidence")
                except subprocess.TimeoutExpired:
                    self.fail(
                        "Cleanup timeout lost the original blocker and skipped daemon shutdown"
                    )
            self.assertIn("readiness socket failure", result["error"])
            self.assertEqual(result["status"], "error")
            self.assertTrue((root / "evidence/result.json").exists())
            daemon.terminate.assert_called_once()


class ComposeCredentialFailureTests(unittest.TestCase):
    def test_sdk_failure_does_not_leak_credential_error_bodies(self):
        from botocore.exceptions import ClientError

        module = runtime_module
        daemon = Mock()
        daemon.poll.return_value = None
        session = Mock()
        session.get_credentials.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "sdk-private-body"}},
            "Credentials",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def command(arguments, **kwargs):
                if arguments[:2] == ["docker", "info"]:
                    return subprocess.CompletedProcess(
                        arguments,
                        0,
                        stdout='{"Driver":"overlayfs","HttpProxy":"http://user:proxy-private@proxy"}',
                        stderr="",
                    )
                if arguments[:2] == ["codex", "sandbox"]:
                    (Path(kwargs["cwd"]) / "stack-result.json").write_text(
                        json.dumps({"uid": os.getuid(), "cleanup": "ok"})
                    )
                return subprocess.CompletedProcess(arguments, 0, stdout="ok", stderr="")

            with (
                patch.dict(os.environ, {"WORKSPACE": directory}),
                patch("subprocess.Popen", return_value=daemon),
                patch("subprocess.run", side_effect=command),
                patch(
                    "pwd.getpwnam",
                    return_value=SimpleNamespace(
                        pw_uid=os.getuid(), pw_gid=os.getgid(), pw_dir=directory
                    ),
                ),
                patch("os.chown"),
                patch("boto3.Session", return_value=session),
            ):
                try:
                    result = module.run_probe(root / "evidence")
                except ClientError:
                    self.fail(
                        "SDK failure escapes into a raw credential-bearing traceback"
                    )
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["stage"], "codex-exec")
            self.assertEqual(result["error"], "ClientError")
            self.assertNotIn(
                "sdk-private-body", (root / "evidence/result.json").read_text()
            )
            self.assertNotIn(
                "proxy-private", (root / "evidence/result.json").read_text()
            )
            daemon.terminate.assert_called_once()


class ComposeSupervisorSuccessTests(unittest.TestCase):
    def test_factory_sandbox_and_model_exec_both_pass_before_success(self):
        module = runtime_module
        daemon = Mock()
        daemon.poll.return_value = None
        credentials = Mock()
        credentials.get_credentials.return_value = None
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = []
            info_attempts = 0
            clock = 0

            def command(arguments, **kwargs):
                nonlocal info_attempts, clock
                if arguments[0] == "sh":
                    clock = 10
                commands.append(arguments)
                if arguments[:2] == ["codex", "sandbox"] or arguments[:2] == [
                    "codex",
                    "exec",
                ]:
                    self.assertEqual(kwargs["user"], "factory")
                    self.assertEqual(kwargs["extra_groups"], ["docker"])
                    self.assertTrue(
                        Path(kwargs["env"]["CODEX_HOME"]).is_dir(),
                        "Pinned Codex requires its home directory to exist",
                    )
                    workspace = Path(kwargs["cwd"])
                    (workspace / "stack-result.json").write_text(
                        json.dumps({"uid": os.getuid(), "cleanup": "ok"})
                    )
                    return subprocess.CompletedProcess(
                        arguments,
                        0,
                        stdout="compose-probe-ok\n" + "x" * 70000,
                        stderr="",
                    )
                if arguments[:2] == ["docker", "info"]:
                    info_attempts += 1
                    if info_attempts == 1:
                        clock = 15
                        raise subprocess.TimeoutExpired(
                            arguments, 5, output=b"daemon starting\n"
                        )
                    clock = 22
                    return subprocess.CompletedProcess(
                        arguments, 0, stdout='{"Driver":"overlay2"}', stderr=""
                    )
                return subprocess.CompletedProcess(
                    arguments, 0, stdout="diagnostics", stderr=""
                )

            with (
                patch.dict(os.environ, {"WORKSPACE": directory}),
                patch("subprocess.Popen", return_value=daemon),
                patch("subprocess.run", side_effect=command),
                patch(
                    "pwd.getpwnam",
                    return_value=SimpleNamespace(
                        pw_uid=os.getuid(), pw_gid=os.getgid(), pw_dir=directory
                    ),
                ),
                patch("os.chown"),
                patch("boto3.Session", return_value=credentials),
                patch("time.monotonic", side_effect=lambda: clock),
                patch("time.sleep"),
            ):
                result = module.run_probe(root / "evidence")
            self.assertEqual(result["status"], "ok", result)
            self.assertEqual(result["stage"], "complete")
            self.assertEqual(result["daemonStartupSeconds"], 12)
            self.assertEqual(result["elapsedSeconds"], 22)
            self.assertLessEqual(len(result["sandboxLog"]), 65536)
            self.assertLessEqual(len(result["codexLog"]), 65536)
            self.assertTrue(
                any(arguments[:2] == ["git", "init"] for arguments in commands),
                "Codex exec needs a real trusted repository, not an empty .git directory",
            )
            self.assertTrue(
                any(arguments[:2] == ["codex", "sandbox"] for arguments in commands)
            )
            self.assertTrue(
                any(arguments[:2] == ["codex", "exec"] for arguments in commands)
            )
            daemon.terminate.assert_called_once()


class ComposeLiveTests(unittest.TestCase):
    def test_observer_failure_still_confirms_vm_termination(self):
        module = live_module
        requests = []

        def aws(session, service, operation, **parameters):
            requests.append((operation, parameters))
            return {
                "run_microvm": {
                    "microvmId": "mvm-probe",
                    "endpoint": "probe.lambda-microvm.us-east-1.on.aws",
                },
                "get_microvm": {"state": "TERMINATED"},
                "create_microvm_auth_token": {
                    "authToken": {"X-aws-proxy-auth": "private-test-token"}
                },
                "terminate_microvm": {},
            }[operation]

        with tempfile.TemporaryDirectory() as directory:
            with patch("src.tools.live.aws", side_effect=aws):
                result = module.run_trial(
                    None,
                    "software-factory-compose-012345abcdef",
                    "1.0",
                    "runtime-role",
                    1,
                    Path(directory),
                )
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["vmState"], "TERMINATED")
            self.assertTrue(
                any(operation == "terminate_microvm" for operation, _ in requests)
            )
            launch = next(
                parameters
                for operation, parameters in requests
                if operation == "run_microvm"
            )
            self.assertEqual(launch["maximumDurationInSeconds"], 900)
            self.assertEqual(launch["imageVersion"], "1.0")
            self.assertNotIn(
                "private-test-token", (Path(directory) / "trial-1.json").read_text()
            )

    def test_success_collects_evidence_without_changing_launch_identity(self):
        module = live_module
        states = iter(("RUNNING", "TERMINATED"))

        def aws(session, service, operation, **parameters):
            if operation == "get_microvm":
                return {"state": next(states)}
            return {
                "run_microvm": {
                    "microvmId": "mvm-probe",
                    "endpoint": "probe.lambda-microvm.us-east-1.on.aws",
                },
                "create_microvm_auth_token": {
                    "authToken": {"X-aws-proxy-auth": "private-test-token"}
                },
                "terminate_microvm": {},
            }[operation]

        connection = Mock()
        response = io.BytesIO(
            json.dumps(
                {"status": "ok", "stage": "complete", "microvmId": "untrusted-id"}
            ).encode()
        )
        response.status = 200
        connection.getresponse.return_value = response
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("src.tools.live.aws", side_effect=aws),
            patch("http.client.HTTPSConnection", return_value=connection),
            patch(
                "urllib.request.urlopen",
                side_effect=OSError("Unexpected redirect-capable client"),
            ),
        ):
            result = module.run_trial(
                None,
                "software-factory-compose-012345abcdef",
                "1.0",
                "runtime-role",
                1,
                Path(directory),
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["microvmId"], "mvm-probe")
        self.assertEqual(result["vmState"], "TERMINATED")

    def test_local_entrypoint_stages_current_source_without_cloud_access(self):
        module = live_module
        name = "software-factory-compose-" + uuid.uuid4().hex[:12]
        directory = ROOT / "cdk.out" / name
        try:
            with patch("subprocess.run") as docker, patch("src.tools.live.aws") as aws:
                module.main(["--name", name, "--local-only"])
                aws.assert_not_called()
                self.assertEqual(docker.call_count, 2)
                with self.assertRaises(ValueError):
                    module.main(["--name", name, "--local-only"])
            dockerfile = (directory / "factory-source/Dockerfile").read_text()
            self.assertEqual(dockerfile.count("\nCMD "), 1)
            self.assertIn(
                "Docker",
                (directory / "factory-source/image/compose_experiment.py").read_text(),
            )
            self.assertTrue((directory / "source.zip").exists())
        finally:
            if directory.exists():
                shutil.rmtree(directory)

    def test_failed_live_probes_are_not_reported_as_cli_success(self):
        module = live_module
        name = "software-factory-compose-" + uuid.uuid4().hex[:12]
        directory = ROOT / "cdk.out" / name
        launched = []
        terminated = []

        def aws(session, service, operation, **parameters):
            if operation == "put_object":
                self.assertEqual(
                    parameters.get("IfNoneMatch"),
                    "*",
                    "A fresh experiment must not overwrite an existing source object",
                )
            if operation == "run_microvm":
                self.assertEqual(parameters["maximumDurationInSeconds"], 900)
                vm = "mvm-" + str(len(launched) + 1)
                launched.append(vm)
                return {
                    "microvmId": vm,
                    "endpoint": "probe.lambda-microvm.us-east-1.on.aws",
                }
            if operation == "terminate_microvm":
                terminated.append(parameters["microvmIdentifier"])
                return {}
            if operation == "get_microvm":
                return {
                    "state": "TERMINATED"
                    if parameters["microvmIdentifier"] in terminated
                    else "RUNNING"
                }
            if operation == "create_role":
                self.assertTrue(parameters["RoleName"].startswith(name))
                return {
                    "Role": {
                        "Arn": "arn:aws:iam::172318459814:role/"
                        + parameters["RoleName"]
                    }
                }
            if operation == "put_role_policy":
                self.assertTrue(parameters["RoleName"].startswith(name))
                self.assertNotIn("secretsmanager", parameters["PolicyDocument"])
            if operation == "create_microvm_image":
                from botocore.session import Session
                from botocore.validate import validate_parameters

                shape = (
                    Session()
                    .get_service_model("lambda-microvms")
                    .operation_model("CreateMicrovmImage")
                    .input_shape
                )
                validate_parameters(parameters, shape)
                self.assertEqual(parameters["additionalOsCapabilities"], ["ALL"])
                self.assertEqual(parameters["baseImageVersion"], "3")
                self.assertEqual(
                    parameters["resources"], [{"minimumMemoryInMiB": 2048}]
                )
                return {
                    "imageArn": "arn:aws:lambda:us-east-1:172318459814:microvm-image:"
                    + name,
                    "imageVersion": "1.0",
                }
            return {
                "get_caller_identity": {"Account": "172318459814"},
                "list_executions": {"executions": []},
                "list_microvms": {"items": []},
                "describe_stacks": {
                    "Stacks": [
                        {
                            "Outputs": [
                                {
                                    "OutputKey": "ArtifactBucket",
                                    "OutputValue": "experiment-bucket",
                                }
                            ]
                        }
                    ]
                },
                "create_microvm_auth_token": {
                    "authToken": {"X-aws-proxy-auth": "private-test-token"}
                },
                "get_microvm_image_version": {
                    "state": "SUCCESSFUL",
                    "status": "ACTIVE",
                },
            }.get(operation, {})

        connection = Mock()

        def response():
            value = io.BytesIO(
                b'{"status":"error","stage":"daemon","daemonLog":"exact blocker"}'
            )
            value.status = 200
            return value

        connection.getresponse.side_effect = response
        try:
            with (
                patch("subprocess.run"),
                patch("boto3.Session"),
                patch("src.tools.live.aws", side_effect=aws),
                patch("http.client.HTTPSConnection", return_value=connection),
                patch("builtins.input", return_value="yes"),
                patch("time.sleep"),
                self.assertRaises(ValueError),
            ):
                module.main(["--name", name])
            self.assertEqual(len(launched), 2)
            self.assertEqual(terminated, launched)
            self.assertTrue((directory / "resources.json").exists())
            for number in (1, 2):
                self.assertNotIn(
                    "private-test-token",
                    (directory / f"trial-{number}.json").read_text(),
                )
        finally:
            if directory.exists():
                shutil.rmtree(directory)

    def test_deployed_image_is_rejected_before_any_aws_request(self):
        module = live_module
        with patch("src.tools.live.aws") as aws:
            with self.assertRaises(ValueError):
                module.run_trial(
                    None, "software-factory-poc", "7.0", "runtime-role", 1, ROOT
                )
            aws.assert_not_called()


if __name__ == "__main__":
    unittest.main()
