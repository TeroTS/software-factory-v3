# Share bounded operator AWS/CDK calls and source checks; never expose credentials or execution history.
import argparse
import json
import os
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from src.tools.verify_image import verify

ROOT = Path(__file__).resolve().parents[2]
ACCOUNT = "172318459814"
REGION = "us-east-1"
IMAGE_ARN = f"arn:aws:lambda:{REGION}:{ACCOUNT}:microvm-image:software-factory-poc"
MACHINE_ARN = f"arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:software-factory-poc"
LOG_GROUP = "/aws/lambda-microvms/software-factory-poc"
CONFIG = Config(connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 1})


# Carry only factory-authored, credential-free messages across the CLI failure boundary.
class OperatorError(ValueError):
    pass


# Add the existing operator profile option to one independently invoked script.
def parser(description):
    result = argparse.ArgumentParser(description=description)
    result.add_argument("--profile", default="AWSAdministratorAccess-172318459814")
    return result


# Authenticate the fixed account before allowing any deployment or Job action.
def operator(profile):
    session = boto3.Session(profile_name=profile, region_name=REGION)
    if aws(session, "sts", "get_caller_identity")["Account"] != ACCOUNT:
        raise OperatorError("Wrong operator account; refusing live actions")
    return session


# Make one bounded SDK request with refreshable operator credentials; callers select safe output.
def aws(session, service, operation, **parameters):
    with closing(session.client(service, config=CONFIG)) as client:
        return getattr(client, operation)(**parameters)


# Collect all pages of the selected list/history API in memory, never logging raw responses.
def collect(session, service, operation, key, **parameters):
    items = []
    while True:
        response = aws(session, service, operation, **parameters)
        items.extend(response[key])
        if not response.get("nextToken"):
            return items
        parameters["nextToken"] = response["nextToken"]


