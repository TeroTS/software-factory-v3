# Stage worker/skill sources and define the native image, empty GitHub secret, scoped roles, and retained logs; no AWS lookups.
import shutil
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    CfnParameter,
    CliCredentialsStackSynthesizer,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3_assets as assets
from aws_cdk import aws_secretsmanager as secretsmanager

from src.infra.workflow import CONNECTOR

ROOT = Path(__file__).resolve().parents[2]


# Rebuild the asset ZIP staging directory from current worker/image sources and the explicit trusted skill bundle.
def stage_source(outdir):
    source = Path(outdir) / "factory-source"
    if source.exists():
        shutil.rmtree(source)
    (source / "src/worker").mkdir(parents=True)
    (source / "image").mkdir()
    for name in ("requirements.txt", "managed_config.toml"):
        shutil.copyfile(ROOT / "image" / name, source / "image" / name)
    shutil.copyfile(ROOT / "image/Dockerfile", source / "Dockerfile")
    shutil.copyfile(ROOT / "src/__init__.py", source / "src/__init__.py")
    for name in ("__init__.py", "server.py", "task.py", "prompts.py", "review.py"):
        shutil.copyfile(ROOT / "src/worker" / name, source / "src/worker" / name)
    skills = {
        "implement": (),
        "code-review": (),
        "pr": ("CREDITS.md",),
        "tdd": ("tests.md", "mocking.md"),
        "codebase-design": ("DEEPENING.md", "DESIGN-IT-TWICE.md"),
    }
    for name, references in skills.items():
        relative = Path("image/skills") / name
        (source / relative / "agents").mkdir(parents=True)
        for filename in ("SKILL.md", "agents/openai.yaml", *references):
            shutil.copyfile(ROOT / relative / filename, source / relative / filename)
    return str(source)


# Own native image configuration, an operator-populated GitHub secret, scoped roles, and CloudWatch logs.
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
        self.artifact_bucket = artifact_bucket
        # No value or generator: populate manually after deploy; an automatic name avoids existing secrets.
        github_secret = secretsmanager.CfnSecret(
            self,
            "GitHubToken",
            description="Repository-scoped GitHub token; populate manually before accepting jobs.",
        )
        github_secret.apply_removal_policy(RemovalPolicy.RETAIN)
        self.github_secret_arn = github_secret.ref
        repository = CfnParameter(
            self,
            "AuthorizedRepository",
            type="String",
            default="TeroTS/factory-smoke-test",
            allowed_pattern=r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+",
            description="Trusted repository matching the manually populated repository-scoped GitHub token; changes require trust/scoping review.",
        )
        target = CfnParameter(
            self,
            "PrTargetBranch",
            type="String",
            default="main",
            min_length=1,
            description="PR target branch in the authorized repository.",
        )
        self.repository = repository.value_as_string
        self.target_branch = target.value_as_string
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
                actions=["secretsmanager:GetSecretValue"],
                resources=[self.github_secret_arn],
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
            description="Worker; Codex 0.166.0, gh 2.103.3, AWS CLI 2.38.0",
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
                    ("AUTHORIZED_REPOSITORY", repository.value_as_string),
                    ("PR_TARGET_BRANCH", target.value_as_string),
                    ("GITHUB_SECRET_ARN", self.github_secret_arn),
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
        CfnOutput(self, "GitHubSecretArn", value=self.github_secret_arn)
