import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import call, patch

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


if __name__ == "__main__":
    unittest.main()
