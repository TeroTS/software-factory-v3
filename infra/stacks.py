# Define native CDK storage/image/workflow stacks; never import or adopt Gate 1 resources.
import shutil
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    CfnParameter,
    CliCredentialsStackSynthesizer,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_events as events,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_s3_assets as assets,
)
from aws_cdk import (
    aws_stepfunctions as sfn,
)

from infra.workflow import CONNECTOR, definition

SECRET = "arn:aws:secretsmanager:us-east-1:172318459814:secret:software-factory-poc/github-token-sXayGQ"
ROOT = Path(__file__).resolve().parents[1]


# Stage Dockerfile, runtime dependency pins and worker sources as a content-addressed ZIP.
def stage_source(outdir):
    source = Path(outdir) / "factory-source"
    (source / "worker").mkdir(parents=True, exist_ok=True)
    (source / "image").mkdir(exist_ok=True)
    for name in ("acceptance.py", "requirements.txt"):
        shutil.copyfile(ROOT / "image" / name, source / "image" / name)
    shutil.copyfile(ROOT / "image/Dockerfile", source / "Dockerfile")
    for name in ("server.py", "task.py", "evidence.py"):
        shutil.copyfile(ROOT / "worker" / name, source / "worker" / name)
    return str(source)


# Create the private artifact bucket first; no custom-resource upload/delete provider.
class Artifacts(Stack):
    # Fixed account/Region name lets the next stack publish assets without bootstrap mutation.
    def __init__(self, scope, construct_id, **kwargs):
        super().__init__(
            scope, construct_id, synthesizer=CliCredentialsStackSynthesizer(), **kwargs
        )
        self.bucket_name = (
            f"software-factory-poc-gate2-artifacts-{self.account}-{self.region}"
        )
        bucket = s3.Bucket(
            self,
            "Artifacts",
            bucket_name=self.bucket_name,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        CfnOutput(self, "ArtifactBucket", value=bucket.bucket_name)


# Own full native image configuration, scoped build/runtime roles, and CloudWatch logs.
class Image(Stack):
    # Deployment uses operator credentials; assets go to the new private bucket, not the image.
    def __init__(self, scope, construct_id, artifact_bucket, **kwargs):
        super().__init__(
            scope,
            construct_id,
            synthesizer=CliCredentialsStackSynthesizer(
                file_assets_bucket_name=artifact_bucket, bucket_prefix="image/"
            ),
            **kwargs,
        )
        log_group = logs.LogGroup(
            self,
            "Logs",
            log_group_name="/aws/lambda-microvms/software-factory-poc",
            removal_policy=RemovalPolicy.RETAIN,
        )
        self.log_group_name = log_group.log_group_name
        build = iam.Role(
            self,
            "BuildRole",
            role_name="software-factory-poc-build",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        self.runtime = iam.Role(
            self,
            "RuntimeRole",
            role_name="software-factory-poc-runtime",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        for role in (build, self.runtime):
            role.assume_role_policy.add_statements(
                iam.PolicyStatement(
                    actions=["sts:TagSession"],
                    principals=[iam.ServicePrincipal("lambda.amazonaws.com")],
                )
            )
            role.add_to_policy(
                iam.PolicyStatement(
                    actions=[
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                    ],
                    resources=[log_group.log_group_arn],
                )
            )
        source = assets.Asset(self, "Source", path=stage_source(self.node.root.outdir))
        build.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[f"arn:aws:s3:::{artifact_bucket}/{source.s3_object_key}"],
            )
        )
        self.runtime.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"], resources=[SECRET]
            )
        )
        self.runtime.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=[
                    f"arn:aws:bedrock:us-east-1:{self.account}:inference-profile/us.openai.gpt-6.1-sol",
                    *[
                        f"arn:aws:bedrock:{region}::foundation-model/openai.gpt-6.1-sol"
                        for region in ("us-east-1", "us-east-2", "us-west-2")
                    ],
                    f"arn:aws:bedrock:us-east-1:{self.account}:project/default",
                ],
            )
        )
        self.image = lambda_.CfnMicrovmImage(
            self,
            "Image",
            name="software-factory-poc",
            description="Gate 2 worker; Codex 0.166.0, gh 2.103.3, AWS CLI 2.38.0",
            base_image_arn="arn:aws:lambda:us-east-1:aws:microvm-image:al2023-1",
            # Creation accepts a major version; read-back may resolve it to a minor.
            base_image_version="3",
            build_role_arn=build.role_arn,
            code_artifact={"uri": source.s3_object_url},
            cpu_configurations=[
                lambda_.CfnMicrovmImage.CpuConfigurationProperty(architecture="ARM_64")
            ],
            resources=[
                lambda_.CfnMicrovmImage.ResourcesProperty(minimum_memory_in_mib=2048)
            ],
            additional_os_capabilities=[],
            egress_network_connectors=[CONNECTOR + "INTERNET_EGRESS"],
            logging=lambda_.CfnMicrovmImage.LoggingProperty(
                cloud_watch=lambda_.CfnMicrovmImage.CloudWatchLoggingProperty(
                    log_group=log_group.log_group_name
                )
            ),
            environment_variables=[
                lambda_.CfnMicrovmImage.EnvironmentVariableProperty(
                    key=key, value=value
                )
                for key, value in (
                    ("GITHUB_SECRET_ARN", SECRET),
                    ("EXPECTED_RUNTIME_ROLE", self.runtime.role_name),
                )
            ],
            hooks=lambda_.CfnMicrovmImage.HooksProperty(
                port=8080,
                microvm_image_hooks=lambda_.CfnMicrovmImage.MicrovmImageHooksProperty(
                    ready="ENABLED",
                    ready_timeout_in_seconds=60,
                    validate="ENABLED",
                    validate_timeout_in_seconds=60,
                ),
                microvm_hooks=lambda_.CfnMicrovmImage.MicrovmHooksProperty(
                    run="ENABLED",
                    run_timeout_in_seconds=60,
                    terminate="ENABLED",
                    terminate_timeout_in_seconds=15,
                ),
            ),
        )
        self.image.node.add_dependency(build, log_group)
        CfnOutput(self, "ImageArn", value=self.image.attr_image_arn)
        CfnOutput(
            self,
            "LatestActiveVersion",
            value=self.image.attr_latest_active_image_version,
        )
        CfnOutput(self, "RuntimeRoleArn", value=self.runtime.role_arn)
        CfnOutput(self, "LogGroup", value=log_group.log_group_name)


