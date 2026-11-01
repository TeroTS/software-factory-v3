# Execute one controlled Job inside the VM; exit zero only after independent publication evidence.
import json
import os
import re
import subprocess
import sys
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import boto3
from botocore.config import Config

from worker.server import parse_job

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
        r'(?i)(["\']?(?:AccessKeyId|SecretAccessKey|SessionToken|AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|GH_TOKEN|GITHUB_TOKEN)["\']?\s*[:=]\s*)["\']?[^\s,"\'}]+["\']?',
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


# Discover expiring execution-role credentials and identity without publishing secret values.
def credentials(env, secrets):
    values = json.loads(
        capture(
            ["aws", "configure", "export-credentials", "--format", "process"], None, env
        )
    )
    for key in ("AccessKeyId", "SecretAccessKey", "SessionToken"):
        if not isinstance(values.get(key), str) or not values[key]:
            raise ValueError("Missing temporary runtime credentials")
        secrets.append(values[key])
    expiry = datetime.fromisoformat(values.get("Expiration", ""))
    if expiry <= datetime.now(UTC):
        raise ValueError("Runtime credentials expired")
    with closing(
        boto3.client("sts", region_name=env["AWS_REGION"], config=AWS_CONFIG)
    ) as client:
        identity = client.get_caller_identity()
    expected = (
        "arn:aws:sts::172318459814:assumed-role/" + env["EXPECTED_RUNTIME_ROLE"] + "/"
    )
    arn = identity.get("Arn")
    if (
        identity.get("Account") != "172318459814"
        or not isinstance(arn, str)
        or not arn.startswith(expected)
    ):
        raise ValueError("Unexpected execution role; refusing secret/model access")
    print(
        "Scoped execution-role credential discovery passed; expires "
        + expiry.isoformat(),
        flush=True,
    )
    return values["AccessKeyId"]


# Stream exactly one Codex invocation; malformed JSON/process failure cannot produce success.
def run_agent(args, prompt, cwd, env, secrets):
    process = subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    process.stdin.write(prompt)
    process.stdin.close()
    events = []
    malformed = False
    failed = False
    for line in process.stdout:
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise TypeError("Invalid agent event")
        except (ValueError, UnicodeError, TypeError):
            malformed = True
            print("Non-JSON Codex diagnostic: " + redact(line, secrets), flush=True)
            continue
        events.append(event)
        item = event.get("item", {})
        if item.get("type") == "command_execution":
            print(
                redact(
                    item.get("command", "") + "\n" + item.get("aggregated_output", ""),
                    secrets,
                ),
                flush=True,
            )
            if "exit_code" in item:
                print(
                    "Agent command actual exit code " + str(item["exit_code"]),
                    flush=True,
                )
        if event.get("type") in ("error", "turn.failed"):
            failed = True
            print(
                redact(
                    str(event.get("message", event.get("error", "Agent turn failed"))),
                    secrets,
                ),
                flush=True,
            )
    process.stdout.close()
    code = process.wait()
    if code != 0 or malformed or failed:
        raise ValueError("Codex process or event stream failed")
    return events


# Give one agent the approved task, script contract, sandbox probe, and configured publication target.
def prompt_for(job, branch, target):
    return f"""Work only in {job["repository"]} at pinned baseline {job["baseSha"]} on branch {branch}.
Task: {job["task"]}
Your FIRST shell command must be exactly: printf factory-shell-ok
Stop on failure of this initial sandbox check, sandbox enforcement, model authentication or inference.
After the check, recover ordinary command failures with installed equivalents within this same invocation.
Diagnose/fix/rerun failing tests in this same invocation; no agent restart, whole-task rerun, provider/model change or sandbox removal.
Implement the requested task and appropriate tests; do not weaken tests or modify deployment configuration.
Inspect the nonempty diff for correctness and test adequacy. Run final verification from the repository root:
./scripts/verify
Only after it exits 0, commit, push, and run gh pr create in this same invocation.
Commit on {branch}; push only that branch to origin and create one PR targeting {target}, left unmerged.
On publication timeout, inspect the existing remote branch/PR before retrying in this same invocation to avoid duplicates.
Use a standalone git ls-remote to confirm a timed-out push, or gh pr view/list to confirm a timed-out PR creation.
Never push directly to {target}, merge a PR, display credentials, change AWS configuration, or include secret values in output.
Stop on unrecoverable failure. Your prose final response or successful process exit is not completion evidence.
"""


