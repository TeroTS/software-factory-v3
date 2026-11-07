# Define the pinned Standard workflow and its queue, composing ingress resources under the same stack scope.
from aws_cdk import (
    CfnOutput,
    CfnParameter,
    CliCredentialsStackSynthesizer,
    Duration,
    Stack,
)
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_events as events
from aws_cdk import aws_iam as iam
from aws_cdk import aws_stepfunctions as sfn

from src.infra.ingress import create_dispatcher, create_receiver
from src.infra.workflow import CONNECTOR, definition


# Own the connection and Standard workflow, with an explicit operator-verified version pin.
class Workflow(Stack):
    # Pin is required at deploy; latest-active fallback is never selected automatically.
    def __init__(self, scope, construct_id, image, **kwargs):
        super().__init__(
            scope,
            construct_id,
            synthesizer=CliCredentialsStackSynthesizer(
                file_assets_bucket_name=image.artifact_bucket, bucket_prefix="receiver/"
            ),
            **kwargs,
        )
        version = CfnParameter(
            self,
            "ImageVersion",
            type="String",
            allowed_pattern=r"[0-9]+\.[0-9]+",
            description="New build version verified SUCCESSFUL/ACTIVE; never silently reuse an older active version",
        )
        connection = events.CfnConnection(
            self,
            "Connection",
            authorization_type="API_KEY",
            auth_parameters=events.CfnConnection.AuthParametersProperty(
                api_key_auth_parameters=events.CfnConnection.ApiKeyAuthParametersProperty(
                    api_key_name="X-Poc-Connection",
                    api_key_value="software-factory-poc",
                )
            ),
        )
        role = iam.Role(
            self,
            "ExecutionRole",
            assumed_by=iam.ServicePrincipal(
                "states.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:states:{self.region}:{self.account}:stateMachine:*"
                    },
                },
            ),
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:PassNetworkConnector"],
                resources=[
                    CONNECTOR + name for name in ("ALL_INGRESS", "INTERNET_EGRESS")
                ],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[image.runtime.role_arn],
                conditions={
                    "StringEquals": {"iam:PassedToService": "lambda.amazonaws.com"}
                },
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["states:InvokeHTTPEndpoint"],
                resources=[
                    f"arn:aws:states:{self.region}:{self.account}:stateMachine:software-factory-poc"
                ],
                conditions={
                    "StringEquals": {"states:HTTPMethod": "GET"},
                    "StringLike": {
                        "states:HTTPEndpoint": "https://*.lambda-microvm.us-east-1.on.aws/status"
                    },
                },
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["events:RetrieveConnectionCredentials"],
                resources=[connection.attr_arn],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:DescribeSecret",
                ],
                resources=[connection.attr_secret_arn],
            )
        )
        # Share the explicit name with ASL so prospective validation needs no deployed table lookup.
        table_name = "software-factory-poc-jobs"
        jobs = dynamodb.Table(
            self,
            "JobsTable",
            table_name=table_name,
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            stream=dynamodb.StreamViewType.NEW_IMAGE,
        )
        machine = sfn.StateMachine(
            self,
            "Workflow",
            state_machine_name="software-factory-poc",
            state_machine_type=sfn.StateMachineType.STANDARD,
            role=role,
            timeout=Duration.minutes(28),
            comment="One VM coordinates implementation, verification, fresh review, gated PR merge and independent GitHub merged-state confirmation; completion also requires confirmed termination. Timeout/StopExecution rely on VM lifespan and manual recovery.",
            definition_body=sfn.DefinitionBody.from_chainable(
                definition(
                    self,
                    image.image.attr_image_arn,
                    version.value_as_string,
                    image.runtime.role_arn,
                    connection.attr_arn,
                    image.log_group_name,
                    table_name,
                    jobs.table_arn,
                )
            ),
        )
        # Preserve the deployed logical ID so this construct refactor does not replace the workflow.
        machine.node.default_child.override_logical_id("Workflow")
        CfnOutput(self, "StateMachineArn", value=machine.state_machine_arn)
        CfnOutput(self, "PinnedImageVersion", value=version.value_as_string)
        CfnOutput(self, "ExecutionRoleArn", value=role.role_arn)
        CfnOutput(self, "ConnectionArn", value=connection.attr_arn)
        webhook_url = create_receiver(self, image, jobs)
        create_dispatcher(self, image, jobs, machine)
        CfnOutput(self, "WebhookUrl", value=webhook_url)
        CfnOutput(self, "JobTableName", value=jobs.table_name)
