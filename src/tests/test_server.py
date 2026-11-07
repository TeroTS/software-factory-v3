import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from src.worker.server import HOOK, Runtime, make_server, parse_job

JOB = {
    "repository": "example/authorized-project",
    "baseSha": "a" * 40,
    "task": "Update the greeting",
}


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        configuration = patch.dict(
            os.environ,
            {
                "AUTHORIZED_REPOSITORY": JOB["repository"],
                "PR_TARGET_BRANCH": "release",
            },
        )
        configuration.start()
        self.addCleanup(configuration.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.runtime = Runtime(self.directory.name)
        docker = patch.object(self.runtime, "start_docker", create=True)
        self.docker_start = docker.start()
        self.addCleanup(docker.stop)
        identity = patch(
            "src.worker.server.worker_options", return_value={}, create=True
        )
        identity.start()
        self.addCleanup(identity.stop)
        self.server = make_server(self.runtime, ("127.0.0.1", 0))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.runtime.terminate()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        connection.request(method, path, body=body)
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    # Keeps readiness/status checks side-effect-free and reports failed validation,
    # preventing snapshot preparation from starting a job or masking an unusable image.
    def test_build_hooks_and_pending_status_do_not_start_worker(self):
        self.assertEqual(self.request("POST", HOOK + "ready"), (200, b""))
        self.assertEqual(self.request("GET", "/status"), (200, b'{"status":"pending"}'))
        self.assertFalse(self.runtime.started)
        self.docker_start.assert_not_called()
        self.assertEqual(self.request("GET", "/other")[0], 404)
        self.assertEqual(self.request("POST", "/other")[0], 404)
        with patch.object(self.runtime, "validate", side_effect=OSError()):
            self.assertEqual(self.request("POST", HOOK + "validate")[0], 503)

    # Rejects malformed, out-of-scope, or oversized job payloads before execution,
    # enforcing configuration authorization and the run-hook size limit.
    def test_invalid_envelopes_never_start_worker(self):
        for envelope in (
            {},
            {"microvmId": "test", "runHookPayload": "bad"},
            {
                "microvmId": "test",
                "runHookPayload": json.dumps({**JOB, "repository": "other"}),
            },
            {"microvmId": "test", "runHookPayload": json.dumps({**JOB, "task": " "})},
            {
                "microvmId": "test",
                "runHookPayload": json.dumps({**JOB, "baseSha": "short"}),
            },
            {"microvmId": "test", "runHookPayload": "x" * 16385},
        ):
            with self.subTest(envelope=str(envelope)[:40]):
                self.assertEqual(
                    self.request("POST", HOOK + "run", json.dumps(envelope))[0], 400
                )
                self.assertFalse(self.runtime.started)
        self.assertEqual(
            parse_job({"microvmId": "test", "runHookPayload": json.dumps(JOB)}), JOB
        )

    def test_authorization_configuration_fails_closed(self):
        envelope = {"microvmId": "test", "runHookPayload": json.dumps(JOB)}
        for settings in (
            {},
            {"AUTHORIZED_REPOSITORY": JOB["repository"]},
            {"AUTHORIZED_REPOSITORY": "../project", "PR_TARGET_BRANCH": "release"},
            {"AUTHORIZED_REPOSITORY": "example/..", "PR_TARGET_BRANCH": "release"},
            {"AUTHORIZED_REPOSITORY": JOB["repository"], "PR_TARGET_BRANCH": " "},
        ):
            with (
                self.subTest(settings=settings),
                patch.dict(os.environ, settings, clear=True),
                self.assertRaisesRegex(ValueError, "authorization configuration"),
            ):
                parse_job(envelope)
        for repository in (None, [], "example/other", "../project"):
            with self.subTest(repository=repository), self.assertRaises(ValueError):
                parse_job(
                    {
                        **envelope,
                        "runHookPayload": json.dumps({**JOB, "repository": repository}),
                    }
                )

    # Accepts one asynchronous job, rejects a duplicate launch, and preserves its
    # terminal result through termination as required by the stable status contract.
    def test_real_async_process_and_stable_terminal_status(self):
        original = subprocess.Popen

        def process(*args, **kwargs):
            self.assertEqual(args[0], [sys.executable, "-m", "src.worker.task"])
            return original(
                [
                    sys.executable,
                    "-c",
                    "import sys,time; sys.stdin.read(); time.sleep(.1)",
                ],
                **kwargs,
            )

        envelope = json.dumps({"microvmId": "test", "runHookPayload": json.dumps(JOB)})
        with patch("src.worker.server.subprocess.Popen", side_effect=process):
            self.assertEqual(self.request("POST", HOOK + "run", envelope), (200, b""))
        self.assertEqual(self.request("POST", HOOK + "run", envelope)[0], 409)
        self.runtime.process.wait(timeout=5)
        self.assertEqual(self.request("GET", "/status"), (200, b'{"status":"ok"}'))
        self.assertEqual(self.request("POST", HOOK + "terminate"), (200, b""))
        self.assertEqual(self.runtime.status(), "ok")

    # Stops active work and reports interruption as an error, ensuring termination
    # does not leave the worker running or turn incomplete work into success.
    def test_termination_kills_real_worker_process_group(self):
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        self.runtime.process = child
        self.assertEqual(self.runtime.status(), "pending")
        self.runtime.terminate()
        self.assertIsNotNone(child.poll())
        self.assertEqual(self.runtime.status(), "error")

    # Returns a failed run-hook response and terminal error when spawning fails,
    # so an unstarted job cannot appear accepted or remain pending indefinitely.
    def test_process_start_failure_is_terminal_and_not_acknowledged(self):
        with patch("src.worker.server.subprocess.Popen", side_effect=OSError()):
            self.assertEqual(
                self.request(
                    "POST",
                    HOOK + "run",
                    json.dumps(
                        {"microvmId": "test", "runHookPayload": json.dumps(JOB)}
                    ),
                )[0],
                500,
            )
        self.assertEqual(self.runtime.status(), "error")

    def test_docker_is_started_before_factory_worker_is_launched(self):
        from src.worker import server

        events = []
        self.docker_start.side_effect = lambda: events.append("docker-started")
        child = Mock()

        def launch(arguments, **kwargs):
            events.append("worker")
            self.assertEqual(kwargs.get("user"), "factory")
            self.assertEqual(kwargs.get("group"), "factory")
            self.assertEqual(kwargs.get("extra_groups"), ["docker"])
            return child

        with (
            patch(
                "src.worker.server.worker_options",
                return_value={
                    "user": "factory",
                    "group": "factory",
                    "extra_groups": ["docker"],
                },
                create=True,
            ),
            patch("src.worker.server.subprocess.Popen", side_effect=launch),
        ):
            self.runtime.start(JOB)
        self.assertEqual(events, ["docker-started", "worker"])
        self.runtime.process = None
        self.assertTrue(callable(getattr(server, "worker_options", None)))

    def test_docker_start_failure_does_not_launch_worker(self):
        self.docker_start.side_effect = OSError("Docker unavailable")
        with patch("src.worker.server.subprocess.Popen") as worker:
            code = self.request(
                "POST",
                HOOK + "run",
                json.dumps({"microvmId": "test", "runHookPayload": json.dumps(JOB)}),
            )[0]
            self.runtime.process = None
            self.assertEqual(code, 500)
        worker.assert_not_called()
        self.assertEqual(self.runtime.status(), "error")

    # Exercises credential-free sandbox validation and rejects missing tools,
    # keeping the snapshot workspace clean and validation independent of secrets.
    def test_validation_removes_probe_and_never_supplies_credentials(self):
        def probe(args, **kwargs):
            self.assertEqual(
                set(kwargs["env"]),
                {"PATH", "HOME", "CODEX_HOME", "PYTHONDONTWRITEBYTECODE"},
            )
            self.assertTrue(Path(kwargs["env"]["CODEX_HOME"]).is_dir())
            self.assertIn('sandbox_mode="workspace-write"', args)
            self.assertEqual(args[args.index("--") + 1], sys.executable)
            return subprocess.CompletedProcess(args, 0, "sandbox-validation-ok\n", "")

        with (
            patch("src.worker.server.shutil.which", return_value="tool") as which,
            patch("src.worker.server.subprocess.run", side_effect=probe),
        ):
            self.runtime.validate()
        self.assertIn(call(sys.executable), which.call_args_list)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])
        with (
            patch("src.worker.server.shutil.which", return_value=None),
            self.assertRaises(OSError),
        ):
            self.runtime.validate()


