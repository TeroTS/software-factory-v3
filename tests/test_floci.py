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
import test_workflow
from botocore.config import Config
from botocore.exceptions import ClientError

ENDPOINT = "http://127.0.0.1:14566"
INTERNAL = "http://floci:4566"
SECRET = "local-signing-secret"
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("FACTORY_FLOCI_TEST") == "1", "run make floci-test")
class FlociTests(test_workflow.WorkflowFixture):
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
        super().setUp()
        self.name = "issue-" + uuid.uuid4().hex
        self.sqs = self.client("sqs")
        self.lambda_ = self.client("lambda")
        self.pipes = self.client("pipes")
        self.sfn = self.client("stepfunctions")
        self.microvms = self.client("lambda-microvms")
        self.iam = self.client("iam")
        role = self.iam.create_role(
            RoleName=self.name,
            AssumeRolePolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {
                                "Service": [
                                    "lambda.amazonaws.com",
                                    "pipes.amazonaws.com",
                                    "states.amazonaws.com",
                                ]
                            },
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
        )["Role"]["Arn"]
        self.addCleanup(self.iam.delete_role, RoleName=self.name)
        self.dlq = self.sqs.create_queue(QueueName=self.name + "-dlq")["QueueUrl"]
        self.addCleanup(self.sqs.delete_queue, QueueUrl=self.dlq)
        dlq_arn = self.sqs.get_queue_attributes(
            QueueUrl=self.dlq, AttributeNames=["QueueArn"]
        )["Attributes"]["QueueArn"]
        self.queue = self.sqs.create_queue(
            QueueName=self.name,
            Attributes={
                "VisibilityTimeout": "1",
                "RedrivePolicy": json.dumps(
                    {"deadLetterTargetArn": dlq_arn, "maxReceiveCount": 1}
                ),
            },
        )["QueueUrl"]
        self.addCleanup(self.sqs.delete_queue, QueueUrl=self.queue)
        queue_arn = self.sqs.get_queue_attributes(
            QueueUrl=self.queue, AttributeNames=["QueueArn"]
        )["Attributes"]["QueueArn"]
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
                        "JOB_QUEUE_URL": self.queue,
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
            # Keep the returned Function URL's Host routing; connect to the loopback port without external DNS.
            parsed = urlsplit(url)
            self.assertTrue(
                parsed.netloc.endswith(".lambda-url.us-east-1.floci:4566"), url
            )
            self.urls[target] = parsed.netloc
        image = self.microvms.create_microvm_image(
            name=self.name,
            baseImageArn="arn:aws:lambda:us-east-1:aws:microvm-image:al2023-1",
            buildRoleArn=role,
            codeArtifact={"uri": "s3://local-fixture/image.zip"},
        )["imageArn"]
        self.addCleanup(self.microvms.delete_microvm_image, imageIdentifier=image)
        connection = self.client("events").create_connection(
            Name=self.name,
            AuthorizationType="API_KEY",
            AuthParameters={
                "ApiKeyAuthParameters": {
                    "ApiKeyName": "X-Poc-Connection",
                    "ApiKeyValue": "software-factory-poc",
                }
            },
        )["ConnectionArn"]
        events = self.client("events")
        self.addCleanup(events.delete_connection, Name=self.name)
        # Resolve only deployment values in the existing fixture's synthesized ASL. Never replace states or AWS hops.
        self.definition = json.loads(
            json.dumps(self.definition).replace("arn:symbolic-value:", "arn:aws:")
        )
        launch = self.definition["States"]["LaunchVM"]["Parameters"]
        launch.update(ImageIdentifier=image, ImageVersion="1.0", ExecutionRoleArn=role)
        launch["Logging"]["CloudWatch"]["LogGroup"] = "/factory/local"
        self.definition["States"]["PollWorker"]["Parameters"]["InvocationConfig"][
            "ConnectionArn"
        ] = connection
        self.machine = self.sfn.create_state_machine(
            name=self.name,
            roleArn=role,
            type="STANDARD",
            definition=json.dumps(self.definition),
        )["stateMachineArn"]
        self.addCleanup(self.sfn.delete_state_machine, stateMachineArn=self.machine)
        self.pipes.create_pipe(
            Name=self.name,
            RoleArn=role,
            Source=queue_arn,
            Target=self.machine,
            DesiredState="STOPPED",
            SourceParameters={"SqsQueueParameters": {"BatchSize": 1}},
            TargetParameters={
                "InputTemplate": "<$.body>",
                "StepFunctionStateMachineParameters": {
                    "InvocationType": "FIRE_AND_FORGET"
                },
            },
        )
        self.addCleanup(self.pipes.delete_pipe, Name=self.name)
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

    def wait_for(self, observe):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            value = observe()
            if value:
                return value
            time.sleep(0.25)
        self.fail("Floci observation timed out")

    def messages(self, queue):
        return self.sqs.receive_message(QueueUrl=queue, MaxNumberOfMessages=10).get(
            "Messages", []
        )

    def test_signed_post_queues_pinned_job_and_pipe_starts_unchanged_workflow(self):
        self.assertEqual(self.post(binary=True), 202)
        message = self.wait_for(lambda: self.messages(self.queue))[0]
        self.assertEqual(json.loads(message["Body"]), self.job)
        # Manual inspection consumes this first delivery; a second HTTP delivery tests the complete queue handoff.
        self.sqs.delete_message(
            QueueUrl=self.queue, ReceiptHandle=message["ReceiptHandle"]
        )
        self.pipes.start_pipe(Name=self.name)
        self.assertEqual(self.post(), 202)
        execution = self.wait_for(
            lambda: self.sfn.list_executions(stateMachineArn=self.machine)["executions"]
        )[0]
        outcome = self.wait_for(lambda: self.finished(execution["executionArn"]))
        self.assertEqual(json.loads(outcome["input"]), self.job)
        self.assertEqual(outcome["status"], "FAILED")
        history = self.sfn.get_execution_history(
            executionArn=execution["executionArn"]
        )["events"]
        self.assertIn("LaunchVM", json.dumps(history, default=str))
        failures = [
            item["taskFailedEventDetails"]
            for item in history
            if item["type"] == "TaskFailed"
        ]
        self.assertEqual(len(failures), 1)
        self.assertEqual(
            failures[0]["cause"],
            "Unsupported resource: arn:aws:states:::aws-sdk:lambdamicrovms:runMicrovm",
        )
        print("Floci launch failure:", failures[0])
        # This is a compatibility observation, NOT successful worker/publication/termination evidence.
        print("Floci lifecycle gap:", outcome.get("error"), outcome.get("cause"))
        self.assertFalse(
            self.messages(self.dlq),
            "Acknowledged startup must not DLQ a later workflow failure",
        )
        # Probe the documented control-plane limitations directly, not as substitute workflow execution.
        vm = self.microvms.run_microvm(
            imageIdentifier=self.definition["States"]["LaunchVM"]["Parameters"][
                "ImageIdentifier"
            ],
            imageVersion="1.0",
        )
        self.addCleanup(
            self.microvms.terminate_microvm, microvmIdentifier=vm["microvmId"]
        )
        detail = self.microvms.get_microvm(microvmIdentifier=vm["microvmId"])
        self.assertTrue(detail["endpoint"].endswith(".lambda-microvm.us-east-1.on.aws"))
        # The control plane fabricates an AWS-shaped hostname; never connect to it in a local test.
        with self.assertRaises(ClientError) as missing_auth:
            self.microvms.create_microvm_auth_token(
                microvmIdentifier=vm["microvmId"],
                expirationInMinutes=30,
                allowedPorts=[{"port": 8080}],
            )
        self.assertEqual(
            missing_auth.exception.response["ResponseMetadata"]["HTTPStatusCode"], 404
        )

    def finished(self, arn):
        value = self.sfn.describe_execution(executionArn=arn)
        return value if value["status"] != "RUNNING" else None

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
        self.assertFalse(self.messages(self.queue))
        self.assertFalse(
            self.sfn.list_executions(stateMachineArn=self.machine)["executions"]
        )

    def test_queue_redrives_after_one_unacknowledged_receive(self):
        self.assertEqual(self.post(), 202)
        self.assertTrue(self.wait_for(lambda: self.messages(self.queue)))
        time.sleep(1.1)
        self.messages(self.queue)
        message = self.wait_for(lambda: self.messages(self.dlq))[0]
        self.assertEqual(json.loads(message["Body"]), self.job)


if __name__ == "__main__":
    unittest.main()
