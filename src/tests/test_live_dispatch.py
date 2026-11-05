import io
import json
import os
import tempfile
import time
import unittest
import uuid
import zipfile
from pathlib import Path

from aws_cdk import App, Environment
from aws_cdk.assertions import Template

from src.infra.stacks import Artifacts, Image, Workflow
from src.tools import live


@unittest.skipUnless(
    os.environ.get("FACTORY_LIVE_DISPATCH_TEST") == "1",
    "opt-in AWS check: FACTORY_LIVE_DISPATCH_TEST=1; creates isolated billable resources",
)
class LiveDispatchTests(unittest.TestCase):
    def setUp(self):
        self.session = live.operator("AWSAdministratorAccess-172318459814")
        self.name = "factory-dispatch-check-" + uuid.uuid4().hex
        with tempfile.TemporaryDirectory() as directory:
            app = App(outdir=directory)
            env = Environment(account=live.ACCOUNT, region=live.REGION)
            artifacts = Artifacts(app, "FactoryArtifacts", env=env)
            image = Image(
                app, "FactoryImage", artifact_bucket=artifacts.bucket_name, env=env
            )
            workflow = Workflow(app, "FactoryWorkflow", image=image, env=env)
            template = Template.from_stack(workflow)
            mapping = next(
                iter(
                    template.find_resources("AWS::Lambda::EventSourceMapping").values()
                )
            )["Properties"]
            self.filters = mapping["FilterCriteria"]
            encoded = template.find_resources("AWS::StepFunctions::StateMachine")[
                "Workflow"
            ]["Properties"]["DefinitionString"]
            definition = json.loads(
                "".join(
                    part if isinstance(part, str) else "symbolic-value"
                    for part in encoded["Fn::Join"][1]
                )
            )
            # Run the actual entry states only: no model, GitHub, or MicroVM effects.
            self.entry = {
                "StartAt": definition["StartAt"],
                "States": {
                    name: definition["States"][name]
                    for name in (
                        "CheckSubmission",
                        "Input",
                        "QueuedInput",
                        "InvalidSubmission",
                    )
                },
            }
            for name in ("Input", "QueuedInput"):
                self.entry["States"][name].pop("Next")
                self.entry["States"][name]["End"] = True
        role = self.aws(
            "iam",
            "create_role",
            RoleName=self.name,
            AssumeRolePolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {
                                "Service": [
                                    "lambda.amazonaws.com",
                                    "states.amazonaws.com",
                                ]
                            },
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
        )["Role"]["Arn"]
        self.addCleanup(self.aws, "iam", "delete_role", RoleName=self.name)
        table = self.aws(
            "dynamodb",
            "create_table",
            TableName=self.name,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
            StreamSpecification={"StreamEnabled": True, "StreamViewType": "NEW_IMAGE"},
        )["TableDescription"]
        self.addCleanup(self.aws, "dynamodb", "delete_table", TableName=self.name)
        self.wait_for(
            lambda: (
                self.aws("dynamodb", "describe_table", TableName=self.name)["Table"][
                    "TableStatus"
                ]
                == "ACTIVE"
            )
        )
        stream = self.aws("dynamodb", "describe_table", TableName=self.name)["Table"][
            "LatestStreamArn"
        ]
        self.log_group = "/aws/lambda/" + self.name
        self.aws("logs", "create_log_group", logGroupName=self.log_group)
        self.addCleanup(
            self.aws, "logs", "delete_log_group", logGroupName=self.log_group
        )
        machine_arn = (
            f"arn:aws:states:{live.REGION}:{live.ACCOUNT}:stateMachine:{self.name}"
        )
        self.aws(
            "iam",
            "put_role_policy",
            RoleName=self.name,
            PolicyName=self.name,
            PolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": ["dynamodb:Query", "dynamodb:UpdateItem"],
                            "Resource": table["TableArn"],
                        },
                        {
                            "Effect": "Allow",
                            "Action": [
                                "dynamodb:DescribeStream",
                                "dynamodb:GetRecords",
                                "dynamodb:GetShardIterator",
                            ],
                            "Resource": stream,
                        },
                        {
                            "Effect": "Allow",
                            "Action": "states:StartExecution",
                            "Resource": machine_arn,
                        },
                        {
                            "Effect": "Allow",
                            "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                            "Resource": f"arn:aws:logs:{live.REGION}:{live.ACCOUNT}:log-group:{self.log_group}:*",
                        },
                    ],
                }
            ),
        )
        self.addCleanup(
            self.aws,
            "iam",
            "delete_role_policy",
            RoleName=self.name,
            PolicyName=self.name,
        )
        # Allow IAM trust propagation before creating isolated service resources.
        time.sleep(10)
        self.machine = self.aws(
            "stepfunctions",
            "create_state_machine",
            name=self.name,
            roleArn=role,
            type="STANDARD",
            definition=json.dumps(self.entry),
        )["stateMachineArn"]
        self.addCleanup(
            self.aws,
            "stepfunctions",
            "delete_state_machine",
            stateMachineArn=self.machine,
        )
        with io.BytesIO() as data:
            with zipfile.ZipFile(data, "w") as archive:
                for path in (Path(__file__).resolve().parents[1] / "dispatcher").glob(
                    "*.py"
                ):
                    archive.write(path, path.name)
            code = data.getvalue()
        self.aws(
            "lambda",
            "create_function",
            FunctionName=self.name,
            Runtime="python3.13",
            Role=role,
            Handler="handler.handle",
            Code={"ZipFile": code},
            Timeout=30,
            Environment={
                "Variables": {
                    "JOB_TABLE_NAME": self.name,
                    "STATE_MACHINE_ARN": self.machine,
                }
            },
        )
        self.addCleanup(self.aws, "lambda", "delete_function", FunctionName=self.name)
        self.wait_for(
            lambda: (
                self.aws(
                    "lambda", "get_function_configuration", FunctionName=self.name
                )["State"]
                == "Active"
            )
        )
        mapping = self.aws(
            "lambda",
            "create_event_source_mapping",
            FunctionName=self.name,
            EventSourceArn=stream,
            StartingPosition="TRIM_HORIZON",
            FilterCriteria=self.filters,
        )
        self.addCleanup(self.delete_mapping, mapping["UUID"])
        self.wait_for(
            lambda: (
                self.aws("lambda", "get_event_source_mapping", UUID=mapping["UUID"])[
                    "State"
                ]
                == "Enabled"
            )
        )

    def aws(self, service, operation, **parameters):
        return live.aws(self.session, service, operation, **parameters)

    def wait_for(self, observe):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            result = observe()
            if result:
                return result
            time.sleep(3)
        self.fail("Isolated AWS Streams observation timed out")

    def delete_mapping(self, uuid):
        from botocore.exceptions import ClientError

        self.aws("lambda", "delete_event_source_mapping", UUID=uuid)

        def deleted():
            try:
                self.aws("lambda", "get_event_source_mapping", UUID=uuid)
            except ClientError as error:
                if error.response["Error"]["Code"] == "ResourceNotFoundException":
                    return True
                raise
            return False

        self.wait_for(deleted)

    def test_real_streams_dispatch_preserves_payload_and_direct_input(self):
        job = {
            "repository": "example/fixture",
            "baseSha": "b" * 40,
            "task": 'Quotes: "multiply"; slash: \\; newline:\nUnicode: café — 東京.',
        }
        key = {"PK": {"S": "JOBS"}, "SK": {"S": "2026-11-05T12:00:00.000000Z"}}
        self.aws(
            "dynamodb",
            "put_item",
            TableName=self.name,
            Item={
                **key,
                "status": {"S": "PENDING"},
                "job": {"M": {name: {"S": value} for name, value in job.items()}},
            },
        )
        executions = self.wait_for(
            lambda: self.aws(
                "stepfunctions", "list_executions", stateMachineArn=self.machine
            )["executions"]
        )
        execution = self.wait_for(lambda: self.finished(executions[0]["executionArn"]))
        reference = {name: value["S"] for name, value in key.items()}
        self.assertEqual(execution["status"], "SUCCEEDED")
        self.assertEqual(
            json.loads(execution["input"]), {"jobs": [job], "queue": reference}
        )
        self.assertEqual(
            json.loads(execution["output"]), {"job": job, "queue": reference}
        )
        self.assertEqual(
            self.aws(
                "dynamodb",
                "get_item",
                TableName=self.name,
                Key=key,
                ConsistentRead=True,
            )["Item"]["status"],
            {"S": "IN_PROGRESS"},
        )
        mapping = self.aws(
            "lambda", "list_event_source_mappings", FunctionName=self.name
        )["EventSourceMappings"][0]
        self.assertEqual(mapping["FilterCriteria"], self.filters)
        # These isolated entry tests have no VM; terminal writes explicitly simulate their release.
        # Drain the INSERT notification before terminal release, avoiding deliberately concurrent dispatch.
        for index, status in enumerate(("COMPLETED", "FAILED"), start=1):
            pending_key = {
                "PK": {"S": "JOBS"},
                "SK": {"S": f"2026-11-05T12:00:0{index}.000000Z"},
            }
            self.aws(
                "dynamodb",
                "put_item",
                TableName=self.name,
                Item={
                    **pending_key,
                    "status": {"S": "PENDING"},
                    "job": {"M": {name: {"S": value} for name, value in job.items()}},
                },
            )
            time.sleep(10)
            self.assertEqual(
                len(
                    self.aws(
                        "stepfunctions", "list_executions", stateMachineArn=self.machine
                    )["executions"]
                ),
                index,
            )
            self.aws(
                "dynamodb",
                "update_item",
                TableName=self.name,
                Key=key,
                UpdateExpression="SET #s = :s",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":s": {"S": status}},
            )
            self.wait_for(
                lambda index=index: (
                    len(
                        self.aws(
                            "stepfunctions",
                            "list_executions",
                            stateMachineArn=self.machine,
                        )["executions"]
                    )
                    == index + 1
                )
            )
            key = pending_key
            self.wait_for(
                lambda key=key: (
                    self.aws(
                        "dynamodb",
                        "get_item",
                        TableName=self.name,
                        Key=key,
                        ConsistentRead=True,
                    )["Item"]["status"]
                    == {"S": "IN_PROGRESS"}
                )
            )
        self.aws(
            "dynamodb",
            "update_item",
            TableName=self.name,
            Key=key,
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": {"S": "COMPLETED"}},
        )
        # No active or pending queued Jobs remain; direct entry starts carry no queue reference.
        manual = self.aws(
            "stepfunctions",
            "start_execution",
            stateMachineArn=self.machine,
            input=json.dumps([job]),
        )["executionArn"]
        execution = self.wait_for(lambda: self.finished(manual))
        self.assertEqual(execution["status"], "SUCCEEDED")
        self.assertEqual(json.loads(execution["output"]), {"job": job})

    def finished(self, arn):
        execution = self.aws("stepfunctions", "describe_execution", executionArn=arn)
        return execution if execution["status"] != "RUNNING" else None

    def test_entry_rejects_direct_objects_and_invalid_batches(self):
        job = {"repository": "example/fixture", "baseSha": "b" * 40, "task": "Change"}
        for submission in (
            job,
            [],
            [job, job],
            [None],
            ["task"],
            [[job]],
            None,
            0,
            {"jobs": [job]},
            {"jobs": [job], "queue": {"PK": "OTHER", "SK": "timestamp"}},
        ):
            with self.subTest(submission=submission):
                arn = self.aws(
                    "stepfunctions",
                    "start_execution",
                    stateMachineArn=self.machine,
                    input=json.dumps(submission),
                )["executionArn"]
                execution = self.wait_for(lambda arn=arn: self.finished(arn))
                self.assertEqual(execution["status"], "FAILED")
                self.assertEqual(execution["error"], "InvalidSubmission")


if __name__ == "__main__":
    unittest.main()