class DockerLifecycleTests(unittest.TestCase):
    def test_root_image_validation_runs_sandbox_as_factory(self):
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(directory)

            def probe(args, **kwargs):
                self.assertEqual(kwargs.get("user"), "factory")
                self.assertEqual(kwargs.get("extra_groups"), ["docker"])
                return subprocess.CompletedProcess(
                    args, 0, "sandbox-validation-ok\n", ""
                )

            with (
                patch("src.worker.server.os.geteuid", return_value=0),
                patch(
                    "src.worker.server.pwd.getpwnam",
                    return_value=SimpleNamespace(pw_uid=1000, pw_gid=1000),
                ),
                patch("src.worker.server.os.chown") as ownership,
                patch("src.worker.server.shutil.which", return_value="tool") as which,
                patch("src.worker.server.subprocess.run", side_effect=probe),
            ):
                runtime.validate()
            self.assertEqual(ownership.call_count, 2)
            self.assertIn(call("dockerd"), which.call_args_list)
            self.assertIn(call("docker"), which.call_args_list)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_worker_waits_for_docker_before_credentials_and_setup(self):
        from src.worker import task

        with (
            patch.dict(
                os.environ,
                {
                    "AUTHORIZED_REPOSITORY": JOB["repository"],
                    "PR_TARGET_BRANCH": "release",
                },
            ),
            patch.object(
                task,
                "wait_for_docker",
                side_effect=OSError("Docker unavailable"),
                create=True,
            ) as readiness,
            patch.object(
                task,
                "prepare_environment",
                side_effect=OSError("Credentials unavailable"),
            ) as environment,
            self.assertRaises(OSError),
        ):
            task.execute(JOB)
        readiness.assert_called_once()
        environment.assert_not_called()

    def test_daemon_start_does_not_wait_inside_run_hook(self):
        runtime = Runtime("/workspace")
        self.assertTrue(
            callable(getattr(runtime, "start_docker", None)),
            "Missing post-resume Docker startup",
        )
        daemon = Mock()
        daemon.poll.return_value = None
        with (
            patch("src.worker.server.subprocess.Popen", return_value=daemon) as launch,
            patch(
                "src.worker.server.subprocess.run",
                side_effect=[
                    subprocess.TimeoutExpired("docker", 5),
                    subprocess.CompletedProcess(["docker", "info"], 0),
                ],
            ) as info,
            patch("src.worker.server.time.sleep"),
        ):
            runtime.start_docker()
        self.assertEqual(info.call_count, 0)
        self.assertEqual(
            launch.call_args.args[0],
            ["dockerd", "--host=unix:///var/run/docker.sock", "--group=docker"],
        )
        self.assertTrue(launch.call_args.kwargs["start_new_session"])
        self.assertIs(runtime.docker, daemon)

    def test_readiness_retries_and_deadline_fails(self):
        from src.worker import server

        self.assertTrue(
            callable(getattr(server, "wait_for_docker", None)),
            "Missing worker Docker readiness gate",
        )
        with (
            patch(
                "src.worker.server.subprocess.run",
                side_effect=[
                    subprocess.TimeoutExpired("docker", 5),
                    subprocess.CompletedProcess(["docker", "info"], 0),
                ],
            ) as info,
            patch("src.worker.server.time.sleep"),
        ):
            server.wait_for_docker()
        self.assertEqual(info.call_count, 2)
        with (
            patch("src.worker.server.time.monotonic", side_effect=[0, 46]),
            self.assertRaises(OSError),
        ):
            server.wait_for_docker()

    def test_termination_escalates_docker_within_hook_budget(self):
        runtime = Runtime("/workspace")
        runtime.docker = Mock(pid=123)
        runtime.docker.wait.side_effect = [subprocess.TimeoutExpired("dockerd", 2), 0]
        with patch("src.worker.server.os.killpg") as kill:
            runtime.terminate()
        self.assertEqual(kill.call_count, 2)
        self.assertEqual(
            runtime.docker.wait.call_args_list, [call(timeout=2), call(timeout=2)]
        )

    def test_root_supervisor_drops_worker_privileges(self):
        from src.worker import server

        self.assertTrue(
            callable(getattr(server, "worker_options", None)),
            "Missing factory privilege drop",
        )
        with patch("src.worker.server.os.geteuid", return_value=0):
            self.assertEqual(
                server.worker_options(),
                {"user": "factory", "group": "factory", "extra_groups": ["docker"]},
            )
        with patch("src.worker.server.os.geteuid", return_value=1000):
            self.assertEqual(server.worker_options(), {})


if __name__ == "__main__":
    unittest.main()
