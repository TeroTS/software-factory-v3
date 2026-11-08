# Synthesize native Gate 2 stacks for the environment-selected account/Region; no AWS mutation here.
import os

from aws_cdk import App, Environment

from src.infra.artifacts_stack import Artifacts
from src.infra.image_stack import Image
from src.infra.workflow_stack import Workflow


# Define deployment order; artifact bucket precedes publishing and workflow needs a verified pin.
def main():
    app = App()
    env = Environment(
        account=os.environ["AWS_ACCOUNT_ID"], region=os.environ["AWS_REGION"]
    )
    storage = Artifacts(app, "FactoryArtifacts", env=env)
    image = Image(app, "FactoryImage", artifact_bucket=storage.bucket_name, env=env)
    image.add_stack_dependency(storage)
    Workflow(app, "FactoryWorkflow", image=image, env=env)
    app.synth()


if __name__ == "__main__":
    main()
