# Provision webhook ingestion and stream dispatch in the supplied workflow stack, preserving construct paths and scoped permissions.
from pathlib import Path

from aws_cdk import CfnParameter, Duration, RemovalPolicy
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs

ROOT = Path(__file__).resolve().parents[2]


# Add the signed issue receiver for the supplied image settings and queue; return its public URL without introducing a construct scope.
def create_receiver(scope, image, jobs):
    signing_secret = CfnParameter(
        scope,
        "WebhookSigningSecret",
        type="String",
        no_echo=True,
        min_length=1,
        description="GitHub signing secret; supply privately, never in source or command history.",
    )
    receiver_logs = logs.LogGroup(
        scope, "ReceiverLogs", removal_policy=RemovalPolicy.RETAIN
    )
    receiver_role = iam.Role(
        scope,
        "ReceiverRole",
        assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
    )
    receiver_logs.grant_write(receiver_role)
    receiver = lambda_.Function(
        scope,
        "IssueReceiver",
        runtime=lambda_.Runtime.PYTHON_3_13,
        handler="handler.handle",
        code=lambda_.Code.from_asset(
            str(ROOT / "src/receiver"), exclude=["__pycache__", "*.pyc"]
        ),
        timeout=Duration.seconds(30),
        log_group=receiver_logs,
        role=receiver_role,
        environment={
            "AUTHORIZED_REPOSITORY": image.repository,
            "PR_TARGET_BRANCH": image.target_branch,
            "GITHUB_SECRET_ARN": image.github_secret_arn,
            "WEBHOOK_SECRET": signing_secret.value_as_string,
            "JOB_TABLE_NAME": jobs.table_name,
        },
    )
    receiver.add_to_role_policy(
        iam.PolicyStatement(actions=["dynamodb:PutItem"], resources=[jobs.table_arn])
    )
    receiver.add_to_role_policy(
        iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[image.github_secret_arn],
        )
    )
    url = receiver.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE)
    return url.url


# Add the dispatcher and filtered stream mapping for the supplied queue/workflow; keep resources in the existing stack scope and return nothing.
def create_dispatcher(scope, jobs, machine):
    dispatcher_logs = logs.LogGroup(
        scope, "DispatcherLogs", removal_policy=RemovalPolicy.RETAIN
    )
    dispatcher_role = iam.Role(
        scope,
        "DispatcherRole",
        assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
    )
    dispatcher_logs.grant_write(dispatcher_role)
    dispatcher = lambda_.Function(
        scope,
        "Dispatcher",
        runtime=lambda_.Runtime.PYTHON_3_13,
        handler="handler.handle",
        code=lambda_.Code.from_asset(
            str(ROOT / "src/dispatcher"), exclude=["__pycache__", "*.pyc"]
        ),
        timeout=Duration.seconds(30),
        log_group=dispatcher_logs,
        role=dispatcher_role,
        environment={
            "JOB_TABLE_NAME": jobs.table_name,
            "STATE_MACHINE_ARN": machine.state_machine_arn,
        },
    )
    dispatcher.add_to_role_policy(
        iam.PolicyStatement(
            actions=["dynamodb:Query", "dynamodb:UpdateItem"],
            resources=[jobs.table_arn],
        )
    )
    dispatcher.add_to_role_policy(
        iam.PolicyStatement(
            actions=[
                "dynamodb:DescribeStream",
                "dynamodb:GetRecords",
                "dynamodb:GetShardIterator",
            ],
            resources=[jobs.table_stream_arn],
        )
    )
    machine.grant_start_execution(dispatcher)
    mapping = lambda_.CfnEventSourceMapping(
        scope,
        "JobNotifications",
        function_name=dispatcher.function_name,
        event_source_arn=jobs.table_stream_arn,
        starting_position="TRIM_HORIZON",
        filter_criteria=lambda_.CfnEventSourceMapping.FilterCriteriaProperty(
            filters=[
                lambda_.CfnEventSourceMapping.FilterProperty(
                    pattern='{"eventName":["INSERT"],"dynamodb":{"NewImage":{"status":{"S":["PENDING"]}}}}'
                ),
                lambda_.CfnEventSourceMapping.FilterProperty(
                    pattern='{"eventName":["MODIFY"],"dynamodb":{"NewImage":{"status":{"S":["COMPLETED","FAILED"]}}}}'
                ),
            ]
        ),
    )
    mapping.node.add_dependency(dispatcher_role)