# Enforce operator single-Job discipline without adding a distributed concurrency lock.
def idle(session):
    try:
        executions = collect(
            session,
            "stepfunctions",
            "list_executions",
            "executions",
            stateMachineArn=MACHINE_ARN,
            statusFilter="RUNNING",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "StateMachineDoesNotExist":
            raise
        executions = []
    if executions:
        raise OperatorError("A workflow execution is running; stop before live changes")
    vms = collect(session, "lambda-microvms", "list_microvms", "items")
    if any(vm.get("state") != "TERMINATED" for vm in vms):
        raise OperatorError("A VM is not terminated; recover it before live changes")


# Run a visible credential-free local check from the factory root; propagate nonzero exit.
def local(*arguments):
    subprocess.run(arguments, cwd=ROOT, check=True)


# Capture command output without shell interpolation or automatic secret-bearing error dumps.
def output(*arguments):
    return subprocess.check_output(
        arguments, cwd=ROOT, text=True, stderr=subprocess.PIPE, timeout=60
    )


# Supply frozen credentials only to local CDK, not Docker, source assets, Job input or logs.
def cdk(session, *arguments):
    credentials = session.get_credentials().get_frozen_credentials()
    env = {
        **os.environ,
        "AWS_REGION": REGION,
        "AWS_DEFAULT_REGION": REGION,
        "AWS_PAGER": "",
    }
    env.pop("AWS_PROFILE", None)
    env.update(
        AWS_ACCESS_KEY_ID=credentials.access_key,
        AWS_SECRET_ACCESS_KEY=credentials.secret_key,
    )
    if credentials.token:
        env["AWS_SESSION_TOKEN"] = credentials.token
    else:
        env.pop("AWS_SESSION_TOKEN", None)
    subprocess.run(
        [str(ROOT / "node_modules" / ".bin" / "cdk"), *arguments],
        cwd=ROOT,
        env=env,
        check=True,
    )


# Require explicit human review after diff/input inspection and before deployment or Job submission.
def confirm(action):
    if input(action + " Type yes to proceed: ").strip() != "yes":
        raise OperatorError("Not approved; stopped before deployment/Job submission")


# Read fresh local synthesis rather than treating an old deployment as current-source evidence.
def template(stack):
    return json.loads((ROOT / "cdk.out" / (stack + ".template.json")).read_text())


# Read native stack outputs without mutating its resources or exposing secret values.
def stack_outputs(session, stack):
    result = aws(session, "cloudformation", "describe_stacks", StackName=stack)[
        "Stacks"
    ][0]
    return {
        item["OutputKey"]: item["OutputValue"] for item in result.get("Outputs", [])
    }


# Reuse the exact-source SUCCESSFUL/ACTIVE guard; return verified runtime configuration to callers.
def verify_source(session, version):
    properties = next(
        r["Properties"]
        for r in template("FactoryImage")["Resources"].values()
        if r["Type"] == "AWS::Lambda::MicrovmImage"
    )
    image = aws(
        session, "lambda-microvms", "get_microvm_image", imageIdentifier=IMAGE_ARN
    )
    details = aws(
        session,
        "lambda-microvms",
        "get_microvm_image_version",
        imageIdentifier=IMAGE_ARN,
        imageVersion=version,
    )
    try:
        verify(image, details, version, properties["CodeArtifact"]["Uri"])
    except ValueError:
        raise OperatorError(
            "Image pin is not SUCCESSFUL/ACTIVE at the exact current source; do not fall back to an older version"
        ) from None
    return details


# Resolve only the intrinsics emitted by this workflow; unknown template changes fail closed.
def resolve_definition(value, imports, connection, version):
    if isinstance(value, str):
        return value
    if "Fn::Join" in value:
        separator, items = value["Fn::Join"]
        return separator.join(
            resolve_definition(item, imports, connection, version) for item in items
        )
    if "Fn::ImportValue" in value:
        return imports[value["Fn::ImportValue"]]
    if "Ref" in value:
        return {
            "ImageVersion": version,
            "AWS::Partition": "aws",
            "AWS::Region": REGION,
            "AWS::AccountId": ACCOUNT,
        }[value["Ref"]]
    if value.get("Fn::GetAtt") == ["Connection", "Arn"]:
        return connection
    raise OperatorError("Unsupported workflow intrinsic; review updated synthesis")


# Resolve synthesized ASL using current image exports and a caller-selected connection ARN.
def workflow_definition(session, version, connection):
    image_stack = aws(
        session, "cloudformation", "describe_stacks", StackName="FactoryImage"
    )["Stacks"][0]
    imports = {
        item["ExportName"]: item["OutputValue"]
        for item in image_stack["Outputs"]
        if "ExportName" in item
    }
    value = template("FactoryWorkflow")["Resources"]["Workflow"]["Properties"][
        "DefinitionString"
    ]
    return json.loads(resolve_definition(value, imports, connection, version))


# Ask AWS to validate the prospective or actual definition; reject validation errors.
def validate_definition(session, definition):
    validation = aws(
        session,
        "stepfunctions",
        "validate_state_machine_definition",
        definition=json.dumps(definition),
        type="STANDARD",
    )
    if validation["result"] != "OK":
        raise OperatorError("AWS workflow validation failed")


# Require exact synthesized workflow, explicit current-source pin and an authorized connection.
def verify_workflow(session, version=None):
    actual = aws(
        session, "stepfunctions", "describe_state_machine", stateMachineArn=MACHINE_ARN
    )
    definition = json.loads(actual["definition"])
    pinned = definition["States"]["LaunchVM"]["Parameters"]["ImageVersion"]
    if (
        actual["status"] != "ACTIVE"
        or actual["type"] != "STANDARD"
        or (version is not None and pinned != version)
    ):
        raise OperatorError("Workflow is not ACTIVE/Standard at the requested pin")
    connection = stack_outputs(session, "FactoryWorkflow")["ConnectionArn"]
    if definition != workflow_definition(session, pinned, connection):
        raise OperatorError("Deployed workflow differs from current synthesis")
    validate_definition(session, definition)
    state = aws(session, "events", "describe_connection", Name=connection.split("/")[1])
    if state["ConnectionState"] != "AUTHORIZED":
        raise OperatorError("Workflow connection is not authorized")
    return verify_source(session, pinned)


# Report only safe validation messages or exception types; interrupted cloud work is never retried.
def run(main):
    try:
        main()
    except OperatorError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (Exception, KeyboardInterrupt) as error:  # noqa: BLE001 - prevent credential/history traceback dumps
        print(
            "Stopped: "
            + type(error).__name__
            + ". Cloud work may still be active; inspect its status. Resume Jobs only with the same name and unchanged input file.",
            file=sys.stderr,
        )
        return 1
    return 0
