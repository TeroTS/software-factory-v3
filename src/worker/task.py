# Execute one controlled Job inside the VM; exit zero only after independently proven merged integration.
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

from src.worker.prompts import (
    implementation_prompt,
    merge_prompt,
    publication_prompt,
    review_prompt,
)
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


# Check exactly one PR against the task identity and phase; return its identity or reject inconsistent evidence.
def verify_pr(prs, branch, head, target, *, merged=False, number=None):
    if len(prs) != 1:
        raise ValueError("Expected exactly one PR on task branch")
    pr = prs[0]
    expected = {
        "state": "MERGED" if merged else "OPEN",
        "baseRefName": target,
        "headRefName": branch,
        "headRefOid": head,
        "isCrossRepository": False,
    }
    if (
        not isinstance(pr.get("number"), int)
        or pr["number"] <= 0
        or isinstance(pr["number"], bool)
        or (number is not None and pr["number"] != number)
        or (not pr.get("mergedAt") if merged else pr.get("mergedAt") is not None)
        or any(pr.get(key) != value for key, value in expected.items())
    ):
        raise ValueError("PR does not match verified task identity/phase")
    return pr


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


# Run fresh read-only review of the supplied commit; return validated verdict/findings, rejecting unreliable evidence.
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
        verdict, findings = read_review_result(result, head)
    print("Review verdict " + verdict + " for commit " + head, flush=True)
    if verdict == "FAIL":
        print(redact(json.dumps(findings), secrets), flush=True)
    return verdict, findings


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
def publish_candidate(
    job, repository, branch, head, env, secrets, *, review_count=1, addressed=()
):
    prompt = publication_prompt(
        job,
        branch,
        env["PR_TARGET_BRANCH"],
        head,
        review_count=review_count,
        addressed=addressed,
    )
    try:
        invoke_agent(job, repository, branch, env, secrets, prompt=prompt)
    except (subprocess.CalledProcessError, OSError):
        print(
            "Publication process failed; inspect existing Git/PR state without retry",
            flush=True,
        )
    return verify_completion(job, repository, branch, head, env)


# Independently confirm publication of exactly the reviewed commit after final verification; reject changed evidence.
def verify_completion(job, repository, branch, reviewed_head, env):
    print("Independently confirm reviewed publication", flush=True)
    verify_local_candidate(job, repository, branch, reviewed_head, env)
    return verify_remote_publication(job, repository, branch, reviewed_head, env)


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


# Confirm origin and scoped repository access; return settings without trusting agent-selected repository identity.
def verify_repository(job, repository, env):
    remote = capture(["git", "remote", "get-url", "origin"], repository, env).strip()
    if remote != "https://github.com/" + job["repository"] + ".git":
        raise ValueError("Repository origin mismatch")
    settings = json.loads(
        capture(["gh", "api", "repos/" + job["repository"]], repository, env)
    )
    if settings.get("full_name", "").lower() != job["repository"].lower():
        raise ValueError("Scoped repository identity mismatch")
    return settings


# Confirm the remote task head and exactly one phase-matching PR in the authorized repository.
def verify_remote_publication(
    job, repository, branch, head, env, *, merged=False, number=None
):
    verify_repository(job, repository, env)
    remote = capture(
        ["git", "ls-remote", "--heads", "origin", branch], repository, env
    ).split()
    # GitHub may delete the task ref after merge; the bound PR head and fetched parents still prove publication.
    if remote != [head, "refs/heads/" + branch] and not (merged and not remote):
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
                "number,state,mergedAt,baseRefName,headRefName,headRefOid,mergeCommit,isCrossRepository",
            ],
            repository,
            env,
        )
    )
    return verify_pr(
        prs, branch, head, env["PR_TARGET_BRANCH"], merged=merged, number=number
    )


# Inspect required-only checks with the pinned CLI; its JSON mode exits zero even when checks fail.
def verify_required_checks(job, repository, branch, number, env):
    args = [
        "gh",
        "pr",
        "checks",
        str(number),
        "--repo",
        job["repository"],
        "--required",
        "--json",
        "name,bucket",
    ]
    try:
        checks = json.loads(capture(args, repository, env))
    except subprocess.CalledProcessError as error:
        # gh 2.103.3 reports an empty required set as exit 1, not JSON; all other errors fail closed.
        empty = {
            f"no checks reported on the '{branch}' branch",
            f"no required checks reported on the '{branch}' branch",
        }
        if error.returncode == 1 and (error.stderr or "").strip() in empty:
            return
        raise
    if not isinstance(checks, list) or any(
        not isinstance(check, dict) or check.get("bucket") not in ("pass", "skipping")
        for check in checks
    ):
        raise ValueError("GitHub required checks are not satisfied")


