import http.client
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import test_worker
import test_workflow

from worker.server import HOOK, Runtime, make_server


class JobOutcomeTests(test_worker.WorkerFixture, test_workflow.WorkflowFixture):
    def setUp(self):
        test_worker.WorkerFixture.setUp(self)
        test_workflow.WorkflowFixture.setUp(self)

    def run_outcome(self, scenario):
        if scenario in ("setup-failure", "baseline-verification-failure"):
            script = "setup" if scenario == "setup-failure" else "verify"
            (self.origin / "scripts" / script).write_text(
                "#!/bin/sh\necho github_pat_synthetic_token >&2\nexit 7\n"
            )
            self.git("add", ".")
            self.git("commit", "-m", "failing script fixture")
            self.sha = self.git("rev-parse", "HEAD").strip()
            self.job["baseSha"] = self.sha
        workspace = self.root / scenario
        runtime = Runtime(workspace)
        server = make_server(runtime, ("127.0.0.1", 0))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(runtime.terminate)
        evidence = self.root / "publication.json"
        logs = self.root / "worker.log"
        popen = subprocess.Popen
        terminated = False
        observations = []

        def request(method, path, body=None):
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            try:
                connection.request(method, path, body=body)
                response = connection.getresponse()
                return response.status, response.read()
            finally:
                connection.close()

        def process(args, **kwargs):
            self.assertEqual(args, [sys.executable, "-m", "worker.task"])
            root = Path(__file__).resolve().parents[1]
            # Never forward host AWS/GitHub credentials to the simulated worker.
            kwargs["env"] = {
                "PATH": os.environ["PATH"],
                "HOME": str(self.root),
                "PYTHONPATH": os.pathsep.join((str(root), str(root / "tests"))),
                "AWS_EC2_METADATA_DISABLED": "true",
            }
            return popen(
                [
                    sys.executable,
                    "-m",
                    "worker_process",
                    str(self.origin),
                    str(workspace),
                    scenario,
                    str(evidence),
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                **kwargs,
            )

        def launch(args):
            self.assertEqual(json.loads(args["RunHookPayload"]), self.job)
            with patch("worker.server.subprocess.Popen", side_effect=process):
                self.assertEqual(
                    request(
                        "POST",
                        HOOK + "run",
                        json.dumps(
                            {
                                "microvmId": "microvm-test",
                                "runHookPayload": args["RunHookPayload"],
                            }
                        ),
                    ),
                    (200, b""),
                )
            return self.replies["LaunchVM"][0]

        def poll(args):
            self.assertEqual(args["Headers"]["X-aws-proxy-port"], "8080")
            runtime.process.wait(timeout=30)
            status, body = request("GET", "/status")
            observations.append(json.loads(body))
            return {"StatusCode": status, "ResponseBody": json.loads(body)}

        def terminate(args):
            nonlocal terminated
            self.assertEqual(args["MicrovmIdentifier"], "microvm-test")
            self.assertEqual(request("POST", HOOK + "terminate"), (200, b""))
            terminated = True
            return {}

        def confirm(args):
            self.assertTrue(
                terminated, "A lifecycle observation cannot skip termination"
            )
            self.assertIsNotNone(runtime.process.poll())
            return {"State": "TERMINATED"}

        with logs.open("w") as output:
            kind, result, calls, final = test_workflow.simulate(
                self.definition,
                {
                    **self.replies,
                    "LaunchVM": launch,
                    "PollWorker": poll,
                    "TerminateVM": terminate,
                    "GetTerminationState": confirm,
                },
                job=self.job,
            )
        success = scenario == "success"
        expected_status = "ok" if success else "error"
        self.assertEqual(kind, "Succeed" if success else "Fail")
        self.assertEqual(final, "JobSucceeded" if success else "JobFailed")
        self.assertEqual(result["status"], expected_status)
        self.assertEqual(result["termination"], "TERMINATED")
        self.assertEqual(result["microvmId"], "microvm-test")
        self.assertEqual(observations, [{"status": expected_status}])
        self.assertEqual(
            request("GET", "/status"),
            (200, json.dumps(observations[0], separators=(",", ":")).encode()),
        )
        names = [name for name, _ in calls]
        for state in (
            "LaunchVM",
            "GetWorkerToken",
            "TerminateVM",
            "GetTerminationState",
        ):
            self.assertEqual(names.count(state), 1)
        self.assertLess(names.index("TerminateVM"), names.index("GetTerminationState"))
        publication = json.loads(evidence.read_text())
        before_agent = scenario in ("setup-failure", "baseline-verification-failure")
        self.assertEqual(publication["agent_calls"], 0 if before_agent else 1)
        self.assertEqual(len(publication["prs"]), 0 if before_agent else 1)
        if success:
            pr = publication["prs"][0]
            self.task.verify_pr(
                publication["prs"],
                pr["headRefName"],
                self.git("rev-parse", pr["headRefName"]).strip(),
                "release",
            )
            self.assertIn("Verified repository script", logs.read_text())
        else:
            self.assertIn("Worker failed:", logs.read_text())
            self.assertNotIn("Verified repository script", logs.read_text())
        if before_agent:
            self.assertIn("actual exit code 7", logs.read_text())
        for secret in ("github_pat_synthetic_token", "secret-access", "session-token"):
            self.assertNotIn(secret, logs.read_text())
            self.assertNotIn(secret, json.dumps(result))

    def test_successful_publication_and_confirmed_termination(self):
        self.run_outcome("success")

    def test_setup_failure_and_confirmed_termination(self):
        self.run_outcome("setup-failure")

    def test_baseline_verification_failure_and_confirmed_termination(self):
        self.run_outcome("baseline-verification-failure")

    def test_final_verification_failure_and_confirmed_termination(self):
        self.run_outcome("final-verification-failure")

    def test_uncertain_publication_failure_and_confirmed_termination(self):
        self.run_outcome("publication-failure")


if __name__ == "__main__":
    import unittest

    unittest.main()
