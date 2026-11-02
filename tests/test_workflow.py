import copy
import json
import re
import tempfile
import unittest

from aws_cdk import App, Environment
from aws_cdk.assertions import Template

from infra.stacks import Artifacts, Image, Workflow


class TaskError(Exception):
    pass


def lookup(data, path, context):
    if path.startswith("$$"):
        data, path = context, "$" + path[2:]
    for key in re.findall(r"\.([\w-]+)|\['([^']+)'\]", path):
        data = data[key[0] or key[1]]
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
    if "And" in rule:
        return all(choice(item, data, context) for item in rule["And"])
    if "Or" in rule:
        return any(choice(item, data, context) for item in rule["Or"])
    if "Not" in rule:
        return not choice(rule["Not"], data, context)
    try:
        value = lookup(data, rule["Variable"], context)
    except (KeyError, TypeError):
        return rule.get("IsPresent") is False
    if "IsPresent" in rule:
        return rule["IsPresent"]
    if "IsString" in rule:
        return isinstance(value, str) == rule["IsString"]
    if "IsNumeric" in rule:
        return (type(value) in (int, float)) == rule["IsNumeric"]
    if "StringMatches" in rule:
        # ASL supports only '*' as wildcard; shell '?' globs are literal here.
        pattern = re.escape(rule["StringMatches"]).replace(r"\*", ".*")
        return isinstance(value, str) and re.fullmatch(pattern, value) is not None
    return value == rule.get("StringEquals", rule.get("NumericEquals"))


def simulate(definition, replies, limit=100, job=None):
    data = (
        job
        if job is not None
        else {
            "repository": "example/authorized-project",
            "baseSha": "a" * 40,
            "task": "Update greeting and tests",
        }
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
            if kind == "Fail":
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


class WorkflowFixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        app = App(outdir=directory.name)
        env = Environment(account="172318459814", region="us-east-1")
        artifacts = Artifacts(app, "Artifacts", env=env)
        image = Image(app, "Image", artifact_bucket=artifacts.bucket_name, env=env)
        workflow = Workflow(app, "Workflow", image=image, env=env)
        resource = next(
            iter(
                Template.from_stack(workflow)
                .find_resources("AWS::StepFunctions::StateMachine")
                .values()
            )
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
        self.assertEqual(self.definition["StartAt"], "Input")
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

    def test_known_vm_string_guard_matches_actual_asl_not_shell_globs(self):
        rule = self.definition["States"]["CheckLaunch"]["Choices"][0]
        self.assertTrue(choice(rule, {"vm": {"MicrovmId": "microvm-real"}}, {}))
        for value in ("", None, 1):
            self.assertFalse(choice(rule, {"vm": {"MicrovmId": value}}, {}))
        self.assertFalse(choice(rule, {"vm": {}}, {}))
        self.assertFalse(
            choice(
                {"Variable": "$.value", "StringMatches": "?*"},
                {"value": "microvm-real"},
                {},
            )
        )

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
            job=job,
        )
        self.assertEqual(kind, "Fail")
        self.assertEqual(json.loads(calls[0][1]["RunHookPayload"]), job)
        self.assertEqual(output["termination"], "TERMINATED")

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
