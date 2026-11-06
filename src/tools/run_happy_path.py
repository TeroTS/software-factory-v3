# Submit or observe one named Job; verify publication and service-confirmed cleanup without reruns.
import json
import os
import re
import time
from pathlib import Path

from botocore.exceptions import ClientError

from src.tools import live
from src.worker.server import parse_job
from src.worker.task import redact, verify_pr


# Recover known launch IDs from restricted history without printing token-bearing state.
def vm_ids(history):
    identifiers = set()
    for event in history:
        if event["type"] == "TaskSucceeded":
            value = json.loads(event["taskSucceededEventDetails"]["output"])
            if (
                isinstance(value, dict)
                and isinstance(value.get("MicrovmId"), str)
                and value["MicrovmId"]
            ):
                identifiers.add(value["MicrovmId"])
    return identifiers


# Prove one launch/token/termination and authenticated pending-to-ok polls, retaining tokens only in memory.
def check_history(history):
    scheduled = [
        e["taskScheduledEventDetails"] for e in history if e["type"] == "TaskScheduled"
    ]
    resources = [task["resource"] for task in scheduled]
    if any(
        resources.count(name) != 1
        for name in ("runMicrovm", "createMicrovmAuthToken", "terminateMicrovm")
    ):
        raise live.OperatorError(
            "History does not prove exactly one launch/token/termination"
        )
    tokens = [
        json.loads(task["parameters"]).get("Headers", {}).get("X-aws-proxy-auth")
        for task in scheduled
        if task["resource"] == "invoke"
    ]
    responses = [
        json.loads(e["taskSucceededEventDetails"]["output"])
        for e in history
        if e["type"] == "TaskSucceeded"
    ]
    polls = [r for r in responses if isinstance(r, dict) and "StatusCode" in r]
    if (
        not tokens
        or not all(isinstance(token, str) and token for token in tokens)
        or len(set(tokens)) != 1
        or len(polls) != len(tokens)
    ):
        raise live.OperatorError(
            "History does not prove one nonempty token reused for every poll"
        )
    expected = [{"status": "pending"}] * (len(polls) - 1) + [{"status": "ok"}]
    if (
        any(poll["StatusCode"] != 200 for poll in polls)
        or [p.get("ResponseBody") for p in polls] != expected
    ):
        raise live.OperatorError(
            "HTTP history does not prove exact pending-to-ok responses"
        )
    identifiers = vm_ids(history)
    if len(identifiers) != 1:
        raise live.OperatorError(
            "No unique VM ID; recover through restricted lifecycle/history records"
        )
    launch = next(task for task in scheduled if task["resource"] == "runMicrovm")
    return (
        identifiers.pop(),
        json.loads(launch["parameters"])["ImageVersion"],
        len(polls),
    )


# Read the terminated VM's logs with pagination and bounded eventual-consistency waits; never dump raw logs.
def worker_logs(session, vm_id, version):
    for attempt in range(6):
        streams = live.collect(
            session,
            "logs",
            "describe_log_streams",
            "logStreams",
            logGroupName=live.LOG_GROUP,
        )
        names = [
            s["logStreamName"]
            for s in streams
            if s["logStreamName"].endswith(vm_id)
            and "[" + version + "]" in s["logStreamName"]
        ]
        if len(names) == 1:
            parameters = {
                "logGroupName": live.LOG_GROUP,
                "logStreamName": names[0],
                "startFromHead": True,
            }
            messages = []
            while True:
                response = live.aws(session, "logs", "get_log_events", **parameters)
                messages.extend(e["message"] for e in response["events"])
                token = response["nextForwardToken"]
                if token == parameters.get("nextToken"):
                    break
                parameters["nextToken"] = token
            text = "\n".join(messages)
            if "Verified repository script, nonempty commit, baseline ancestry" in text:
                return text
        if attempt < 5:
            time.sleep(5)
    raise live.OperatorError(
        "Worker completion logs unavailable; inspect CloudWatch before accepting this run"
    )


# Correlate logs to the task branch and independently check PR/remote ancestry; display only the review diff.
def inspect_publication(session, vm_id, version, job, target):
    logs = worker_logs(session, vm_id, version)
    branches = set(
        re.findall(r"Command completed: git switch -c (\S+); actual exit code 0", logs)
    )
    verification = logs.split("Independently verify repository candidate")
    reviewed = re.findall(r"Review verdict PASS for commit ([0-9a-f]{40})", logs)
    if (
        len(branches) != 1
        or len(verification) != 2
        or len(reviewed) != 1
        or logs.count("Agent process actual exit code 0") < 2
        or "Command completed: ./scripts/verify; actual exit code 0"
        not in verification[-1].split("Fresh review of commit")[0]
        or "Independently confirm reviewed publication" not in logs
    ):
        raise live.OperatorError(
            "Missing stage verification, passing review, or unique task-branch evidence"
        )
    branch = branches.pop()
    repository = job["repository"]
    prs = json.loads(
        live.output(
            "gh",
            "pr",
            "list",
            "--repo",
            repository,
            "--head",
            branch,
            "--state",
            "all",
            "--json",
            "number,state,mergedAt,baseRefName,headRefName,headRefOid",
        )
    )
    remote = json.loads(
        live.output("gh", "api", "repos/" + repository + "/git/ref/heads/" + branch)
    )["object"]["sha"]
    if reviewed != [remote]:
        raise live.OperatorError("Published head does not match the reviewed commit")
    verify_pr(prs, branch, remote, target)
    comparison = json.loads(
        live.output(
            "gh",
            "api",
            "repos/" + repository + "/compare/" + job["baseSha"] + "..." + remote,
        )
    )
    if (
        comparison["merge_base_commit"]["sha"].lower() != job["baseSha"].lower()
        or comparison["ahead_by"] < 1
        or not comparison["files"]
    ):
        raise live.OperatorError(
            "Published change is empty or not descended from the pinned baseline"
        )
    number = str(prs[0]["number"])
    print(redact(live.output("gh", "pr", "diff", number, "--repo", repository), []))
    return "https://github.com/" + repository + "/pull/" + number


