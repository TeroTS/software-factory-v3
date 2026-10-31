# Execute one controlled Job inside the VM; exit zero only after independent publication evidence.
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from worker.evidence import TEST_COMMAND, verify_events, verify_pr, verify_source
from worker.server import parse_job

FILES = ("calculator.py", "tests/test_calculator.py")
ACCEPTANCE = Path(__file__).resolve().parents[1] / "image/acceptance.py"


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
    expiry = datetime.fromisoformat(values.get("Expiration", "").replace("Z", "+00:00"))
    if expiry <= datetime.now(timezone.utc):
        raise ValueError("Runtime credentials expired")
    identity = json.loads(
        capture(["aws", "sts", "get-caller-identity", "--output", "json"], None, env)
    )
    expected = (
        "arn:aws:sts::172318459814:assumed-role/" + env["EXPECTED_RUNTIME_ROLE"] + "/"
    )
    if not identity.get("Arn", "").startswith(expected):
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
            print(
                redact(
                    str(event.get("message", event.get("error", "Agent turn failed"))),
                    secrets,
                ),
                flush=True,
            )
    process.stdout.close()
    code = process.wait()
    if code != 0 or malformed:
        raise ValueError("Codex process or event stream failed")
    return events


# Give the same agent the fixed task, sandbox probe, recovery rules, and ordered publication proof.
def prompt_for(job, branch):
    return f"""Work only in {job["repository"]} at pinned baseline {job["baseSha"]} on branch {branch}.
Task: {job["task"]}
Your FIRST shell command must be exactly: printf gate2-shell-ok
Stop on failure of this initial sandbox check, sandbox enforcement, model authentication or inference.
After the check, recover ordinary command failures with installed equivalents within this same invocation.
Diagnose/fix/rerun failing tests in this same invocation; no agent restart, whole-task rerun, provider/model change or sandbox removal.
Add multiply(a, b) and unittest cases for positive, negative and zero operands, preserving addition and all baseline tests.
Required cases: multiply(2,3)==6, multiply(-2,3)==-6, multiply(-2,-3)==6, zero in either operand position.
Change only calculator.py and tests/test_calculator.py; do not modify deployment configuration or weaken tests.
Inspect a meaningful nonempty source/test diff. Run final approved tests as a standalone command exactly:
{TEST_COMMAND}
Only after that command exits 0, run separate standalone commands in this order: git commit, git push, gh pr create.
Commit on {branch}; push only that branch to origin and create one PR targeting main, left unmerged.
On publication timeout, inspect the existing remote branch/PR before retrying in this same invocation to avoid duplicates.
Use a standalone git ls-remote to confirm a timed-out push, or gh pr view/list to confirm a timed-out PR creation.
Never push directly to main, merge a PR, display credentials, change AWS configuration, or include secret values in output.
Stop on unrecoverable failure. Your prose final response or successful process exit is not completion evidence.
"""


# Clone/test the pinned baseline, run one agent, then verify tests/Git/remote PR independently.
def execute(job):
    job = parse_job({"microvmId": "runtime", "runHookPayload": json.dumps(job)})
    env = {
        **os.environ,
        "AWS_REGION": "us-east-1",
        "AWS_DEFAULT_REGION": "us-east-1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    secrets = []
    print("Discover scoped runtime credentials", flush=True)
    initial_access = credentials(env, secrets)
    print("Retrieve repository-scoped GitHub token", flush=True)
    token = json.loads(
        capture(
            [
                "aws",
                "secretsmanager",
                "get-secret-value",
                "--secret-id",
                env["GITHUB_SECRET_ARN"],
                "--output",
                "json",
            ],
            None,
            env,
        )
    ).get("SecretString")
    if not isinstance(token, str):
        raise TypeError("Invalid repository-scoped GitHub token")
    if not token.strip():
        raise ValueError("Missing repository-scoped GitHub token")
    secrets.append(token)
    env["GH_TOKEN"] = token
    capture(["gh", "auth", "setup-git"], None, env)
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
    branch = "factory/task-" + uuid.uuid4().hex
    logged(["git", "switch", "-c", branch], repository, env, secrets)
    capture(["git", "config", "user.name", "Software Factory PoC"], repository, env)
    capture(
        ["git", "config", "user.email", "factory@users.noreply.github.com"],
        repository,
        env,
    )
    baseline = {
        name: capture(["git", "show", job["baseSha"] + ":" + name], repository, env)
        for name in FILES
    }
    logged(TEST_COMMAND.split(), repository, env, secrets)
    version = capture(["codex", "--version"], repository, env).strip()
    if version != "codex-cli 0.166.0":
        raise ValueError("Unexpected pinned Codex version")
    args = [
        "codex",
        "exec",
        "--json",
        "--ephemeral",
        "-C",
        str(repository),
        "-s",
        "workspace-write",
        "-m",
        "us.openai.gpt-6.1-sol",
        "-c",
        'model_provider="amazon-bedrock-runtime"',
        "-c",
        'web_search="disabled"',
        "-c",
        "sandbox_workspace_write.network_access=true",
        "-c",
        'approval_policy="never"',
        "-c",
        'shell_environment_policy.inherit="all"',
        "-c",
        "shell_environment_policy.ignore_default_excludes=true",
        "--add-dir",
        str(repository / ".git"),
        "-",
    ]
    events = run_agent(args, prompt_for(job, branch), repository, env, secrets)
    verify_events(events)
    print("Independently verify approved final tests and changes", flush=True)
    logged(TEST_COMMAND.split(), repository, env, secrets)
    logged([sys.executable, str(ACCEPTANCE), str(repository)], repository, env, secrets)
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
    if set(changes) != set(FILES):
        raise ValueError("Missing source/tests or unexpected deployment changes")
    current = {
        name: capture(["git", "show", head + ":" + name], repository, env)
        for name in FILES
    }
    verify_source(baseline, current)
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
    verify_pr(prs, branch, head, job["baseSha"])
    refreshed_access = credentials(env, secrets)
    print(
        "Runtime credentials rediscovered; rotated: "
        + str(initial_access != refreshed_access),
        flush=True,
    )
    print(
        "Verified passing tests, preserved source/tests, baseline ancestry and one agent-published unmerged PR",
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
