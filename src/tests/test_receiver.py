import base64
import copy
import hashlib
import hmac
import json
import os
import unittest
from datetime import UTC, datetime
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

from src.receiver.handler import handle


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "WEBHOOK_SECRET": "signing-secret",
                "AUTHORIZED_REPOSITORY": "example/project",
                "PR_TARGET_BRANCH": "release/next",
                "JOB_TABLE_NAME": "jobs",
                "GITHUB_SECRET_ARN": "scoped-token",
            },
            clear=True,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.payload = {
            "action": "opened",
            "repository": {"full_name": "example/project"},
            "issue": {
                "title": 'Change "greeting" — café',
                "body": "Test.\nKeep \\ paths.",
            },
        }
        self.job = {
            "repository": "example/project",
            "baseSha": "b" * 40,
            "task": 'Change "greeting" — café\n\nTest.\nKeep \\ paths.',
        }
        self.session = Mock()
        self.secrets = Mock()
        self.secrets.get_secret_value.return_value = {"SecretString": "private-token"}
        self.table = Mock()
        self.session.client.side_effect = lambda service, **kwargs: {
            "secretsmanager": self.secrets,
            "dynamodb": self.table,
        }[service]
        session = patch("src.receiver.handler.boto3.Session", return_value=self.session)
        session.start()
        self.addCleanup(session.stop)
        self.connection = Mock()
        self.connection.getresponse.return_value.status = 200
        self.connection.getresponse.return_value.read.return_value = json.dumps(
            {"sha": "b" * 40}
        ).encode()
        connection = patch(
            "http.client.HTTPSConnection",
            return_value=self.connection,
        )
        connection.start()
        self.addCleanup(connection.stop)

    def event(self, payload=None, binary=False):
        raw = json.dumps(
            self.payload if payload is None else payload, ensure_ascii=False
        ).encode()
        return {
            "requestContext": {"http": {"method": "POST"}},
            "headers": {
                "X-GitHub-Event": "issues",
                "X-Hub-Signature-256": "sha256="
                + hmac.new(b"signing-secret", raw, hashlib.sha256).hexdigest(),
            },
            "body": base64.b64encode(raw).decode() if binary else raw.decode(),
            "isBase64Encoded": binary,
        }

    def test_signed_receipt_stores_bounded_pinned_payload(self):
        result = handle(self.event(binary=True), None)
        self.assertEqual(result["statusCode"], 202)
        request = self.table.put_item.call_args.kwargs
        self.assertEqual(request["TableName"], "jobs")
        item = request["Item"]
        self.assertEqual(item["PK"], {"S": "JOBS"})
        self.assertRegex(
            item["SK"]["S"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"
        )
        self.assertEqual(item["status"], {"S": "PENDING"})
        self.assertEqual(
            item["job"], {"M": {key: {"S": value} for key, value in self.job.items()}}
        )
        self.assertEqual(set(item), {"PK", "SK", "status", "job"})
        self.assertEqual(
            request["ConditionExpression"],
            "attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
        self.secrets.get_secret_value.assert_called_once_with(SecretId="scoped-token")
        self.assertEqual(
            self.connection.request.call_args.args[:2],
            ("GET", "/repos/example/project/commits/release%2Fnext"),
        )

    def test_timestamp_collision_preserves_original_and_rejects_new_job(self):
        stored = {}

        def insert(**request):
            item = request["Item"]
            key = (item["PK"]["S"], item["SK"]["S"])
            if (
                key in stored
                and request.get("ConditionExpression")
                == "attribute_not_exists(PK) AND attribute_not_exists(SK)"
            ):
                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
                )
            stored[key] = copy.deepcopy(item)

        self.table.put_item.side_effect = insert
        with patch("src.receiver.handler.datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 5, 12, 0, 0, 123, UTC)
            self.assertEqual(handle(self.event(), None)["statusCode"], 202)
            original = copy.deepcopy(stored)
            changed = {
                **self.payload,
                "issue": {"title": "Different Job", "body": None},
            }
            rejected = handle(self.event(changed), None)
        self.assertEqual(rejected["statusCode"], 502)
        self.assertEqual(stored, original)
        self.assertEqual(len(stored), 1)
        self.assertIn(("JOBS", "2026-11-05T12:00:00.000123Z"), stored)
        self.assertEqual(self.table.put_item.call_count, 2)
        self.assertNotIn("private-token", rejected["body"])

    def test_rejections_and_ignored_events_do_not_access_aws(self):
        cases = []
        for headers, status in (
            ({"X-Hub-Signature-256": "bad"}, 403),
            ({"X-GitHub-Event": "push"}, 200),
        ):
            event = self.event()
            event["headers"].update(headers)
            cases.append((event, status))
        for payload, status in (
            ([], 400),
            ({**self.payload, "action": "edited"}, 200),
            ({**self.payload, "repository": {"full_name": "other/project"}}, 403),
            ({**self.payload, "issue": None}, 400),
            ({**self.payload, "issue": {"title": "", "body": None}}, 400),
            ({**self.payload, "issue": {"title": "Title", "body": False}}, 400),
            ({**self.payload, "issue": {"title": "Title", "body": "é" * 9000}}, 400),
            ({**self.payload, "issue": {"title": "PR", "pull_request": {}}}, 200),
        ):
            cases.append((self.event(payload), status))
        for event, status in cases:
            with self.subTest(status=status, event=event):
                self.assertEqual(handle(event, None)["statusCode"], status)
        self.session.client.assert_not_called()

    def test_null_or_empty_body_captures_title_only(self):
        for body in (None, ""):
            with self.subTest(body=body):
                event = self.event(
                    {**self.payload, "issue": {"title": "Title", "body": body}}
                )
                self.assertEqual(handle(event, None)["statusCode"], 202)
                self.assertEqual(
                    self.table.put_item.call_args.kwargs["Item"]["job"]["M"]["task"],
                    {"S": "Title"},
                )

    def test_method_and_configuration_failures_precede_authentication(self):
        event = self.event()
        event["headers"]["X-Hub-Signature-256"] = "bad"
        event["requestContext"]["http"]["method"] = "GET"
        result = handle(event, None)
        self.assertEqual(result["statusCode"], 405)
        self.assertEqual(json.loads(result["body"])["message"], "POST required")
        event["requestContext"]["http"]["method"] = "POST"
        for name, value in (
            ("WEBHOOK_SECRET", ""),
            ("AUTHORIZED_REPOSITORY", "example/.."),
            ("PR_TARGET_BRANCH", " "),
            ("JOB_TABLE_NAME", ""),
            ("GITHUB_SECRET_ARN", ""),
            ("LOCAL_AWS_ENDPOINT", "http://unexpected"),
            ("LOCAL_GITHUB_ENDPOINT", "http://unexpected"),
        ):
            with self.subTest(name=name), patch.dict(os.environ, {name: value}):
                result = handle(event, None)
                self.assertEqual(result["statusCode"], 500)
                self.assertEqual(
                    json.loads(result["body"])["message"],
                    "Receiver configuration unavailable",
                )
        self.session.client.assert_not_called()

    def test_malformed_bodies_and_signatures_do_not_access_aws(self):
        cases = []
        for body, binary in ((None, False), ("!", True), ("\ud800", False)):
            event = self.event()
            event.update(body=body, isBase64Encoded=binary)
            cases.append((event, 400, "Invalid request body"))
        for signature in (None, "sha256=é"):
            event = self.event()
            event["headers"]["X-Hub-Signature-256"] = signature
            cases.append((event, 403, "Invalid signature"))
        event = self.event()
        event["body"] = "{"
        event["headers"]["X-Hub-Signature-256"] = (
            "sha256=" + hmac.new(b"signing-secret", b"{", hashlib.sha256).hexdigest()
        )
        cases.append((event, 400, "Invalid issue payload"))
        for event, status, message in cases:
            with self.subTest(status=status, message=message):
                result = handle(event, None)
                self.assertEqual(result["statusCode"], status)
                self.assertEqual(json.loads(result["body"])["message"], message)
        self.session.client.assert_not_called()

    def test_invalid_baseline_does_not_enqueue(self):
        for token in (None, " "):
            with self.subTest(token=token):
                self.secrets.get_secret_value.return_value = {"SecretString": token}
                result = handle(self.event(), None)
                self.assertEqual(result["statusCode"], 502)
                self.assertEqual(
                    json.loads(result["body"])["message"], "Baseline resolution failed"
                )
        self.connection.request.assert_not_called()
        self.secrets.get_secret_value.return_value = {"SecretString": "private-token"}
        for sha in (None, "not-a-sha", "b" * 39):
            with self.subTest(sha=sha):
                self.connection.getresponse.return_value.read.return_value = json.dumps(
                    {"sha": sha}
                ).encode()
                result = handle(self.event(), None)
                self.assertEqual(result["statusCode"], 502)
                self.assertEqual(
                    json.loads(result["body"])["message"], "Baseline resolution failed"
                )
        self.table.put_item.assert_not_called()

    def test_baseline_exceptions_preserve_safe_preparation_error(self):
        self.connection.request.side_effect = OSError("private-token")
        result = handle(self.event(), None)
        self.assertEqual(result["statusCode"], 502)
        self.assertEqual(
            json.loads(result["body"])["message"],
            "Job preparation or enqueue failed",
        )
        self.assertNotIn("private-token", result["body"])
        self.table.put_item.assert_not_called()
        self.secrets.close.assert_called_once()
        self.connection.close.assert_called_once()

    def test_baseline_and_storage_failures_are_safe(self):
        self.connection.getresponse.return_value.status = 404
        self.assertEqual(handle(self.event(), None)["statusCode"], 502)
        self.table.put_item.assert_not_called()
        self.connection.getresponse.return_value.status = 200
        self.table.put_item.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "private-token"}},
            "PutItem",
        )
        result = handle(self.event(), None)
        self.assertEqual(result["statusCode"], 502)
        self.assertNotIn("private-token", result["body"])


if __name__ == "__main__":
    unittest.main()
