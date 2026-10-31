# Read back the exact new image artifact/version before pinning; never create/update or launch.
import argparse
import json
import subprocess
from pathlib import Path


# Refuse pending/failed images, inactive versions, or fallback to an older build/source.
def verify(image, version, requested, artifact_uri):
    if (
        image.get("state") not in ("CREATED", "UPDATED")
        or image.get("latestActiveImageVersion") != requested
    ):
        raise ValueError(
            "New image build is not ready/current; do not use an older active version"
        )
    if (
        version.get("state") != "SUCCESSFUL"
        or version.get("status") != "ACTIVE"
        or version.get("imageVersion") != requested
        or version.get("imageArn") != image.get("imageArn")
        or version.get("codeArtifact", {}).get("uri") != artifact_uri
    ):
        raise ValueError(
            "Pinned version does not match the successful active synthesized source artifact"
        )


# Resolve native image settings from synthesis and print only the verified deploy parameter.
def main():
    parser = argparse.ArgumentParser(
        description="Read-only verification of the newly deployed CDK image"
    )
    parser.add_argument("--version", required=True)
    parser.add_argument("--profile", default="AWSAdministratorAccess-172318459814")
    parser.add_argument(
        "--template", type=Path, default=Path("cdk.out/FactoryImage.template.json")
    )
    args = parser.parse_args()
    template = json.loads(args.template.read_text())
    properties = next(
        value["Properties"]
        for value in template["Resources"].values()
        if value["Type"] == "AWS::Lambda::MicrovmImage"
    )
    arn = "arn:aws:lambda:us-east-1:172318459814:microvm-image:" + properties["Name"]
    common = [
        "--image-identifier",
        arn,
        "--profile",
        args.profile,
        "--region",
        "us-east-1",
        "--output",
        "json",
    ]
    image = json.loads(
        subprocess.check_output(
            ["aws", "lambda-microvms", "get-microvm-image", *common], text=True
        )
    )
    version = json.loads(
        subprocess.check_output(
            [
                "aws",
                "lambda-microvms",
                "get-microvm-image-version",
                "--image-version",
                args.version,
                *common,
            ],
            text=True,
        )
    )
    verify(image, version, args.version, properties["CodeArtifact"]["Uri"])
    print("Verified new source and SUCCESSFUL/ACTIVE image version: " + args.version)
    print(
        "Workflow deploy parameter: --parameters FactoryWorkflow:ImageVersion="
        + args.version
    )


if __name__ == "__main__":
    main()
