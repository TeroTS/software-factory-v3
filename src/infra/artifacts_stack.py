# Define the private artifact storage stack.
from aws_cdk import CfnOutput, CliCredentialsStackSynthesizer, RemovalPolicy, Stack
from aws_cdk import aws_s3 as s3


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
