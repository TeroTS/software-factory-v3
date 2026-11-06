# Execute one controlled Job inside the VM; exit zero only after independent publication evidence.
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import uuid
from contextlib import closing
from pathlib import Path

import boto3
from botocore.config import Config

from src.worker.prompts import implementation_prompt, publication_prompt, review_prompt
from src.worker.review import (
    REVIEW_SCHEMA,
    ReviewError,
    ReviewRejected,
    read_review_result,
)
from src.worker.server import parse_job

CODEX_CONFIG = Path("/etc/codex/managed_config.toml")
AWS_CONFIG = Config(
    connect_timeout=5,
    read_timeout=10,
    retries={"mode": "standard", "total_max_attempts": 1},
)


# Capture a subprocess without a shell; callers decide whether its output is safe to log.
def capture(args, cwd, env, secrets=None):
    result = subprocess.run(
        args, cwd=cwd, env=env, text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise subprocess.CalledProcessError(
            result.returncode, args, result.stdout, result.stderr
        )
    if secrets is not None and result.stderr:
        print(redact(result.stderr, secrets), flush=True)
    return result.stdout


# Remove known secrets and recognizable newly rotated credential fields before stdout logging.
def redact(text, secrets):
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(
        r'(?i)([\\"\']*(?:AccessKeyId|SecretAccessKey|SessionToken|AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|GH_TOKEN|GITHUB_TOKEN)[\\"\']*\s*[:=]\s*)[\\"\']*[^\s,"\'\\}]+[\\"\']*',
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|(?:AKIA|ASIA)[A-Z0-9]{16})\b",
        "[REDACTED]",
        text,
    )
    return text


# Log available safe command/test output and real exit code; propagate failures to terminal error.
def logged(args, cwd, env, secrets):
    try:
        output = capture(args, cwd, env, secrets)
    except subprocess.CalledProcessError as error:
        print(redact((error.output or "") + (error.stderr or ""), secrets), flush=True)
        print("Command failed; actual exit code " + str(error.returncode), flush=True)
        raise
    print(redact(output, secrets), flush=True)
    print(
        "Command completed: "
        + redact(" ".join(args), secrets)
        + "; actual exit code 0",
        flush=True,
    )
    return output


# Register SDK-discovered credential values for redaction; AWS SDK/service errors handle credential failures.
def credentials(session, secrets):
    values = session.get_credentials()
    if values is not None:
        frozen = values.get_frozen_credentials()
        secrets.extend((frozen.access_key, frozen.secret_key, frozen.token))


# Run one Codex process with stdin prompt; log redacted diagnostics after exit and propagate failure.
def run_agent(args, prompt, cwd, env, secrets):
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        input=prompt,
        text=True,
        capture_output=True,
        check=False,
    )
    print(redact(result.stdout + result.stderr, secrets), flush=True)
    print("Agent process actual exit code " + str(result.returncode), flush=True)
    result.check_returncode()


