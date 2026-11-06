import hashlib
import hmac
import io
import json
import os
import time
import unittest
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import boto3
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config

ENDPOINT = "http://127.0.0.1:14566"
INTERNAL = "http://floci:4566"
SECRET = "local-signing-secret"
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("FACTORY_FLOCI_TEST") == "1", "run make floci-test")
class FlociTests(unittest.TestCase):
    def client(self, service):
        client = boto3.Session().client(
            service,
            endpoint_url=ENDPOINT,
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
            config=Config(
                connect_timeout=5,
                read_timeout=90,
                retries={"total_max_attempts": 1},
                proxies={},
            ),
        )
        self.addCleanup(client.close)
        return client

    def setUp(self):
        self.name = "issue-" + uuid.uuid4().hex
        self.table = self.client("dynamodb")
        self.table.create_table(
            TableName=self.name,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
            StreamSpecification={"StreamEnabled": True, "StreamViewType": "NEW_IMAGE"},
        )
        self.addCleanup(self.table.delete_table, TableName=self.name)
        self.lambda_ = self.client("lambda")
        iam = self.client("iam")
        role = iam.create_role(
            RoleName=self.name,
            AssumeRolePolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
        )["Role"]["Arn"]
        self.addCleanup(iam.delete_role, RoleName=self.name)
        secrets = self.client("secretsmanager")
        token = secrets.create_secret(
            Name=self.name, SecretString="local-github-token"
        )["ARN"]
        self.addCleanup(
            secrets.delete_secret, SecretId=token, ForceDeleteWithoutRecovery=True
        )
        with io.BytesIO() as data:
            with zipfile.ZipFile(data, "w") as archive:
                for path in (ROOT / "receiver").glob("*.py"):
                    archive.write(path, path.name)
            code = data.getvalue()
        self.urls = {}
        for target in ("release", "missing", "unpaired-local"):
            name = self.name + "-" + target
            self.lambda_.create_function(
                FunctionName=name,
                Runtime="python3.13",
                Role=role,
                Handler="handler.handle",
                Code={"ZipFile": code},
                Timeout=30,
                Environment={
                    "Variables": {
                        "AUTHORIZED_REPOSITORY": "example/authorized-project",
                        "PR_TARGET_BRANCH": "release"
                        if target == "unpaired-local"
                        else target,
                        "GITHUB_SECRET_ARN": token,
                        "WEBHOOK_SECRET": SECRET,
                        "JOB_TABLE_NAME": self.name,
                        "LOCAL_AWS_ENDPOINT": INTERNAL,
                        **(
                            {"LOCAL_GITHUB_ENDPOINT": "http://github:8080"}
                            if target != "unpaired-local"
                            else {}
                        ),
                    }
                },
            )
            self.addCleanup(self.lambda_.delete_function, FunctionName=name)
            url = self.lambda_.create_function_url_config(
                FunctionName=name, AuthType="NONE"
            )["FunctionUrl"]
            parsed = urlsplit(url)
            self.assertTrue(
                parsed.netloc.endswith(".lambda-url.us-east-1.floci:4566"), url
            )
            self.urls[target] = parsed.netloc
        self.payload = {
            "action": "opened",
            "repository": {"full_name": "example/authorized-project"},
            "issue": {
                "title": "Update greeting — café",
                "body": "Include a test.\nKeep scripts working.",
            },
        }
        self.job = {
            "repository": "example/authorized-project",
            "baseSha": "b" * 40,
            "task": "Update greeting — café\n\nInclude a test.\nKeep scripts working.",
        }

    def post(
        self,
        payload=None,
        *,
        signature=True,
        tamper=False,
        event="issues",
        method="POST",
        target="release",
        binary=False,
        raw=None,
    ):
        body = (
            raw
            if raw is not None
            else json.dumps(
                self.payload if payload is None else payload, ensure_ascii=False
            ).encode()
        )
        headers = {
            "Content-Type": "application/octet-stream"
            if binary
            else "application/json",
            "X-GitHub-Event": event,
        }
        if signature:
            headers["X-Hub-Signature-256"] = (
                signature
                if isinstance(signature, str)
                else "sha256="
                + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
            )
        if tamper:
            body += b" "
        # Route the returned Function URL through loopback without external DNS.
        headers["Host"] = self.urls[target]
        request = urllib.request.Request(
            ENDPOINT + "/", data=body, headers=headers, method=method
        )
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                request, timeout=90
            ) as response:
                return response.status
        except urllib.error.HTTPError as error:
            with error:
                return error.code

    def jobs(self):
        return self.table.query(
            TableName=self.name,
            KeyConditionExpression="PK = :partition",
            ExpressionAttributeValues={":partition": {"S": "JOBS"}},
            ConsistentRead=True,
        )["Items"]

    def start_handoff(self):
        from src.tests.test_workflow import WorkflowFixture, entry_definition

        self.fixture = WorkflowFixture()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.machine = self.client("stepfunctions")
        role = self.lambda_.get_function_configuration(
            FunctionName=self.name + "-release"
        )["Role"]
        # Execute real entry states locally; VM/HTTP lifecycle tasks use the existing simulator.
        self.machine_arn = self.machine.create_state_machine(
            name=self.name,
            roleArn=role,
            type="STANDARD",
            definition=json.dumps(entry_definition(self.fixture.definition)),
        )["stateMachineArn"]
        self.addCleanup(
            self.machine.delete_state_machine, stateMachineArn=self.machine_arn
        )
        name = self.name + "-dispatcher"
        self.logs = self.client("logs")
        self.log_group = "/aws/lambda/" + name
        self.logs.create_log_group(logGroupName=self.log_group)
        self.addCleanup(self.logs.delete_log_group, logGroupName=self.log_group)
        with io.BytesIO() as data:
            with zipfile.ZipFile(data, "w") as archive:
                for path in (ROOT / "dispatcher").glob("*.py"):
                    archive.write(path, path.name)
                archive.write(ROOT / "tests" / "dispatch_observer.py", "observer.py")
            code = data.getvalue()
        self.lambda_.create_function(
            FunctionName=name,
            Runtime="python3.13",
            Role=role,
            Handler="observer.handle",
            Code={"ZipFile": code},
            Timeout=30,
            Environment={
                "Variables": {
                    "JOB_TABLE_NAME": self.name,
                    "STATE_MACHINE_ARN": self.machine_arn,
                    "AWS_ENDPOINT_URL": INTERNAL,
                    "AWS_ACCESS_KEY_ID": "test",
                    "AWS_SECRET_ACCESS_KEY": "test",
                    "AWS_EC2_METADATA_DISABLED": "true",
                }
            },
        )
        self.addCleanup(self.lambda_.delete_function, FunctionName=name)
        stream = self.table.describe_table(TableName=self.name)["Table"][
            "LatestStreamArn"
        ]
        mapping = self.lambda_.create_event_source_mapping(
            FunctionName=name,
            EventSourceArn=stream,
            StartingPosition="TRIM_HORIZON",
            FilterCriteria=self.fixture.filters,
        )
        self.addCleanup(self.lambda_.delete_event_source_mapping, UUID=mapping["UUID"])

    def wait_for(self, observe):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            result = observe()
            if result:
                return result
            time.sleep(0.25)
        self.fail(
            "Local Streams/workflow observation timed out; no manual dispatch fallback"
        )

    def executions(self):
        return self.machine.list_executions(stateMachineArn=self.machine_arn)[
            "executions"
        ]

    def finished(self, arn):
        execution = self.machine.describe_execution(executionArn=arn)
        return execution if execution["status"] != "RUNNING" else None

    def reference(self, item):
        return {name: item[name]["S"] for name in ("PK", "SK")}

    def execution_for(self, item):
        for execution in self.executions():
            detail = self.finished(execution["executionArn"])
            if detail and json.loads(detail["input"]).get("queue") == self.reference(
                item
            ):
                self.assertEqual(detail["status"], "SUCCEEDED")
                return detail
        return None

    def notifications(self):
        events = self.logs.filter_log_events(logGroupName=self.log_group)["events"]
        return [
            json.loads(event["message"].split("HANDOFF_RECORD ", 1)[1])
            for event in events
            if "HANDOFF_RECORD " in event["message"]
        ]

    def assert_entry_history(self, arn, entry):
        history = self.machine.get_execution_history(executionArn=arn)["events"]
        names = [
            event["stateEnteredEventDetails"]["name"]
            for event in history
            if "stateEnteredEventDetails" in event
        ]
        self.assertIn("CheckSubmission", names)
        self.assertIn(entry, names)

    def complete(self, request):
        # Apply only the simulated workflow's post-termination task to the local table.
        self.table.update_item(**{**request, "TableName": self.name})
        return {}

    def test_signed_http_receipt_stores_pinned_job_in_dynamodb(self):
        self.assertEqual(self.post(binary=True), 202)
        items = self.jobs()
        self.assertEqual(len(items), 1)
        item = TypeDeserializer().deserialize({"M": items[0]})
        self.assertEqual(item["job"], self.job)
        self.assertEqual(item["status"], "PENDING")
        self.assertEqual(item["PK"], "JOBS")
        self.assertRegex(item["SK"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
        # Receipt-only case; the handoff cases below exercise local Streams delivery.

    def test_stream_handoff_orders_three_signed_jobs_and_releases_after_cleanup(self):
        from src.tests.test_workflow import simulate

        self.start_handoff()
        for index in range(3):
            payload = {
                **self.payload,
                "issue": {
                    "title": f'Job {index}: "quotes" — café',
                    "body": "Backslash: \\; newline:\n東京.",
                    # GitHub creation order must not replace factory enqueue order.
                    "created_at": f"2026-01-0{3 - index}T00:00:00Z",
                },
            }
            self.assertEqual(self.post(payload), 202)
            if index == 0:
                self.wait_for(lambda: len(self.executions()) == 1)
            self.wait_for(lambda index=index: len(self.notifications()) == index + 1)
        items = self.jobs()
        self.assertEqual(len(items), 3)
        self.assertEqual(
            [item["status"]["S"] for item in items],
            ["IN_PROGRESS", "PENDING", "PENDING"],
        )
        self.assertEqual(len(self.executions()), 1)
        for index, item in enumerate(items):
            execution = self.wait_for(lambda item=item: self.execution_for(item))
            job = TypeDeserializer().deserialize(item["job"])
            self.assertEqual(job["baseSha"], "b" * 40)
            self.assertEqual(
                job["task"],
                f'Job {index}: "quotes" — café\n\nBackslash: \\; newline:\n東京.',
            )
            submission = json.loads(execution["input"])
            self.assertEqual(submission, {"jobs": [job], "queue": self.reference(item)})
            self.assertEqual(
                json.loads(execution["output"]),
                {"job": job, "queue": self.reference(item)},
            )
            self.assert_entry_history(execution["executionArn"], "QueuedInput")
            kind, _, calls, final = simulate(
                self.fixture.definition,
                {**self.fixture.replies, "CompleteQueuedJob": self.complete},
                job=submission,
            )
            self.assertEqual((kind, final), ("Succeed", "JobSucceeded"))
            self.assertEqual(json.loads(calls[0][1]["RunHookPayload"]), job)
            self.assertEqual(
                [name for name, _ in calls][-2:],
                ["GetTerminationState", "CompleteQueuedJob"],
            )
            self.wait_for(lambda index=index: len(self.notifications()) == 4 + index)
            if index < 2:
                self.wait_for(lambda index=index: len(self.executions()) == index + 2)
        self.assertEqual(
            [item["status"]["S"] for item in self.jobs()], ["COMPLETED"] * 3
        )
        self.assertEqual(len(self.executions()), 3)
        before = self.jobs()
        direct = self.machine.start_execution(
            stateMachineArn=self.machine_arn, input=json.dumps([self.job])
        )["executionArn"]
        execution = self.wait_for(lambda: self.finished(direct))
        self.assertEqual(json.loads(execution["output"]), {"job": self.job})
        self.assert_entry_history(direct, "Input")
        _, _, calls, _ = simulate(
            self.fixture.definition, self.fixture.replies, job=[self.job]
        )
        self.assertNotIn("CompleteQueuedJob", [name for name, _ in calls])
        self.assertEqual(self.jobs(), before)

    def test_failed_or_unconfirmed_cleanup_blocks_later_signed_jobs(self):
        from src.tests.test_workflow import TaskError, simulate

        self.start_handoff()
        self.assertEqual(self.post(), 202)
        self.wait_for(lambda: len(self.executions()) == 1)
        self.wait_for(lambda: len(self.notifications()) == 1)
        item = self.jobs()[0]
        execution = self.wait_for(lambda: self.execution_for(item))
        for count in (2, 3):
            self.assertEqual(self.post(), 202)
            self.wait_for(lambda count=count: len(self.notifications()) == count)
        self.assertEqual(len(self.executions()), 1)
        for index, (state, replies, outcome, termination) in enumerate(
            (
                (
                    "PollWorker",
                    [{"StatusCode": 200, "ResponseBody": {"status": "error"}}],
                    "Fail",
                    "TERMINATED",
                ),
                (
                    "GetTerminationState",
                    [{"State": "TERMINATING"}] * 340,
                    "TIMED_OUT",
                    None,
                ),
                ("TerminateVM", [TaskError()], "Fail", None),
                ("GetTerminationState", [{"State": "RUNNING"}], "Fail", None),
            ),
            start=1,
        ):
            with self.subTest(state=state, outcome=outcome):
                kind, output, calls, _ = simulate(
                    self.fixture.definition,
                    {
                        **self.fixture.replies,
                        state: replies,
                        "CompleteQueuedJob": self.complete,
                    },
                    limit=1500,
                    job=json.loads(execution["input"]),
                )
                self.assertEqual(kind, outcome)
                self.assertEqual(output.get("termination"), termination)
                self.assertIn("TerminateVM", [name for name, _ in calls])
                self.assertNotIn("CompleteQueuedJob", [name for name, _ in calls])
                if termination:
                    self.assertEqual(output["error"], "JobFailed")
                    self.assertEqual(output["status"], "error")
                elif outcome == "Fail":
                    self.assertEqual(output["error"], "CleanupUnconfirmed")
                # A fresh signed INSERT is an ordinary dispatch attempt, not a manual Lambda call.
                self.assertEqual(self.post(), 202)
                self.wait_for(
                    lambda index=index: len(self.notifications()) == index + 3
                )
                self.assertEqual(len(self.executions()), 1)
                self.assertEqual(
                    [job["status"]["S"] for job in self.jobs()],
                    ["IN_PROGRESS"] + ["PENDING"] * (index + 2),
                )

    def test_rejections_enqueue_no_job(self):
        cases = [
            ({"signature": False}, 403),
            ({"tamper": True}, 403),
            ({"signature": "sha256=" + "0" * 64}, 403),
            ({"raw": b"not JSON"}, 400),
            ({"raw": b"[]"}, 400),
            ({"payload": {**self.payload, "issue": None}}, 400),
            (
                {
                    "payload": {
                        **self.payload,
                        "issue": {"title": "Title", "body": False},
                    }
                },
                400,
            ),
            (
                {
                    "payload": {
                        **self.payload,
                        "issue": {"title": "Title", "body": "é" * 9000},
                    }
                },
                400,
            ),
            ({"method": "PUT"}, 405),
            ({"event": "push"}, 200),
            ({"event": "pull_request"}, 200),
            ({"payload": {**self.payload, "action": "edited"}}, 200),
            (
                {
                    "payload": {
                        **self.payload,
                        "repository": {"full_name": "other/repository"},
                    }
                },
                403,
            ),
            (
                {
                    "payload": {
                        **self.payload,
                        "issue": {**self.payload["issue"], "pull_request": {}},
                    }
                },
                200,
            ),
            ({"payload": {**self.payload, "issue": {"title": "", "body": None}}}, 400),
            ({"target": "missing"}, 502),
            ({"target": "unpaired-local"}, 500),
        ]
        for arguments, status in cases:
            with self.subTest(arguments=arguments):
                self.assertEqual(self.post(**arguments), status)
        self.assertFalse(self.jobs())


if __name__ == "__main__":
    unittest.main()
