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


# Validate deployment-owned queue/GitHub settings and, for webhook receipt, the signing secret; return config or None without I/O.
def _load_config(webhook=True):
    config = {
        "secret": os.environ.get("WEBHOOK_SECRET", ""),
        "repository": os.environ.get("AUTHORIZED_REPOSITORY", ""),
        "target": os.environ.get("PR_TARGET_BRANCH", ""),
        "table": os.environ.get("JOB_TABLE_NAME", ""),
        "token_arn": os.environ.get("GITHUB_SECRET_ARN", ""),
        "local": os.environ.get("LOCAL_AWS_ENDPOINT"),
        "github_local": os.environ.get("LOCAL_GITHUB_ENDPOINT"),
    }
    if (
        (webhook and not config["secret"])
        or not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", config["repository"])
        or config["repository"].split("/")[-1] in (".", "..")
        or not config["target"].strip()
        or not config["table"]
        or not config["token_arn"]
        or (
            (config["local"] is not None or config["github_local"] is not None)
            and (
                config["local"] != "http://floci:4566"
                or config["github_local"] != "http://github:8080"
            )
        )
    ):
        return None
    return config


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


# Resolve the configured branch through Secrets Manager and GitHub; return a full SHA or None for invalid results, propagating I/O errors.
def _resolve_baseline(session, config):
    with closing(
        session.client(
            "secretsmanager", endpoint_url=config["local"], config=AWS_CONFIG
        )
    ) as client:
        token = client.get_secret_value(SecretId=config["token_arn"]).get(
            "SecretString"
        )
    if not isinstance(token, str) or not token.strip():
        return None
    connection = (
        http.client.HTTPConnection("github", 8080, timeout=10)
        if config["github_local"]
        else http.client.HTTPSConnection("api.github.com", timeout=10)
    )
    with closing(connection):
        connection.request(
            "GET",
            "/repos/"
            + config["repository"]
            + "/commits/"
            + quote(config["target"], safe=""),
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
                "User-Agent": "software-factory-poc",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        result = connection.getresponse()
        if result.status != 200:
            return None
        baseline = json.loads(result.read()).get("sha")
    if not isinstance(baseline, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", baseline):
        return None
    return baseline


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
    config = _load_config()
    if config is None:
        return response(500, "Receiver configuration unavailable")
    headers = {key.lower(): value for key, value in event.get("headers", {}).items()}
    body, error = _authenticate_request(event, headers, config["secret"])
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
        baseline = _resolve_baseline(session, config)
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