# Prepare the subprocess environment and Git auth; register secrets and reject unscoped credentials.
def prepare_environment(secrets):
    env = {
        **os.environ,
        "AWS_REGION": "us-east-1",
        "AWS_DEFAULT_REGION": "us-east-1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    print("Discover scoped runtime credentials", flush=True)
    initial_access = credentials(env, secrets)
    print("Retrieve repository-scoped GitHub token", flush=True)
    with closing(
        boto3.client("secretsmanager", region_name=env["AWS_REGION"], config=AWS_CONFIG)
    ) as client:
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
    return env, initial_access


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


# Enforce the pinned Codex invocation for this Job/branch; return streamed events or propagate failure.
def invoke_agent(job, repository, branch, env, secrets):
    version = capture(["codex", "--version"], repository, env).strip()
    if version != "codex-cli 0.166.0":
        raise ValueError("Unexpected pinned Codex version")
    if not CODEX_CONFIG.is_file():
        raise ValueError("Missing factory Codex configuration")
    args = [
        "codex",
        "exec",
        "--json",
        "--ephemeral",
        "-C",
        str(repository),
        "--add-dir",
        str(repository / ".git"),
        "-",
    ]
    events = run_agent(
        args, prompt_for(job, branch, env["PR_TARGET_BRANCH"]), repository, env, secrets
    )
    commands = [
        event["item"]
        for event in events
        if event.get("type") == "item.completed"
        and event.get("item", {}).get("type") == "command_execution"
    ]
    if (
        not commands
        or commands[0].get("exit_code") != 0
        or "factory-shell-ok" not in commands[0].get("aggregated_output", "")
    ):
        raise ValueError("Initial sandbox shell did not pass")
    return events


# Check one open, unmerged PR against verified Git branch/head, pinned base, and configured target.
def verify_pr(prs, branch, head, base, target):
    if len(prs) != 1:
        raise ValueError("Expected exactly one PR on task branch")
    pr = prs[0]
    expected = {
        "state": "OPEN",
        "mergedAt": None,
        "baseRefName": target,
        "headRefName": branch,
        "headRefOid": head,
        "baseRefOid": base,
    }
    if (
        not isinstance(pr.get("number"), int)
        or pr["number"] <= 0
        or any(pr.get(key) != value for key, value in expected.items())
    ):
        raise ValueError("PR does not match verified unmerged task publication")


# Independently verify scripts and Git/PR consistency; human review judges correctness, not publication chronology.
def verify_completion(job, repository, branch, env, secrets):
    print("Independently verify repository script and publication", flush=True)
    path = repository / "scripts/verify"
    if not path.is_file() or not os.access(path, os.X_OK):
        print(
            "Missing or non-executable repository script: ./scripts/verify", flush=True
        )
        raise ValueError(
            "Missing or non-executable repository script: ./scripts/verify"
        )
    logged(["./scripts/verify"], repository, env, secrets)
    head = capture(["git", "rev-parse", "HEAD"], repository, env).strip()
    if (
        head == job["baseSha"]
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
                "number,state,mergedAt,baseRefName,headRefName,headRefOid,baseRefOid",
            ],
            repository,
            env,
        )
    )
    verify_pr(prs, branch, head, job["baseSha"], env["PR_TARGET_BRANCH"])


# Validate and coordinate one Job's stages; require final scoped credentials before successful exit.
def execute(job):
    job = parse_job({"microvmId": "runtime", "runHookPayload": json.dumps(job)})
    secrets = []
    env, initial_access = prepare_environment(secrets)
    repository, branch = prepare_repository(job, env, secrets)
    invoke_agent(job, repository, branch, env, secrets)
    verify_completion(job, repository, branch, env, secrets)
    print("Rediscover scoped runtime credentials after completion", flush=True)
    refreshed_access = credentials(env, secrets)
    print(
        "Runtime credentials rediscovered; rotated: "
        + str(initial_access != refreshed_access),
        flush=True,
    )
    print(
        "Verified repository script, nonempty commit, baseline ancestry and one agent-published unmerged PR; human correctness review required",
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
