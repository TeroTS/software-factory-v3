import hashlib
import hmac
import io
import json
import os
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

    def test_signed_http_receipt_stores_pinned_job_in_dynamodb(self):
        self.assertEqual(self.post(binary=True), 202)
        items = self.jobs()
        self.assertEqual(len(items), 1)
        item = TypeDeserializer().deserialize({"M": items[0]})
        self.assertEqual(item["job"], self.job)
        self.assertEqual(item["status"], "PENDING")
        self.assertEqual(item["PK"], "JOBS")
        self.assertRegex(item["SK"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
        # Receipt-only local test; actual Streams delivery is covered by the opt-in AWS check.

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
