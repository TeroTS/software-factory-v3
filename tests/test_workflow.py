import copy
import importlib
import json
import re
import unittest


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
    if "StringMatches" in rule:
        # ASL supports only '*' as wildcard; shell '?' globs are literal here.
        pattern = re.escape(rule["StringMatches"]).replace(r"\*", ".*")
        return isinstance(value, str) and re.fullmatch(pattern, value) is not None
    return value == rule.get("StringEquals", rule.get("NumericEquals"))


def simulate(definition, replies, limit=100):
    data = {
        "repository": "TeroTS/factory-smoke-test",
        "baseSha": "a" * 40,
        "task": "multiply",
    }
    context = {"Execution": {"Name": "same-execution"}}
    state = definition["StartAt"]
    calls, elapsed = [], 0
    replies = {name: iter(items) for name, items in replies.items()}
    for _ in range(limit):
        step = definition["States"][state]
        kind = step["Type"]
        if kind in ("Succeed", "Fail"):
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
                result = next(replies[state])
                if isinstance(result, Exception):
                    catch = step.get("Catch")
                    if not catch:
                        return "Fail", data, calls, state
                    data[catch[0]["ResultPath"][2:]] = {"Error": "simulated"}
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


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(
            importlib.util.find_spec("infra.workflow"), "Workflow is missing"
        )
        from infra.workflow import definition

        self.definition = definition(
            "image-arn", "7.0", "role-arn", "connection-arn", "logs"
        )
        self.replies = {
            "InspectImage": [{"State": "UPDATED"}],
            "InspectVersion": [{"State": "SUCCESSFUL", "Status": "ACTIVE"}],
            "Launch": [
                {
                    "MicrovmId": "microvm-test",
                    "Endpoint": "vm.lambda-microvm.us-east-1.on.aws",
                }
            ],
            "Readiness": [{"State": "PENDING"}, {"State": "RUNNING"}],
            "Token": [{"AuthToken": {"X-aws-proxy-auth": "secret-jwe"}}],
            "Poll": [
                {"StatusCode": 200, "ResponseBody": {"status": "pending"}},
                {"StatusCode": 200, "ResponseBody": {"status": "ok"}},
            ],
            "Terminate": [{}],
            "Confirm": [{"State": "TERMINATING"}, {"State": "TERMINATED"}],
        }

    def test_known_vm_string_guard_matches_actual_asl_not_shell_globs(self):
        rule = self.definition["States"]["KnownVM"]["Choices"][0]
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
        kind, output, calls, _ = simulate(self.definition, self.replies)
        self.assertEqual(kind, "Succeed")
        self.assertEqual(
            output,
            {"microvmId": "microvm-test", "status": "ok", "termination": "TERMINATED"},
        )
        polls = [args for name, args in calls if name == "Poll"]
        self.assertEqual(len(polls), 2)
        self.assertEqual(polls[0]["Headers"], polls[1]["Headers"])
        self.assertEqual(sum(name == "Token" for name, _ in calls), 1)
        self.assertEqual(sum(name == "Launch" for name, _ in calls), 1)
        launch = next(args for name, args in calls if name == "Launch")
        self.assertEqual(launch["ClientToken"], "same-execution")
        self.assertEqual(launch["MaximumDurationInSeconds"], 1800)
        self.assertEqual(
            json.loads(launch["RunHookPayload"])["repository"],
            "TeroTS/factory-smoke-test",
        )
        self.assertEqual(self.definition["TimeoutSeconds"], 1680)
        self.assertEqual(self.definition["States"]["Poll"]["TimeoutSeconds"], 10)
        self.assertEqual(self.definition["States"]["PendingWait"]["Seconds"], 30)

    def test_bad_status_and_http_errors_share_cleanup(self):
        for body in (
            {},
            {"status": "error"},
            {"status": "unknown"},
            {"status": "ok", "extra": True},
            None,
            "not-json",
        ):
            with self.subTest(body=body):
                replies = {
                    **self.replies,
                    "Poll": [{"StatusCode": 200, "ResponseBody": body}],
                }
                kind, output, calls, _ = simulate(self.definition, replies)
                self.assertEqual(kind, "Fail")
                self.assertIn("Terminate", [name for name, _ in calls])
                self.assertNotIn("secret-jwe", json.dumps(output))
        for reply in (TaskError(), {"StatusCode": 502}, {"StatusCode": 200}):
            kind, _, calls, _ = simulate(
                self.definition, {**self.replies, "Poll": [reply]}
            )
            self.assertEqual(kind, "Fail")
            self.assertIn("Confirm", [name for name, _ in calls])

    def test_prelaunch_failures_do_not_cleanup_unknown_vm(self):
        for state, reply in (
            ("Launch", TaskError()),
            ("Launch", {}),
            ("InspectVersion", {"State": "FAILED", "Status": "INACTIVE"}),
        ):
            kind, _, calls, _ = simulate(
                self.definition, {**self.replies, state: [reply]}
            )
            self.assertEqual(kind, "Fail")
            self.assertNotIn("Terminate", [name for name, _ in calls])

    def test_postlaunch_failures_never_succeed(self):
        for state, reply in (
            ("Launch", {"MicrovmId": "microvm-test"}),
            ("Readiness", {"State": "TERMINATED"}),
            ("Readiness", TaskError()),
            ("Token", TaskError()),
            ("Token", {}),
        ):
            kind, _, calls, _ = simulate(
                self.definition, {**self.replies, state: [reply]}
            )
            self.assertEqual(kind, "Fail")
            self.assertIn("Terminate", [name for name, _ in calls])

    def test_cleanup_failure_does_not_claim_termination(self):
        for state in ("Terminate", "Confirm"):
            kind, output, _, final = simulate(
                self.definition, {**self.replies, state: [TaskError()]}
            )
            self.assertEqual(kind, "Fail")
            self.assertEqual(final, "CleanupFailed")
            self.assertNotIn("termination", output)

    def test_pending_timeout_does_not_run_catch_cleanup(self):
        replies = {
            **self.replies,
            "Poll": [{"StatusCode": 200, "ResponseBody": {"status": "pending"}}] * 60,
        }
        kind, _, calls, _ = simulate(self.definition, replies, limit=500)
        self.assertEqual(kind, "TIMED_OUT")
        self.assertNotIn("Terminate", [name for name, _ in calls])


if __name__ == "__main__":
    unittest.main()