# Submit/observe one singleton-array Job using a fixed identity; invalid envelopes and uncertain acknowledgements never trigger retries.
def execute(session, name, job_file):
    payload = job_file.read_text()
    if len(payload.encode("utf-8")) > 16384:
        raise live.OperatorError("Job exceeds 16 KB")
    submission = json.loads(payload)
    if (
        not isinstance(submission, list)
        or len(submission) != 1
        or not isinstance(submission[0], dict)
    ):
        raise live.OperatorError(
            "Workflow input must be an array containing exactly one Job object"
        )
    job = submission[0]
    arn = live.MACHINE_ARN.replace(":stateMachine:", ":execution:") + ":" + name
    print("Retain execution ARN: " + arn, flush=True)
    print(
        "For interrupted/uncertain acknowledgement, rerun with this same name and unchanged Job file.",
        flush=True,
    )
    # shortcut: deduplication uses AWS's 90-day name retention, add durable receipts for longer recovery.
    try:
        execution = live.aws(
            session, "stepfunctions", "describe_execution", executionArn=arn
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ExecutionDoesNotExist":
            raise
        execution = None
    if execution is not None and execution["input"] != payload:
        raise live.OperatorError(
            "Existing execution input differs; do not submit a replacement Job"
        )
    if execution is None:
        live.local("make", "synth")
        image = live.verify_workflow(session)
        configuration = image["environmentVariables"]
        os.environ.update(
            {
                key: configuration[key]
                for key in ("AUTHORIZED_REPOSITORY", "PR_TARGET_BRANCH")
            }
        )
        parse_job(
            {
                "microvmId": "operator-validation",
                "runHookPayload": json.dumps(
                    job, ensure_ascii=False, separators=(",", ":")
                ),
            }
        )
        base = live.output(
            "gh",
            "api",
            "repos/" + job["repository"] + "/commits/" + job["baseSha"],
            "--jq",
            ".sha",
        ).strip()
        if base.lower() != job["baseSha"].lower():
            raise live.OperatorError("GitHub did not confirm the requested baseline")
        live.idle(session)
        live.confirm(
            "Review "
            + str(job_file)
            + ". Start exactly one billable Job (model submission and agent branch/PR publication; no merge)?"
        )
        live.idle(session)
        live.aws(
            session,
            "stepfunctions",
            "start_execution",
            stateMachineArn=live.MACHINE_ARN,
            name=name,
            input=payload,
        )
    for attempt in range(181):
        if execution is None or execution["status"] == "RUNNING":
            execution = live.aws(
                session, "stepfunctions", "describe_execution", executionArn=arn
            )
        print("Execution status: " + execution["status"], flush=True)
        if execution["status"] != "RUNNING" or attempt == 180:
            break
        time.sleep(10)
    history = live.collect(
        session, "stepfunctions", "get_execution_history", "events", executionArn=arn
    )
    identifiers = vm_ids(history)
    for identifier in sorted(identifiers):
        print("Retain VM ID: " + identifier, flush=True)
    if len(identifiers) != 1:
        raise live.OperatorError(
            "No unique VM ID; recover through restricted lifecycle/history records"
        )
    vm_id = identifiers.pop()
    vm = live.aws(session, "lambda-microvms", "get_microvm", microvmIdentifier=vm_id)
    print("Service lifecycle: " + vm["state"], flush=True)
    if vm["state"] != "TERMINATED":
        raise live.OperatorError(
            "Cleanup unconfirmed. Workflow timeout/cancellation bypasses cleanup; inspect execution and recover the retained VM manually. No stop/termination was requested by this script."
        )
    if execution["status"] != "SUCCEEDED":
        raise live.OperatorError(
            "Job did not succeed; inspect CloudWatch. No automatic retry or replacement Job."
        )
    checked_id, version, polls = check_history(history)
    if (
        checked_id != vm_id
        or vm["imageVersion"] != version
        or json.loads(execution["output"])
        != {"microvmId": vm_id, "status": "ok", "termination": "TERMINATED"}
    ):
        raise live.OperatorError(
            "Final output/lifecycle does not match the verified execution"
        )
    image = live.aws(
        session,
        "lambda-microvms",
        "get_microvm_image_version",
        imageIdentifier=live.IMAGE_ARN,
        imageVersion=version,
    )
    url = inspect_publication(
        session, vm_id, version, job, image["environmentVariables"]["PR_TARGET_BRANCH"]
    )
    print(
        "PASS: "
        + url
        + "; image "
        + version
        + "; "
        + str(polls)
        + " authenticated polls; fresh review PASS; VM TERMINATED. PR remains unmerged; merge is not authorized by this Job."
    )


# Require operator-provided name/input and expose observation only on subsequent identical invocations.
def main(argv=None):
    parser = live.parser(
        "Run or resume exactly one named happy-path Job; never deploy or merge"
    )
    parser.add_argument("--name", required=True)
    parser.add_argument("--job-file", required=True, type=Path)
    args = parser.parse_args(argv)
    execute(live.operator(args.profile), args.name, args.job_file.resolve())


if __name__ == "__main__":
    raise SystemExit(live.run(main))
