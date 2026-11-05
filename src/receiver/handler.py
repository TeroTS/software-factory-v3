# Authenticate GitHub issue deliveries, resolve a deployment-owned baseline, and queue one bounded Job; never log payloads or credentials.
import base64
import hashlib
import hmac
import http.client
import json
import os
import re
from contextlib import closing
from datetime import UTC, datetime
from urllib.parse import quote

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

AWS_CONFIG = Config(
    connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 1}
)


# Return only a fixed HTTP status/message; no exception details, task content, or tokens cross this boundary.
def response(status, message):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"message": message}),
    }


# Handle the Function URL v2 envelope; reject unauthorized or malformed requests before any executable Job is sent.
def handle(event, context):
    if event.get("requestContext", {}).get("http", {}).get("method") != "POST":
        return response(405, "POST required")
    secret = os.environ.get("WEBHOOK_SECRET", "")
    repository = os.environ.get("AUTHORIZED_REPOSITORY", "")
    target = os.environ.get("PR_TARGET_BRANCH", "")
    table = os.environ.get("JOB_TABLE_NAME", "")
    token_arn = os.environ.get("GITHUB_SECRET_ARN", "")
    local = os.environ.get("LOCAL_AWS_ENDPOINT")
    github_local = os.environ.get("LOCAL_GITHUB_ENDPOINT")
    if (
        not secret
        or not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repository)
        or repository.split("/")[-1] in (".", "..")
        or not target.strip()
        or not table
        or not token_arn
        or (
            (local is not None or github_local is not None)
            and (local != "http://floci:4566" or github_local != "http://github:8080")
        )
    ):
        return response(500, "Receiver configuration unavailable")
    headers = {key.lower(): value for key, value in event.get("headers", {}).items()}
    try:
        raw = event.get("body")
        if not isinstance(raw, str):
            return response(400, "Invalid request body")
        body = (
            base64.b64decode(raw, validate=True)
            if event.get("isBase64Encoded")
            else raw.encode("utf-8")
        )
    except (ValueError, UnicodeError):
        return response(400, "Invalid request body")
    signature = headers.get("x-hub-signature-256", "")
    expected = (
        "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    )
    if not isinstance(signature, str) or not hmac.compare_digest(
        expected.encode(), signature.encode("utf-8")
    ):
        return response(403, "Invalid signature")
    if headers.get("x-github-event") != "issues":
        return response(200, "Ignored event")
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            return response(400, "Invalid issue payload")
        if payload.get("action") != "opened":
            return response(200, "Ignored action")
        received_repository = payload.get("repository")
        if (
            not isinstance(received_repository, dict)
            or received_repository.get("full_name") != repository
        ):
            return response(403, "Unauthorized repository")
        issue = payload.get("issue")
        if not isinstance(issue, dict):
            return response(400, "Invalid issue payload")
        if "pull_request" in issue:
            return response(200, "Ignored pull request")
        title, text = issue.get("title"), issue.get("body")
        if (
            not isinstance(title, str)
            or not title.strip()
            or (text is not None and not isinstance(text, str))
        ):
            return response(400, "Invalid issue task")
        task = title + ("\n\n" + text if text else "")
        # Reserve the full SHA's space before lookup; preserve the worker's 16 KiB payload limit.
        job = {"repository": repository, "baseSha": "0" * 40, "task": task}
        if (
            len(
                json.dumps(job, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            > 16384
        ):
            return response(400, "Job too large")
    except (ValueError, UnicodeError):
        return response(400, "Invalid issue payload")
    session = boto3.Session(
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        **(
            {"aws_access_key_id": "test", "aws_secret_access_key": "test"}
            if local
            else {}
        ),
    )
    try:
        with closing(
            session.client("secretsmanager", endpoint_url=local, config=AWS_CONFIG)
        ) as client:
            token = client.get_secret_value(SecretId=token_arn).get("SecretString")
        if not isinstance(token, str) or not token.strip():
            return response(502, "Baseline resolution failed")
        connection = (
            http.client.HTTPConnection("github", 8080, timeout=10)
            if github_local
            else http.client.HTTPSConnection("api.github.com", timeout=10)
        )
        with closing(connection):
            connection.request(
                "GET",
                "/repos/" + repository + "/commits/" + quote(target, safe=""),
                headers={
                    "Authorization": "Bearer " + token,
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "software-factory-poc",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            result = connection.getresponse()
            if result.status != 200:
                return response(502, "Baseline resolution failed")
            baseline = json.loads(result.read()).get("sha")
        if not isinstance(baseline, str) or not re.fullmatch(
            r"[0-9a-fA-F]{40}", baseline
        ):
            return response(502, "Baseline resolution failed")
        job["baseSha"] = baseline
        with closing(
            session.client("dynamodb", endpoint_url=local, config=AWS_CONFIG)
        ) as client:
            client.put_item(
                TableName=table,
                Item={
                    "PK": {"S": "JOBS"},
                    "SK": {"S": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")},
                    "status": {"S": "PENDING"},
                    "job": {"M": {key: {"S": value} for key, value in job.items()}},
                },
                ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
            )
    except (
        BotoCoreError,
        ClientError,
        OSError,
        http.client.HTTPException,
        ValueError,
        TypeError,
        AttributeError,
    ):
        return response(502, "Job preparation or enqueue failed")
    return response(202, "Job queued")