# Own the connection and Standard workflow, with an explicit operator-verified version pin.
class Workflow(Stack):
    # Pin is required at deploy; latest-active fallback is never selected automatically.
    def __init__(self, scope, construct_id, image, **kwargs):
        super().__init__(
            scope, construct_id, synthesizer=CliCredentialsStackSynthesizer(), **kwargs
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
                actions=[
                    "lambda:RunMicrovm",
                    "lambda:GetMicrovm",
                    "lambda:TerminateMicrovm",
                    "lambda:CreateMicrovmAuthToken",
                    "lambda:GetMicrovmImage",
                    "lambda:GetMicrovmImageVersion",
                ],
                resources=[image.image.attr_image_arn],
            )
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
        machine = sfn.CfnStateMachine(
            self,
            "Workflow",
            state_machine_name="software-factory-poc",
            state_machine_type="STANDARD",
            role_arn=role.role_arn,
            definition_string=self.to_json_string(
                definition(
                    image.image.attr_image_arn,
                    version.value_as_string,
                    image.runtime.role_arn,
                    connection.attr_arn,
                    image.log_group_name,
                )
            ),
        )
        machine.node.add_dependency(role)
        CfnOutput(self, "StateMachineArn", value=machine.ref)
        CfnOutput(self, "PinnedImageVersion", value=version.value_as_string)
        CfnOutput(self, "ExecutionRoleArn", value=role.role_arn)
        CfnOutput(self, "ConnectionArn", value=connection.attr_arn)
