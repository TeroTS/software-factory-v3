# Validate shared ingress deployment settings and resolve the configured GitHub target SHA; callers own webhook authentication and failure responses.
import http.client
import json
import os
import re
from contextlib import closing
from urllib.parse import quote

from botocore.config import Config

AWS_CONFIG = Config(
    connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 1}
)


# Read deployment-owned repository, target, secret, queue, and local endpoints; return validated settings or None without I/O.
def load_config():
    config = {
        "repository": os.environ.get("AUTHORIZED_REPOSITORY", ""),
        "target": os.environ.get("PR_TARGET_BRANCH", ""),
        "table": os.environ.get("JOB_TABLE_NAME", ""),
        "token_arn": os.environ.get("GITHUB_SECRET_ARN", ""),
        "local": os.environ.get("LOCAL_AWS_ENDPOINT"),
        "github_local": os.environ.get("LOCAL_GITHUB_ENDPOINT"),
    }
    if (
        not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", config["repository"])
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


# Resolve the configured branch through Secrets Manager and GitHub; return a full SHA or None for invalid results, propagating I/O errors.
def resolve_baseline(session, config):
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
