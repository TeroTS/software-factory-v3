import copy
import json
import re
import tempfile
import unittest

from aws_cdk import App, Environment
from aws_cdk.assertions import Template

from src.infra.artifacts_stack import Artifacts
from src.infra.image_stack import Image
from src.infra.workflow_stack import Workflow


class TaskError(Exception):
    pass


def lookup(data, path, context):
    if path.startswith("$$"):
        data, path = context, "$" + path[2:]
    for key, quoted, index in re.findall(r"\.([\w-]+)|\['([^']+)'\]|\[(\d+)\]", path):
        data = data[int(index) if index else key or quoted]
    return data


def expression(value, data, context):
    if value.startswith("States.JsonToString("):
        return json.dumps(lookup(data, value[20:-1], context), separators=(",", ":"))
    if value.startswith("States.Format("):
        match = re.fullmatch(r"States.Format\('([^']*)', (.+)\)", value)
        return match[1].format(lookup(data, match[2], context))
    return lookup(data, value, context)


def parameters(value, data, context):
    if isinstance(value, dict):
        return {
            key.removesuffix(".$"): (
                expression(item, data, context)
                if key.endswith(".$")
                else parameters(item, data, context)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [parameters(item, data, context) for item in value]
    return value


def choice(rule, data, context):
    if "Condition" in rule:
        # Model only this predicate; the opt-in AWS test executes the real JSONata state.
        direct = (
            "{% $type($states.input) = 'array' and $count($states.input) = 1 "
            "and $type($states.input[0]) = 'object' %}"
        )
        if rule["Condition"] == direct:
            return (
                isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict)
            )
        assert rule["Condition"] == (
            "{% $type($states.input) = 'object' and $type($states.input.jobs) = 'array' "
            "and $count($states.input.jobs) = 1 and $type($states.input.jobs[0]) = 'object' "
            "and $type($states.input.queue) = 'object' and $states.input.queue.PK = 'JOBS' "
            "and $type($states.input.queue.SK) = 'string' and $states.input.queue.SK != '' %}"
        )
        return (
            isinstance(data, dict)
            and isinstance(data.get("jobs"), list)
            and len(data["jobs"]) == 1
            and isinstance(data["jobs"][0], dict)
            and isinstance(data.get("queue"), dict)
            and data["queue"].get("PK") == "JOBS"
            and isinstance(data["queue"].get("SK"), str)
            and bool(data["queue"]["SK"])
        )
    if "And" in rule:
        return all(choice(item, data, context) for item in rule["And"])
    if "Or" in rule:
        return any(choice(item, data, context) for item in rule["Or"])
    if "Not" in rule:
        return not choice(rule["Not"], data, context)
    try:
        value = lookup(data, rule["Variable"], context)
    except (KeyError, TypeError, IndexError):
        return rule.get("IsPresent") is False
    if "IsPresent" in rule:
        return rule["IsPresent"]
    if "IsString" in rule:
        return isinstance(value, str) == rule["IsString"]
    if "IsNumeric" in rule:
        return (type(value) in (int, float)) == rule["IsNumeric"]
    return value == rule.get("StringEquals", rule.get("NumericEquals"))


def simulate(definition, replies, limit=100, job=None):
    data = (
        job
        if job is not None
        else [
            {
                "repository": "example/authorized-project",
                "baseSha": "a" * 40,
                "task": "Update greeting and tests",
            }
        ]
    )
    context = {"Execution": {"Name": "same-execution"}}
    state = definition["StartAt"]
    calls, elapsed = [], 0
    replies = {
        name: items if callable(items) else iter(items)
        for name, items in replies.items()
    }
    for _ in range(limit):
        step = definition["States"][state]
        kind = step["Type"]
        if kind in ("Succeed", "Fail"):
            if kind == "Fail" and "ErrorPath" in step:
                # Fail paths must resolve on the shaped, token-free terminal state.
                expression(step["ErrorPath"], data, context)
                expression(step["CausePath"], data, context)
            return kind, data, calls, state
        if kind == "Choice":
            state = next(
                (
                    rule["Next"]
                    for rule in step["Choices"]
                    if choice(rule, data, context)
                ),
                step["Default"],
            )
            continue
        if kind == "Wait":
            elapsed += step["Seconds"]
            if elapsed >= definition["TimeoutSeconds"]:
                return "TIMED_OUT", data, calls, state
        else:
            if kind == "Task":
                args = parameters(step["Parameters"], data, context)
                calls.append((state, args))
                reply = replies[state]
                result = reply(args) if callable(reply) else next(reply)
                if isinstance(result, Exception):
                    catch = step.get("Catch")
                    if not catch:
                        return "Fail", data, calls, state
                    data[catch[0]["ResultPath"][2:]] = {
                        "Error": "simulated",
                        "Cause": "secret-jwe",
                    }
                    state = catch[0]["Next"]
                    continue
            else:
                result = parameters(
                    step.get("Parameters", step.get("Result", data)), data, context
                )
            path = step.get("ResultPath", "$")
            if path == "$":
                data = copy.deepcopy(result)
            elif path is not None:
                data[path[2:]] = result
        state = step["Next"]
    raise AssertionError("Simulation exceeded state budget")


def entry_definition(definition):
    entry = {
        "StartAt": definition["StartAt"],
        "States": {
            name: copy.deepcopy(definition["States"][name])
            for name in ("CheckSubmission", "Input", "QueuedInput", "InvalidSubmission")
        },
    }
    for name in ("Input", "QueuedInput"):
        entry["States"][name].pop("Next")
        entry["States"][name]["End"] = True
    return entry


class WorkflowFixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        app = App(outdir=directory.name)
        env = Environment(account="172318459814", region="us-east-1")
        artifacts = Artifacts(app, "Artifacts", env=env)
        image = Image(app, "Image", artifact_bucket=artifacts.bucket_name, env=env)
        workflow = Workflow(app, "Workflow", image=image, env=env)
        template = Template.from_stack(workflow)
        self.filters = next(
            iter(template.find_resources("AWS::Lambda::EventSourceMapping").values())
        )["Properties"]["FilterCriteria"]
        resource = next(
            iter(template.find_resources("AWS::StepFunctions::StateMachine").values())
        )
        encoded = resource["Properties"]["DefinitionString"]
        # Substitute only CloudFormation deployment references, never ASL expressions.
        text = (
            encoded
            if isinstance(encoded, str)
            else "".join(
                piece if isinstance(piece, str) else "symbolic-value"
                for piece in encoded["Fn::Join"][1]
            )
        )
        self.definition = json.loads(text)
        self.replies = {
            "LaunchVM": [
                {
                    "MicrovmId": "microvm-test",
                    "Endpoint": "vm.lambda-microvm.us-east-1.on.aws",
                }
            ],
            "GetVMState": [{"State": "PENDING"}, {"State": "RUNNING"}],
            "GetWorkerToken": [{"AuthToken": {"X-aws-proxy-auth": "secret-jwe"}}],
            "PollWorker": [
                {"StatusCode": 200, "ResponseBody": {"status": "pending"}},
                {"StatusCode": 200, "ResponseBody": {"status": "ok"}},
            ],
            "TerminateVM": [{}],
            "GetTerminationState": [{"State": "TERMINATING"}, {"State": "TERMINATED"}],
        }


class WorkflowTests(WorkflowFixture):
    def test_simplified_plan_uses_fresh_polls_and_shared_termination(self):
        states = self.definition["States"]
        self.assertEqual(self.definition["StartAt"], "CheckSubmission")
        self.assertEqual(states["Input"]["Parameters"], {"job.$": "$[0]"})
        self.assertEqual(states["Input"]["Next"], "LaunchVM")
        self.assertIn("reason", states["RecordFailure"].get("Parameters", {}))
        for obsolete in (
            "InspectImage",
            "ImageReady",
            "InspectVersion",
            "VersionReady",
        ):
            self.assertNotIn(obsolete, states)
        for wait, seconds, target in (
            ("WaitForVMToStart", 5, "GetVMState"),
            ("WaitForWorker", 10, "PollWorker"),
            ("WaitForVMToTerminate", 5, "GetTerminationState"),
        ):
            self.assertEqual(states[wait]["Seconds"], seconds)
            self.assertEqual(states[wait]["Next"], target)
        for outcome in ("RecordSuccess", "RecordFailure"):
            self.assertEqual(states[outcome]["Next"], "TerminateVM")
        self.assertEqual(states["TerminateVM"]["ResultPath"], "$.terminationRequest")
        for name, operation in (
            ("LaunchVM", "runMicrovm"),
            ("GetVMState", "getMicrovm"),
            ("GetWorkerToken", "createMicrovmAuthToken"),
            ("PollWorker", None),
            ("TerminateVM", "terminateMicrovm"),
            ("GetTerminationState", "getMicrovm"),
        ):
            self.assertNotIn("Retry", states[name])
            self.assertEqual(states[name]["Catch"][0]["ErrorEquals"], ["States.ALL"])
            if operation:
                self.assertTrue(
                    states[name]["Resource"].endswith(
                        "states:::aws-sdk:lambdamicrovms:" + operation
                    )
                )
        self.assertEqual(self.definition["TimeoutSeconds"], 1680)
        self.assertEqual(states["PollWorker"]["TimeoutSeconds"], 10)

    def test_success_reuses_one_token_and_confirms_cleanup(self):
        kind, output, calls, final = simulate(self.definition, self.replies)
        self.assertEqual((kind, final), ("Succeed", "JobSucceeded"))
        self.assertEqual(
            output,
            {
                "microvmId": "microvm-test",
                "status": "ok",
                "termination": "TERMINATED",
            },
        )
        self.assertEqual(
            [name for name, _ in calls],
            [
                "LaunchVM",
                "GetVMState",
                "GetVMState",
                "GetWorkerToken",
                "PollWorker",
                "PollWorker",
                "TerminateVM",
                "GetTerminationState",
                "GetTerminationState",
            ],
        )
        polls = [args for name, args in calls if name == "PollWorker"]
        self.assertEqual(polls[0], polls[1])
        self.assertEqual(
            polls[0]["ApiEndpoint"], "https://vm.lambda-microvm.us-east-1.on.aws/status"
        )
        self.assertEqual(
            polls[0]["Headers"],
            {
                "X-aws-proxy-auth": "secret-jwe",
                "X-aws-proxy-port": "8080",
            },
        )
        token = next(args for name, args in calls if name == "GetWorkerToken")
        self.assertEqual(
            token,
            {
                "MicrovmIdentifier": "microvm-test",
                "ExpirationInMinutes": 30,
                "AllowedPorts": [{"Port": 8080}],
            },
        )
        launch = calls[0][1]
        self.assertEqual(launch["ClientToken"], "same-execution")
        self.assertEqual(launch["MaximumDurationInSeconds"], 1800)
        self.assertEqual(
            launch["IdlePolicy"],
            {
                "MaxIdleDurationSeconds": 3600,
                "SuspendedDurationSeconds": 0,
                "AutoResumeEnabled": False,
            },
        )
        self.assertEqual(
            json.loads(launch["RunHookPayload"])["repository"],
            "example/authorized-project",
        )
        for name, args in calls:
            if name not in ("LaunchVM", "PollWorker"):
                self.assertEqual(args["MicrovmIdentifier"], "microvm-test")

    def assert_failed_with_cleanup(self, replies):
        kind, output, calls, final = simulate(self.definition, replies)
        self.assertEqual((kind, final), ("Fail", "JobFailed"))
        self.assertEqual(output["microvmId"], "microvm-test")
        self.assertEqual(output["status"], "error")
        self.assertEqual(output["termination"], "TERMINATED")
        self.assertEqual(output["error"], "JobFailed")
        self.assertIn("TerminateVM", [name for name, _ in calls])
        self.assertIn("GetTerminationState", [name for name, _ in calls])
        self.assertNotIn("secret-jwe", json.dumps(output))

    def test_bad_status_and_http_errors_share_cleanup(self):
        for body in (
            {},
            {"status": "error"},
            {"status": "unknown"},
            {"status": "ok", "extra": True},
            None,
            "not-json",
            [],
            200,
        ):
            with self.subTest(body=body):
                self.assert_failed_with_cleanup(
                    {
                        **self.replies,
                        "PollWorker": [{"StatusCode": 200, "ResponseBody": body}],
                    }
                )
        for reply in (
            TaskError(),
            {},
            {"StatusCode": 502},
            {"StatusCode": 200},
            {"StatusCode": "200", "ResponseBody": {"status": "ok"}},
        ):
            with self.subTest(reply=reply):
                self.assert_failed_with_cleanup({**self.replies, "PollWorker": [reply]})

    def test_launch_failures_do_not_cleanup_unknown_vm(self):
        for reply in (
            TaskError(),
            {},
            {"MicrovmId": ""},
            {"MicrovmId": None},
            {"MicrovmId": 123},
            {"Endpoint": "vm.invalid"},
        ):
            with self.subTest(reply=reply):
                kind, output, calls, final = simulate(
                    self.definition,
                    {**self.replies, "LaunchVM": [reply]},
                )
                self.assertEqual((kind, final), ("Fail", "JobFailed"))
                self.assertNotIn("TerminateVM", [name for name, _ in calls])
                self.assertNotIn("termination", output)
                self.assertNotIn("microvmId", output)
                self.assertNotIn("secret-jwe", json.dumps(output))

    def test_postlaunch_failures_never_succeed(self):
        for state, replies in (
            ("LaunchVM", [{"MicrovmId": "microvm-test"}]),
            ("LaunchVM", [{"MicrovmId": "microvm-test", "Endpoint": ""}]),
            ("LaunchVM", [{"MicrovmId": "microvm-test", "Endpoint": 123}]),
            ("GetVMState", [{"State": "TERMINATED"}]),
            ("GetVMState", [{"State": "TERMINATING"}]),
            ("GetVMState", [{}]),
            ("GetVMState", [TaskError()]),
            ("GetVMState", [{"State": "PENDING"}, TaskError()]),
            ("GetWorkerToken", [TaskError()]),
            ("GetWorkerToken", [{}]),
            ("GetWorkerToken", [{"AuthToken": {"X-aws-proxy-auth": ""}}]),
            ("GetWorkerToken", [{"AuthToken": {"X-aws-proxy-auth": 123}}]),
        ):
            with self.subTest(state=state, replies=replies):
                self.assert_failed_with_cleanup({**self.replies, state: replies})

    def test_cleanup_failure_does_not_claim_termination(self):
        for state, replies in (
            ("TerminateVM", [TaskError()]),
            ("GetTerminationState", [TaskError()]),
            ("GetTerminationState", [{"State": "TERMINATING"}, TaskError()]),
            ("GetTerminationState", [{}]),
            ("GetTerminationState", [{"State": "RUNNING"}]),
            ("GetTerminationState", [{"State": "PENDING"}]),
        ):
            with self.subTest(state=state, replies=replies):
                kind, output, _, final = simulate(
                    self.definition,
                    {**self.replies, state: replies},
                )
                self.assertEqual((kind, final), ("Fail", "JobFailed"))
                self.assertEqual(output["microvmId"], "microvm-test")
                self.assertEqual(output["error"], "CleanupUnconfirmed")
                self.assertIn("unconfirmed", output["cause"])
                self.assertNotIn("termination", output)
                self.assertNotIn("secret-jwe", json.dumps(output))

    def test_workflow_rejects_non_singleton_submissions_before_launch(self):
        job = {"repository": "example/fixture", "baseSha": "a" * 40, "task": "Change"}
        for submission in (
            job,
            [],
            [job, job],
            [None],
            ["task"],
            [[job]],
            {},
            0,
            "task",
        ):
            with self.subTest(submission=submission):
                kind, _, calls, final = simulate(
                    self.definition, self.replies, job=submission
                )
                self.assertEqual((kind, final), ("Fail", "InvalidSubmission"))
                self.assertEqual(calls, [])
        self.assertEqual(
            self.definition["States"]["CheckSubmission"]["QueryLanguage"], "JSONata"
        )
        self.assertEqual(
            self.definition["States"]["CheckSubmission"]["Default"], "InvalidSubmission"
        )

    def test_invalid_job_is_forwarded_to_worker_without_changing_contract(self):
        job = {"repository": "unauthorized/project", "baseSha": "bad", "task": ""}
        kind, output, calls, _ = simulate(
            self.definition,
            {
                **self.replies,
                "PollWorker": [
                    {"StatusCode": 200, "ResponseBody": {"status": "error"}}
                ],
            },
            job=[job],
        )
        self.assertEqual(kind, "Fail")
        self.assertEqual(json.loads(calls[0][1]["RunHookPayload"]), job)
        self.assertEqual(output["termination"], "TERMINATED")

    def test_queued_success_persists_matching_keys_after_confirmed_termination(self):
        job = {"repository": "example/fixture", "baseSha": "b" * 40, "task": "Change"}
        key = {"PK": "JOBS", "SK": "2026-11-05T12:00:00.000000Z"}
        kind, output, calls, final = simulate(
            self.definition,
            {**self.replies, "CompleteQueuedJob": [{}]},
            job={"jobs": [job], "queue": key},
        )
        self.assertEqual((kind, final), ("Succeed", "JobSucceeded"))
        self.assertEqual(json.loads(calls[0][1]["RunHookPayload"]), job)
        self.assertEqual(
            [name for name, _ in calls][-3:],
            ["GetTerminationState", "GetTerminationState", "CompleteQueuedJob"],
        )
        self.assertEqual(
            calls[-1][1]["Key"], {name: {"S": value} for name, value in key.items()}
        )
        self.assertEqual(
            calls[-1][1]["ExpressionAttributeValues"], {":status": {"S": "COMPLETED"}}
        )
        self.assertEqual(
            output,
            {"microvmId": "microvm-test", "status": "ok", "termination": "TERMINATED"},
        )
        states = self.definition["States"]
        self.assertTrue(
            states["CompleteQueuedJob"]["Resource"].endswith(
                "aws-sdk:dynamodb:updateItem"
            )
        )
        self.assertEqual(states["CompleteQueuedJob"]["Next"], "EndResult")

    def test_queued_failures_and_unconfirmed_cleanup_do_not_release_queue(self):
        queued = {
            "jobs": [{"task": "Change"}],
            "queue": {"PK": "JOBS", "SK": "timestamp"},
        }
        for state, replies in (
            ("LaunchVM", [TaskError()]),
            ("PollWorker", [{"StatusCode": 200, "ResponseBody": {"status": "error"}}]),
            ("TerminateVM", [TaskError()]),
            ("GetTerminationState", [{"State": "RUNNING"}]),
        ):
            with self.subTest(state=state):
                kind, _, calls, _ = simulate(
                    self.definition, {**self.replies, state: replies}, job=queued
                )
                self.assertEqual(kind, "Fail")
                self.assertNotIn("CompleteQueuedJob", [name for name, _ in calls])

    def test_terminal_persistence_failure_does_not_report_success(self):
        kind, _, calls, final = simulate(
            self.definition,
            {**self.replies, "CompleteQueuedJob": [TaskError()]},
            job={
                "jobs": [{"task": "Change"}],
                "queue": {"PK": "JOBS", "SK": "timestamp"},
            },
        )
        self.assertEqual((kind, final), ("Fail", "CompleteQueuedJob"))
        self.assertEqual(
            [name for name, _ in calls][-2:],
            ["GetTerminationState", "CompleteQueuedJob"],
        )

    def test_pending_timeout_does_not_run_catch_cleanup(self):
        for state, reply in (
            ("GetVMState", {"State": "PENDING"}),
            ("PollWorker", {"StatusCode": 200, "ResponseBody": {"status": "pending"}}),
            ("GetTerminationState", {"State": "TERMINATING"}),
        ):
            with self.subTest(state=state):
                kind, output, calls, _ = simulate(
                    self.definition,
                    {**self.replies, state: [reply] * 340},
                    limit=1500,
                )
                self.assertEqual(kind, "TIMED_OUT")
                if state != "GetTerminationState":
                    self.assertNotIn("TerminateVM", [name for name, _ in calls])
                self.assertNotIn("termination", output)


if __name__ == "__main__":
    unittest.main()