# Prepare the subprocess environment and Git auth; use SDK credentials and register secrets for redaction.
def prepare_environment(secrets):
    env = {
        **os.environ,
        "AWS_REGION": "us-east-1",
        "AWS_DEFAULT_REGION": "us-east-1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    session = boto3.Session(region_name=env["AWS_REGION"])
    credentials(session, secrets)
    print("Retrieve repository-scoped GitHub token", flush=True)
    with closing(session.client("secretsmanager", config=AWS_CONFIG)) as client:
        token = client.get_secret_value(SecretId=env["GITHUB_SECRET_ARN"]).get(
            "SecretString"
        )
    if not isinstance(token, str):
        raise TypeError("Invalid repository-scoped GitHub token")
    if not token.strip():
        raise ValueError("Missing repository-scoped GitHub token")
    secrets.append(token)
    env["GH_TOKEN"] = token
    capture(["gh", "auth", "setup-git"], None, env)
    return env


# Clone the pinned authorized repository; run setup and baseline verification, returning path and task branch.
def prepare_repository(job, env, secrets):
    root = Path(env["WORKSPACE"])
    root.mkdir(parents=True, exist_ok=True)
    repository = root / "repository"
    print("Clone and check out pinned baseline", flush=True)
    logged(
        [
            "git",
            "clone",
            "https://github.com/" + job["repository"] + ".git",
            str(repository),
        ],
        root,
        env,
        secrets,
    )
    logged(["git", "checkout", "--detach", job["baseSha"]], repository, env, secrets)
    capture(
        ["git", "check-ref-format", "--branch", env["PR_TARGET_BRANCH"]],
        repository,
        env,
    )
    branch = "factory/task-" + uuid.uuid4().hex
    if branch == env["PR_TARGET_BRANCH"]:
        raise ValueError("Task branch matches configured PR target")
    logged(["git", "switch", "-c", branch], repository, env, secrets)
    capture(["git", "config", "user.name", "Software Factory PoC"], repository, env)
    capture(
        ["git", "config", "user.email", "factory@users.noreply.github.com"],
        repository,
        env,
    )
    for script in ("./scripts/setup", "./scripts/verify"):
        path = repository / script
        if not path.is_file() or not os.access(path, os.X_OK):
            print("Missing or non-executable repository script: " + script, flush=True)
            raise ValueError("Missing or non-executable repository script: " + script)
    logged(["./scripts/setup"], repository, env, secrets)
    logged(["./scripts/verify"], repository, env, secrets)
    return repository, branch


# Enforce the pinned Codex invocation for this Job/branch; propagate process failure.
def invoke_agent(job, repository, branch, env, secrets, *, prompt=None, options=()):
    version = capture(["codex", "--version"], repository, env).strip()
    if version != "codex-cli 0.166.0":
        raise ValueError("Unexpected pinned Codex version")
    if not CODEX_CONFIG.is_file():
        raise ValueError("Missing factory Codex configuration")
    args = [
        "codex",
        "exec",
        "--ephemeral",
        "-C",
        str(repository),
        "--add-dir",
        str(repository / ".git"),
        *options,
        "-",
    ]
    run_agent(
        args,
        prompt
        if prompt is not None
        else implementation_prompt(job, branch, env["PR_TARGET_BRANCH"]),
        repository,
        env,
        secrets,
    )


# Check one open, unmerged PR against verified task branch/head and configured target, which may advance.
def verify_pr(prs, branch, head, target):
    if len(prs) != 1:
        raise ValueError("Expected exactly one PR on task branch")
    pr = prs[0]
    expected = {
        "state": "OPEN",
        "mergedAt": None,
        "baseRefName": target,
        "headRefName": branch,
        "headRefOid": head,
    }
    if (
        not isinstance(pr.get("number"), int)
        or pr["number"] <= 0
        or any(pr.get(key) != value for key, value in expected.items())
    ):
        raise ValueError("PR does not match verified unmerged task publication")


# Independently verify implementation before any review or publication; propagate script failures.
def verify_candidate(repository, env, secrets):
    print("Independently verify repository candidate", flush=True)
    path = repository / "scripts/verify"
    if not path.is_file() or not os.access(path, os.X_OK):
        print(
            "Missing or non-executable repository script: ./scripts/verify", flush=True
        )
        raise ValueError(
            "Missing or non-executable repository script: ./scripts/verify"
        )
    logged(["./scripts/verify"], repository, env, secrets)


# Stage every tracked/untracked candidate change and create the ordinary commit that review will identify.
def commit_candidate(repository, branch, env, secrets):
    if capture(["git", "branch", "--show-current"], repository, env).strip() != branch:
        raise ValueError("Candidate left assigned task branch")
    logged(["git", "add", "--all"], repository, env, secrets)
    logged(["git", "commit", "-m", "Implement Agent task"], repository, env, secrets)
    return capture(["git", "rev-parse", "HEAD"], repository, env).strip()


# Run fresh read-only review using a new external result file; reject unreliable evidence or required corrections.
def review_candidate(job, repository, branch, head, env, secrets):
    print("Fresh review of commit " + head, flush=True)
    with tempfile.TemporaryDirectory(
        prefix="factory-review-", dir=repository.parent
    ) as directory:
        schema = Path(directory) / "schema.json"
        result = Path(directory) / "last-message.json"
        schema.write_text(json.dumps(REVIEW_SCHEMA))
        policy = tomllib.loads(CODEX_CONFIG.read_text())
        # Managed workspace-write overrides CLI sandbox selection; Git checks still enforce read-only review.
        options = (
            []
            if policy.get("sandbox_mode") == "workspace-write"
            else ["--sandbox", "read-only"]
        )
        prompt = review_prompt(job, branch, env["PR_TARGET_BRANCH"], head)
        try:
            invoke_agent(
                job,
                repository,
                branch,
                env,
                secrets,
                prompt=prompt,
                options=[
                    *options,
                    "--output-schema",
                    str(schema),
                    "--output-last-message",
                    str(result),
                ],
            )
        except (subprocess.CalledProcessError, OSError) as error:
            raise ReviewError("Reviewer process failed") from error
        verify_review_unchanged(repository, branch, head, env)
        verdict, findings = read_review_result(result)
    print("Review verdict " + verdict + " for commit " + head, flush=True)
    if verdict == "FAIL":
        print(redact(json.dumps(findings), secrets), flush=True)
        raise ReviewRejected("Review requires corrections")


# Reject review-time changes to the commit, task branch, index, tracked files, or untracked files.
def verify_review_unchanged(repository, branch, head, env):
    if (
        capture(["git", "rev-parse", "HEAD"], repository, env).strip() != head
        or capture(["git", "branch", "--show-current"], repository, env).strip()
        != branch
        or capture(
            ["git", "status", "--porcelain", "--untracked-files=all"], repository, env
        ).strip()
    ):
        raise ReviewError("Reviewer changed candidate")


# Publish the existing reviewed commit once; after ambiguous process exit inspect state instead of retrying.
def publish_candidate(job, repository, branch, head, env, secrets):
    prompt = publication_prompt(job, branch, env["PR_TARGET_BRANCH"], head)
    try:
        invoke_agent(job, repository, branch, env, secrets, prompt=prompt)
    except (subprocess.CalledProcessError, OSError):
        print(
            "Publication process failed; inspect existing Git/PR state without retry",
            flush=True,
        )
    verify_completion(job, repository, branch, head, env)


# Independently confirm publication of exactly the reviewed commit; no verification rerun after read-only review.
def verify_completion(job, repository, branch, reviewed_head, env):
    print("Independently confirm reviewed publication", flush=True)
    verify_local_candidate(job, repository, branch, reviewed_head, env)
    verify_remote_publication(job, repository, branch, reviewed_head, env)


# Confirm a clean task branch at the reviewed SHA with a nonempty change descended from the pinned baseline.
def verify_local_candidate(job, repository, branch, reviewed_head, env):
    head = capture(["git", "rev-parse", "HEAD"], repository, env).strip()
    if (
        head != reviewed_head
        or head == job["baseSha"]
        or capture(["git", "branch", "--show-current"], repository, env).strip()
        != branch
    ):
        raise ValueError("Missing unique task commit/branch")
    capture(
        ["git", "merge-base", "--is-ancestor", job["baseSha"], head], repository, env
    )
    if capture(["git", "status", "--porcelain"], repository, env).strip():
        raise ValueError("Dirty/uncommitted task workspace")
    changes = capture(
        ["git", "diff", "--name-only", job["baseSha"], head], repository, env
    ).splitlines()
    if not changes:
        raise ValueError("Missing nonempty committed change")


# Confirm the remote branch and exactly one open PR have the reviewed head and configured target.
def verify_remote_publication(job, repository, branch, head, env):
    remote = capture(
        ["git", "ls-remote", "--heads", "origin", branch], repository, env
    ).split()
    if remote != [head, "refs/heads/" + branch]:
        raise ValueError("Published branch/head mismatch")
    prs = json.loads(
        capture(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                job["repository"],
                "--head",
                branch,
                "--state",
                "all",
                "--json",
                "number,state,mergedAt,baseRefName,headRefName,headRefOid",
            ],
            repository,
            env,
        )
    )
    verify_pr(prs, branch, head, env["PR_TARGET_BRANCH"])


# Validate and coordinate one Job's stages; complete only after independent review/publication evidence.
def execute(job):
    job = parse_job({"microvmId": "runtime", "runHookPayload": json.dumps(job)})
    secrets = []
    env = prepare_environment(secrets)
    repository, branch = prepare_repository(job, env, secrets)
    invoke_agent(job, repository, branch, env, secrets)
    verify_candidate(repository, env, secrets)
    head = commit_candidate(repository, branch, env, secrets)
    review_candidate(job, repository, branch, head, env, secrets)
    publish_candidate(job, repository, branch, head, env, secrets)
    print(
        "Verified repository script, nonempty commit, baseline ancestry and one agent-published unmerged PR; fresh review PASS for commit "
        + head
        + "; merge not authorized",
        flush=True,
    )


# Read only structured Job from stdin; failures exit nonzero without leaking exception values.
def main():
    try:
        execute(json.load(sys.stdin))
    except Exception as error:  # noqa: BLE001 - process boundary must not dump secret-bearing exceptions
        print(
            "Worker failed: "
            + type(error).__name__
            + "; see preceding stage and redacted command output",
            flush=True,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
