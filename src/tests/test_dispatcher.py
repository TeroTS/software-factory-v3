import copy
import json
import os
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(
            os.environ,
            {
                "PATH": os.environ["PATH"],
                "JOB_TABLE_NAME": "jobs",
                "STATE_MACHINE_ARN": "machine",
                "AUTHORIZED_REPOSITORY": "example/project",
                "PR_TARGET_BRANCH": "release/next",
                "GITHUB_SECRET_ARN": "repository-secret",
            },
            clear=True,
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.table = Mock()
        self.machine = Mock()
        self.secrets = Mock()
        self.secrets.get_secret_value.return_value = {
            "SecretString": "repository-token"
        }
        self.connection = Mock()
        self.result = Mock(status=200)
        self.result.read.return_value = json.dumps({"sha": "c" * 40}).encode()
        self.connection.getresponse.return_value = self.result
        connection = patch("http.client.HTTPSConnection", return_value=self.connection)
        connection.start()
        self.addCleanup(connection.stop)
        self.session = Mock()
        self.session.client.side_effect = lambda service, **kwargs: {
            "dynamodb": self.table,
            "stepfunctions": self.machine,
            "secretsmanager": self.secrets,
        }[service]
        session = patch("boto3.Session", return_value=self.session)
        session.start()
        self.addCleanup(session.stop)
        self.job = {
            "repository": "example/project",
            "baseSha": "b" * 40,
            "task": 'Change "greeting" — café\nTest \\ paths.',
        }
        self.items = [self.item(index) for index in range(3)]

    def item(self, index, status="PENDING"):
        return {
            "PK": {"S": "JOBS"},
            "SK": {"S": f"2026-11-05T12:00:0{index}.000000Z"},
            "status": {"S": status},
            "job": {"M": {key: {"S": value} for key, value in self.job.items()}},
        }

    def invoke(self, records=None):
        from src.dispatcher.handler import handle

        return handle({"Records": records or []}, None)

    def test_oldest_pending_is_marked_before_one_start_for_entire_batch(self):
        self.table.query.side_effect = [{"Items": []}, {"Items": self.items}]

        def started(**request):
            self.assertEqual(self.table.update_item.call_count, 2)
            self.secrets.get_secret_value.assert_called_once_with(
                SecretId="repository-secret"
            )
            self.assertEqual(
                self.connection.request.call_args.args,
                (
                    "GET",
                    "/repos/example/project/commits/release%2Fnext",
                ),
            )
            self.assertEqual(
                self.connection.request.call_args.kwargs["headers"]["Authorization"],
                "Bearer repository-token",
            )
            persisted = self.table.update_item.call_args.kwargs
            self.assertEqual(persisted["UpdateExpression"], "SET job.baseSha = :sha")
            self.assertEqual(
                persisted["ExpressionAttributeValues"], {":sha": {"S": "c" * 40}}
            )
            self.assertEqual(request["stateMachineArn"], "machine")
            self.assertEqual(
                json.loads(request["input"]),
                {
                    "jobs": [{**self.job, "baseSha": "c" * 40}],
                    "queue": {"PK": "JOBS", "SK": "2026-11-05T12:00:00.000000Z"},
                },
            )
            return {"executionArn": "started"}

        def token(**request):
            self.table.update_item.assert_called_once()
            self.assertEqual(request, {"SecretId": "repository-secret"})
            return {"SecretString": "repository-token"}

        self.secrets.get_secret_value.side_effect = token
        before = copy.deepcopy(self.items)
        self.machine.start_execution.side_effect = started
        self.invoke([{"newest": True}, {"unrelated": True}, {}])
        self.machine.start_execution.assert_called_once()
        self.assertEqual(self.items, before)
        update = self.table.update_item.call_args_list[0].kwargs
        self.assertEqual(
            update["Key"],
            {"PK": {"S": "JOBS"}, "SK": {"S": "2026-11-05T12:00:00.000000Z"}},
        )
        self.assertEqual(
            update["ExpressionAttributeValues"], {":status": {"S": "IN_PROGRESS"}}
        )
        self.machine.describe_execution.assert_not_called()
        queries = [call.kwargs for call in self.table.query.call_args_list]
        self.assertEqual(len(queries), 2)
        for query in queries:
            self.assertTrue(query["ConsistentRead"])
            self.assertTrue(query["ScanIndexForward"])
            self.assertEqual(query["TableName"], "jobs")
            self.assertEqual(query["KeyConditionExpression"], "PK = :partition")
            self.assertEqual(query["FilterExpression"], "#status = :status")
            self.assertNotIn("Limit", query)
            self.assertNotIn("IndexName", query)
            self.assertNotIn("ExclusiveStartKey", query)
        self.assertEqual(
            queries[0]["ExpressionAttributeValues"][":status"], {"S": "IN_PROGRESS"}
        )
        self.assertEqual(
            queries[1]["ExpressionAttributeValues"][":status"], {"S": "PENDING"}
        )

    def test_active_job_or_empty_page_starts_nothing_without_pagination(self):
        for pages, count in (
            ([{"Items": [self.item(0, "IN_PROGRESS")]}], 1),
            (
                [
                    {"Items": [], "LastEvaluatedKey": self.items[0]},
                    {"Items": [], "LastEvaluatedKey": self.items[-1]},
                ],
                2,
            ),
        ):
            with self.subTest(pages=pages):
                self.table.reset_mock()
                self.table.query.side_effect = pages
                self.invoke([{}, {}])
                self.assertEqual(self.table.query.call_count, count)
                self.table.update_item.assert_not_called()
                self.machine.start_execution.assert_not_called()

    def test_three_jobs_run_in_order_only_after_previous_cleanup_and_completion(self):
        from src.tests.test_workflow import WorkflowFixture, simulate

        fixture = WorkflowFixture()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        # Terminal items precede all pending Jobs, exposing DynamoDB's filter-after-read semantics.
        items = [self.item(0, "COMPLETED"), self.item(1, "FAILED")]
        self.items = [self.item(index) for index in range(2, 5)]
        items.extend(copy.deepcopy(self.items))
        by_key = {item["SK"]["S"]: item for item in items}
        starts = []

        def query(**request):
            evaluated = items[: request.get("Limit", len(items))]
            wanted = request["ExpressionAttributeValues"][":status"]
            return {
                "Items": [
                    copy.deepcopy(item)
                    for item in evaluated
                    if item["status"] == wanted
                ]
            }

        def update(**request):
            item = by_key[request["Key"]["SK"]["S"]]
            values = request["ExpressionAttributeValues"]
            if ":sha" in values:
                item["job"]["M"]["baseSha"] = values[":sha"]
            else:
                item["status"] = values[":status"]

        self.table.query.side_effect = query
        self.table.update_item.side_effect = update
        self.machine.start_execution.side_effect = lambda **request: starts.append(
            json.loads(request["input"])
        )
        for index in range(3):
            self.invoke([{"notification": "not-the-selected-job"}] * 3)
            self.assertEqual(len(starts), index + 1)
            self.assertEqual(starts[-1]["queue"]["SK"], self.items[index]["SK"]["S"])
            self.invoke([{}])
            self.assertEqual(len(starts), index + 1)

            def complete(request, index=index):
                self.assertEqual(
                    request["Key"], {"PK": {"S": "JOBS"}, "SK": self.items[index]["SK"]}
                )
                self.assertEqual(
                    request["ExpressionAttributeValues"],
                    {":status": {"S": "COMPLETED"}},
                )
                update(**request)
                return {}

            kind, _, calls, _ = simulate(
                fixture.definition,
                {**fixture.replies, "CompleteQueuedJob": complete},
                job=starts[-1],
            )
            self.assertEqual(kind, "Succeed")
            self.assertEqual(
                [name for name, _ in calls][-2:],
                ["GetTerminationState", "CompleteQueuedJob"],
            )
        self.invoke([{}])
        self.assertEqual(len(starts), 3)
        self.assertTrue(all(item["status"] == {"S": "COMPLETED"} for item in items[2:]))

    def test_preparation_and_uncertain_start_failures_remain_blocking(self):
        for failure in ("missing-sha", "resolution", "persistence", "start"):
            with self.subTest(failure=failure):
                self.table.reset_mock()
                self.machine.reset_mock()
                self.result.read.reset_mock(side_effect=True)
                active = []
                items = copy.deepcopy(self.items)

                def query(active=active, items=items, **request):
                    if (
                        request["ExpressionAttributeValues"][":status"]["S"]
                        == "IN_PROGRESS"
                    ):
                        return {"Items": active}
                    return {"Items": items}

                def update(active=active, items=items, failure=failure, **request):
                    if ":status" in request["ExpressionAttributeValues"]:
                        active.append({**items[0], "status": {"S": "IN_PROGRESS"}})
                    elif failure == "persistence":
                        raise OSError("github_pat_private persistence detail")

                self.table.query.side_effect = query
                self.table.update_item.side_effect = update
                self.result.read.return_value = json.dumps(
                    {"sha": None if failure == "missing-sha" else "c" * 40}
                ).encode()
                if failure == "resolution":
                    self.result.read.side_effect = OSError(
                        "github_pat_private resolution detail"
                    )
                self.machine.start_execution.side_effect = (
                    OSError("github_pat_private uncertain response")
                    if failure == "start"
                    else None
                )
                with self.assertRaises(RuntimeError) as raised:
                    self.invoke([{}])
                self.assertNotIn("github_pat_private", str(raised.exception))
                self.assertTrue(raised.exception.__suppress_context__)
                self.assertEqual(
                    self.machine.start_execution.call_count, int(failure == "start")
                )
                count = self.table.update_item.call_count
                items.append(self.item(4))
                for records in ([{}], [{"duplicate": True}] * 3, [{"unrelated": True}]):
                    self.invoke(records)
                self.assertEqual(self.table.update_item.call_count, count)
                self.assertEqual(
                    self.machine.start_execution.call_count, int(failure == "start")
                )
                self.assertEqual(active[0]["status"], {"S": "IN_PROGRESS"})

    def test_invalid_deployment_scope_blocks_claim_without_resolution(self):
        for setting, value in (
            ("AUTHORIZED_REPOSITORY", "other/../repository"),
            ("PR_TARGET_BRANCH", " "),
            ("GITHUB_SECRET_ARN", ""),
            ("LOCAL_AWS_ENDPOINT", "http://attacker"),
            ("LOCAL_GITHUB_ENDPOINT", "http://attacker"),
        ):
            with (
                self.subTest(setting=setting),
                patch.dict(os.environ, {setting: value}),
            ):
                self.table.reset_mock()
                self.table.query.side_effect = [{"Items": []}, {"Items": self.items}]
                with self.assertRaises(RuntimeError):
                    self.invoke([{}])
                self.table.update_item.assert_called_once()
                self.secrets.get_secret_value.assert_not_called()
                self.connection.request.assert_not_called()
                self.machine.start_execution.assert_not_called()

    def test_claimed_job_cannot_choose_a_different_repository(self):
        self.items[0]["job"]["M"]["repository"] = {"S": "other/project"}
        self.table.query.side_effect = [{"Items": []}, {"Items": self.items}]
        with self.assertRaises(RuntimeError):
            self.invoke([{}])
        self.table.update_item.assert_called_once()
        self.secrets.get_secret_value.assert_not_called()
        self.machine.start_execution.assert_not_called()

    def test_failed_mark_prevents_start_and_start_failure_keeps_mark(self):
        self.table.query.side_effect = [{"Items": []}, {"Items": self.items}]
        error = ClientError({"Error": {"Code": "AccessDeniedException"}}, "UpdateItem")
        self.table.update_item.side_effect = error
        with self.assertRaises(ClientError):
            self.invoke([{}])
        self.machine.start_execution.assert_not_called()
        self.table.update_item.side_effect = None
        self.table.query.side_effect = [{"Items": []}, {"Items": self.items}]
        self.machine.start_execution.side_effect = error
        with self.assertRaises(RuntimeError):
            self.invoke([{}])
        self.assertEqual(self.table.update_item.call_count, 3)
        self.machine.start_execution.assert_called_once()


if __name__ == "__main__":
    unittest.main()