# Authorize one fresh merge process after the worker gate; inspect lost responses without an unchecked retry.
def merge_candidate(job, repository, branch, head, number, env, secrets):
    verify_local_candidate(job, repository, branch, head, env)
    verify_remote_publication(job, repository, branch, head, env, number=number)
    if verify_repository(job, repository, env).get("allow_merge_commit") is not True:
        raise ValueError("Repository prohibits merge commits")
    verify_required_checks(job, repository, branch, number, env)
    print(
        "Independent worker gate authorizes merge of PR " + str(number) + " at " + head,
        flush=True,
    )
    try:
        invoke_agent(
            job,
            repository,
            branch,
            env,
            secrets,
            prompt=merge_prompt(job, branch, env["PR_TARGET_BRANCH"], head, number),
        )
    except (subprocess.CalledProcessError, OSError):
        print(
            "Merge process failed; inspect existing Git/PR state without retry",
            flush=True,
        )
    verify_merged_integration(job, repository, branch, head, number, env)


# Prove the exact PR merge has two parents, reviewed head second, and ancestry in the independently fetched target.
def verify_merged_integration(job, repository, branch, head, number, env):
    print("Independently confirm merged integration", flush=True)
    verify_local_candidate(job, repository, branch, head, env)
    pr = verify_remote_publication(
        job, repository, branch, head, env, merged=True, number=number
    )
    merge = (pr.get("mergeCommit") or {}).get("oid")
    if not isinstance(merge, str) or not re.fullmatch(r"[0-9a-f]{40}", merge):
        raise ValueError("Missing merge commit identity")
    capture(
        [
            "git",
            "fetch",
            "--no-tags",
            "origin",
            "refs/heads/" + env["PR_TARGET_BRANCH"],
        ],
        repository,
        env,
    )
    target = capture(["git", "rev-parse", "FETCH_HEAD"], repository, env).strip()
    capture(["git", "fetch", "--no-tags", "origin", merge], repository, env)
    parents = capture(
        ["git", "rev-list", "--parents", "-n", "1", merge], repository, env
    ).split()
    if len(parents) != 3 or parents[0] != merge or parents[2] != head:
        raise ValueError(
            "Merge commit does not integrate verified head as second parent"
        )
    capture(["git", "merge-base", "--is-ancestor", merge, target], repository, env)
    verify_repository(job, repository, env)


# Review/repair a committed candidate within three passes; return passing head/count/addressed findings or fail closed.
def review_and_repair_candidate(job, repository, branch, head, env, secrets):
    addressed = []
    for review_count in range(1, 4):
        verdict, findings = review_candidate(
            job, repository, branch, head, env, secrets
        )
        if verdict == "PASS":
            return head, review_count, addressed
        if review_count == 3:
            raise ReviewRejected("Review budget exhausted after three passes")
        findings = [
            {key: redact(value, secrets) for key, value in finding.items()}
            for finding in findings
        ]
        invoke_agent(
            job,
            repository,
            branch,
            env,
            secrets,
            prompt=implementation_prompt(
                job,
                branch,
                env["PR_TARGET_BRANCH"],
                review_head=head,
                findings=findings,
            ),
        )
        verify_candidate(repository, env, secrets)
        addressed.append({"candidateHead": head, "findings": findings})
        head = commit_candidate(repository, branch, env, secrets)


# Coordinate verification, bounded fresh review, publication and gated merge; fail closed before queue release.
def execute(job):
    job = parse_job({"microvmId": "runtime", "runHookPayload": json.dumps(job)})
    secrets = []
    env = prepare_environment(secrets)
    repository, branch = prepare_repository(job, env, secrets)
    invoke_agent(job, repository, branch, env, secrets)
    verify_candidate(repository, env, secrets)
    head = commit_candidate(repository, branch, env, secrets)
    head, review_count, addressed = review_and_repair_candidate(
        job, repository, branch, head, env, secrets
    )
    verify_candidate(repository, env, secrets)
    verify_review_unchanged(repository, branch, head, env)
    pr = publish_candidate(
        job,
        repository,
        branch,
        head,
        env,
        secrets,
        review_count=review_count,
        addressed=addressed,
    )
    merge_candidate(job, repository, branch, head, pr["number"], env, secrets)
    print(
        "Verified repository script, nonempty commit, baseline ancestry and independently verified merged PR; fresh review PASS for commit "
        + head
        + "; merged integration confirmed",
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
