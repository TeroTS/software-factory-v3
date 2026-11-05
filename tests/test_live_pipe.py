import json
import os
import tempfile
import time
import unittest
import uuid

from aws_cdk import App, Environment
from aws_cdk.assertions import Template
from botocore.exceptions import ClientError

from infra.stacks import Artifacts, Image, Workflow
from tools import live


@unittest.skipUnless(
    os.environ.get("FACTORY_LIVE_PIPE_TEST") == "1",
    "opt-in AWS check: FACTORY_LIVE_PIPE_TEST=1; creates isolated billable resources",
)
class LivePipeTests(unittest.TestCase):
    def setUp(self):
        self.session = live.operator("AWSAdministratorAccess-172318459814")
        self.name = "factory-pipe-check-" + uuid.uuid4().hex
        with tempfile.TemporaryDirectory() as directory:
            app = App(outdir=directory)
            env = Environment(account=live.ACCOUNT, region=live.REGION)
            artifacts = Artifacts(app, "FactoryArtifacts", env=env)
            image = Image(
                app, "FactoryImage", artifact_bucket=artifacts.bucket_name, env=env
            )
            workflow = Workflow(app, "FactoryWorkflow", image=image, env=env)
            template = Template.from_stack(workflow)
            resources = template.find_resources("AWS::Pipes::Pipe")
            self.mapping = next(iter(resources.values()))["Properties"][
                "TargetParameters"
            ]
            encoded = next(
                iter(
                    template.find_resources("AWS::StepFunctions::StateMachine").values()
                )
            )["Properties"]["DefinitionString"]
            definition = json.loads(
                "".join(
                    part if isinstance(part, str) else "symbolic-value"
                    for part in encoded["Fn::Join"][1]
                )
            )
            # Execute the real entry states, ending before LaunchVM: no model, GitHub, or VM side effects.
            self.entry = {
                "StartAt": definition["StartAt"],
                "States": {
                    name: definition["States"][name]
                    for name in ("CheckSubmission", "Input", "InvalidSubmission")
                },
            }
            self.entry["States"]["Input"].pop("Next")
            self.entry["States"]["Input"]["End"] = True
        self.queue = self.aws("sqs", "create_queue", QueueName=self.name)["QueueUrl"]
        self.addCleanup(self.aws, "sqs", "delete_queue", QueueUrl=self.queue)
        queue_arn = self.aws(
            "sqs",
            "get_queue_attributes",
            QueueUrl=self.queue,
            AttributeNames=["QueueArn"],
        )["Attributes"]["QueueArn"]
        factory_role = self.aws(
            "stepfunctions", "describe_state_machine", stateMachineArn=live.MACHINE_ARN
        )["roleArn"]
        self.machine = self.aws(
            "stepfunctions",
            "create_state_machine",
            name=self.name,
            roleArn=factory_role,
            type="STANDARD",
            definition=json.dumps(self.entry),
        )["stateMachineArn"]
        self.addCleanup(
            self.aws,
            "stepfunctions",
            "delete_state_machine",
            stateMachineArn=self.machine,
        )
        pipe_arn = f"arn:aws:pipes:{live.REGION}:{live.ACCOUNT}:pipe/{self.name}"
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
                            "Principal": {"Service": "pipes.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                            "Condition": {"ArnEquals": {"aws:SourceArn": pipe_arn}},
                        }
                    ],
                }
            ),
        )["Role"]["Arn"]
        self.addCleanup(self.aws, "iam", "delete_role", RoleName=self.name)
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
                            "Action": [
                                "sqs:ReceiveMessage",
                                "sqs:DeleteMessage",
                                "sqs:ChangeMessageVisibility",
                                "sqs:GetQueueAttributes",
                                "sqs:GetQueueUrl",
                            ],
                            "Resource": queue_arn,
                        },
                        {
                            "Effect": "Allow",
                            "Action": "states:StartExecution",
                            "Resource": self.machine,
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
        self.log_group = "/aws/vendedlogs/pipes/" + self.name
        self.aws("logs", "create_log_group", logGroupName=self.log_group)
        self.addCleanup(
            self.aws, "logs", "delete_log_group", logGroupName=self.log_group
        )
        # Synthetic inputs only; detailed diagnostics never capture a factory Job or credentials.
        self.aws(
            "pipes",
            "create_pipe",
            Name=self.name,
            RoleArn=role,
            Source=queue_arn,
            Target=self.machine,
            DesiredState="STOPPED",
            SourceParameters={"SqsQueueParameters": {"BatchSize": 1}},
            TargetParameters=self.mapping,
            LogConfiguration={
                "Level": "ERROR",
                "IncludeExecutionData": ["ALL"],
                "CloudwatchLogsLogDestination": {
                    "LogGroupArn": f"arn:aws:logs:{live.REGION}:{live.ACCOUNT}:log-group:{self.log_group}"
                },
            },
        )
        self.addCleanup(self.delete_pipe)
        self.wait_for(
            lambda: (
                self.aws("pipes", "describe_pipe", Name=self.name)["CurrentState"]
                == "STOPPED"
            )
        )
        self.aws("pipes", "start_pipe", Name=self.name)
        self.wait_for(
            lambda: (
                self.aws("pipes", "describe_pipe", Name=self.name)["CurrentState"]
                == "RUNNING"
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
        self.fail("Isolated AWS Pipe observation timed out")

    def delete_pipe(self):
        self.aws("pipes", "delete_pipe", Name=self.name)

        def deleted():
            try:
                self.aws("pipes", "describe_pipe", Name=self.name)
            except ClientError as error:
                if error.response["Error"]["Code"] == "NotFoundException":
                    return True
                raise
            return False

        self.wait_for(deleted)

    def test_pipe_and_manual_submissions_use_one_envelope_and_preserve_job(self):
        tasks = [
            "Add multiplication and tests.",
            'Quotes: "multiply"; slash: \\; newline:\nUnicode: café — 東京.',
            'Literal JSON: {"nested": [1, 2]}; tab:\tend.',
        ]
        seen = set()
        for task in tasks:
            with self.subTest(task=task):
                job = {
                    "repository": "example/fixture",
                    "baseSha": "b" * 40,
                    "task": task,
                }
                self.aws(
                    "sqs",
                    "send_message",
                    QueueUrl=self.queue,
                    MessageBody=json.dumps(
                        job, ensure_ascii=False, separators=(",", ":")
                    ),
                )

                def started_or_failed():
                    executions = self.aws(
                        "stepfunctions", "list_executions", stateMachineArn=self.machine
                    )["executions"]
                    new = [e for e in executions if e["executionArn"] not in seen]
                    if new:
                        return new[0]["executionArn"]
                    logs = self.aws(
                        "logs", "filter_log_events", logGroupName=self.log_group
                    )["events"]
                    if logs:
                        self.fail(
                            "Real AWS Pipe rejected delivery: " + logs[-1]["message"]
                        )
                    return None

                arn = self.wait_for(started_or_failed)
                seen.add(arn)
                execution = self.aws(
                    "stepfunctions", "describe_execution", executionArn=arn
                )
                self.assertEqual(json.loads(execution["input"]), [job])
                self.wait_for(
                    lambda arn=arn: (
                        self.aws(
                            "stepfunctions", "describe_execution", executionArn=arn
                        )["status"]
                        != "RUNNING"
                    )
                )
                execution = self.aws(
                    "stepfunctions", "describe_execution", executionArn=arn
                )
                self.assertEqual(execution["status"], "SUCCEEDED")
                self.assertEqual(json.loads(execution["output"]), {"job": job})
        self.assertEqual(len(seen), len(tasks))
        manual = self.aws(
            "stepfunctions",
            "start_execution",
            stateMachineArn=self.machine,
            input=json.dumps([job]),
        )["executionArn"]
        self.wait_for(
            lambda: (
                self.aws("stepfunctions", "describe_execution", executionArn=manual)[
                    "status"
                ]
                != "RUNNING"
            )
        )
        execution = self.aws("stepfunctions", "describe_execution", executionArn=manual)
        self.assertEqual(execution["status"], "SUCCEEDED")
        self.assertEqual(json.loads(execution["output"]), {"job": job})

    def test_entry_rejects_direct_objects_and_invalid_batches(self):
        job = {"repository": "example/fixture", "baseSha": "b" * 40, "task": "Change"}
        for submission in (job, [], [job, job], [None], ["task"], [[job]], None, 0):
            with self.subTest(submission=submission):
                arn = self.aws(
                    "stepfunctions",
                    "start_execution",
                    stateMachineArn=self.machine,
                    input=json.dumps(submission),
                )["executionArn"]
                self.wait_for(
                    lambda arn=arn: (
                        self.aws(
                            "stepfunctions", "describe_execution", executionArn=arn
                        )["status"]
                        != "RUNNING"
                    )
                )
                execution = self.aws(
                    "stepfunctions", "describe_execution", executionArn=arn
                )
                self.assertEqual(execution["status"], "FAILED")
                self.assertEqual(execution["error"], "InvalidSubmission")
