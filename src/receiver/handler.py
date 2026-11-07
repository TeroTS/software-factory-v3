# Authenticate GitHub issue deliveries, resolve a deployment-owned baseline, and queue one bounded Job; never log payloads or credentials.
import base64
import hashlib
import hmac
import http.client
import json
import os
from contextlib import closing
from datetime import UTC, datetime

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from src.utils.job_utils import AWS_CONFIG, load_config, resolve_baseline


# Return only a fixed HTTP status/message; no exception details, task content, or tokens cross this boundary.
def response(status, message):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"message": message}),
    }


# Decode the request and verify its HMAC using normalized headers and the signing secret; return body/error without parsing JSON.
def _authenticate_request(event, headers, secret):
    try:
        raw = event.get("body")
        if not isinstance(raw, str):
            return None, response(400, "Invalid request body")
        body = (
            base64.b64decode(raw, validate=True)
            if event.get("isBase64Encoded")
            else raw.encode("utf-8")
        )
    except (ValueError, UnicodeError):
        return None, response(400, "Invalid request body")
    signature = headers.get("x-hub-signature-256", "")
    expected = (
        "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    )
    if not isinstance(signature, str) or not hmac.compare_digest(
        expected.encode(), signature.encode("utf-8")
    ):
        return None, response(403, "Invalid signature")
    return body, None


# Filter authenticated deliveries and validate issue content against the authorized repository and byte limit; return job/error without I/O.
def _prepare_job(body, event_type, repository):
    if event_type != "issues":
        return None, response(200, "Ignored event")
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            return None, response(400, "Invalid issue payload")
        if payload.get("action") != "opened":
            return None, response(200, "Ignored action")
        received_repository = payload.get("repository")
        if (
            not isinstance(received_repository, dict)
            or received_repository.get("full_name") != repository
        ):
            return None, response(403, "Unauthorized repository")
        issue = payload.get("issue")
        if not isinstance(issue, dict):
            return None, response(400, "Invalid issue payload")
        if "pull_request" in issue:
            return None, response(200, "Ignored pull request")
        title, text = issue.get("title"), issue.get("body")
        if (
            not isinstance(title, str)
            or not title.strip()
            or (text is not None and not isinstance(text, str))
        ):
            return None, response(400, "Invalid issue task")
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
            return None, response(400, "Job too large")
    except (ValueError, UnicodeError):
        return None, response(400, "Invalid issue payload")
    return job, None


# Store the validated, pinned job in the configured DynamoDB queue; propagate failures and preserve existing items on timestamp collisions.
def _enqueue_job(session, config, job):
    with closing(
        session.client("dynamodb", endpoint_url=config["local"], config=AWS_CONFIG)
    ) as client:
        client.put_item(
            TableName=config["table"],
            Item={
                "PK": {"S": "JOBS"},
                "SK": {"S": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")},
                "status": {"S": "PENDING"},
                "job": {"M": {key: {"S": value} for key, value in job.items()}},
            },
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )


# Handle the Function URL v2 envelope; reject unauthorized or malformed requests before any executable Job is sent.
def handle(event, context):
    if event.get("requestContext", {}).get("http", {}).get("method") != "POST":
        return response(405, "POST required")
    config = load_config()
    secret = os.environ.get("WEBHOOK_SECRET", "")
    if config is None or not secret:
        return response(500, "Receiver configuration unavailable")
    headers = {key.lower(): value for key, value in event.get("headers", {}).items()}
    body, error = _authenticate_request(event, headers, secret)
    if error is not None:
        return error
    job, error = _prepare_job(body, headers.get("x-github-event"), config["repository"])
    if error is not None:
        return error
    session = boto3.Session(
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        **(
            {"aws_access_key_id": "test", "aws_secret_access_key": "test"}
            if config["local"]
            else {}
        ),
    )
    try:
        baseline = resolve_baseline(session, config)
        if baseline is None:
            return response(502, "Baseline resolution failed")
        job["baseSha"] = baseline
        _enqueue_job(session, config, job)
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
