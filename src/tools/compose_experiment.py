# Build and run an isolated, bounded Compose feasibility experiment; never update the deployed factory image.
import http.client
import json
import re
import shutil
import time
import uuid
from pathlib import Path

from src.infra.image_stack import stage_source
from src.infra.workflow import CONNECTOR
from src.tools import live

NAME_PATTERN = r"software-factory-compose-[0-9a-f]{12}"


# Refuse production names and arbitrary identifiers before any cloud action or artifact write.
def check_name(name):
    if not re.fullmatch(NAME_PATTERN, name):
        raise ValueError(
            "Expected software-factory-compose- followed by 12 lowercase hex digits"
        )


# Stage the named image's worker/experimental source; return source, ZIP and evidence paths without changing production assets.
def prepare(name):
    check_name(name)
    outdir = live.ROOT / "cdk.out" / name
    if outdir.exists():
        raise live.OperatorError(
            "Experiment directory exists; inspect it instead of replacing evidence"
        )
    outdir.mkdir(parents=True)
    source = Path(stage_source(outdir))
    for filename in ("compose_experiment.py", "compose_probe.py"):
        shutil.copyfile(live.ROOT / "image" / filename, source / "image" / filename)
    shutil.copytree(
        live.ROOT / "image/compose",
        source / "image/compose",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    dockerfile = source / "Dockerfile"
    dockerfile.write_text(
        dockerfile.read_text().removesuffix(
            'CMD ["python3.13", "-m", "src.worker.server"]\n'
        )
        + (live.ROOT / "image/compose/Dockerfile.append").read_text()
    )
    archive = shutil.make_archive(str(outdir / "source"), "zip", source)
    return source, Path(archive), outdir


# Launch one explicitly pinned 15-minute VM; retain IDs before observation and confirm termination even on failure.
def run_trial(session, name, version, role, number, evidence):
    check_name(name)
    evidence = Path(evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    image = f"arn:aws:lambda:{live.REGION}:{live.ACCOUNT}:microvm-image:{name}"
    result = {
        "status": "error",
        "stage": "launch",
        "clientToken": f"{name}-trial-{number}",
        "imageArn": image,
        "imageVersion": version,
    }
    path = evidence / f"trial-{number}.json"
    path.write_text(json.dumps(result))
    vm = None
    try:
        vm = live.aws(
            session,
            "lambda-microvms",
            "run_microvm",
            imageIdentifier=image,
            imageVersion=version,
            executionRoleArn=role,
            maximumDurationInSeconds=900,
            idlePolicy={
                "autoResumeEnabled": False,
                "maxIdleDurationSeconds": 900,
                "suspendedDurationSeconds": 60,
            },
            ingressNetworkConnectors=[CONNECTOR + "ALL_INGRESS"],
            egressNetworkConnectors=[CONNECTOR + "INTERNET_EGRESS"],
            clientToken=result["clientToken"],
        )
        result["microvmId"] = vm["microvmId"]
        path.write_text(json.dumps(result))
        result["stage"] = "observer"
        token = live.aws(
            session,
            "lambda-microvms",
            "create_microvm_auth_token",
            microvmIdentifier=vm["microvmId"],
            expirationInMinutes=15,
            allowedPorts=[{"port": 8080}],
        )["authToken"]["X-aws-proxy-auth"]
        endpoint = vm["endpoint"].removeprefix("https://")
        if not re.fullmatch(
            r"[a-zA-Z0-9-]+\.lambda-microvm\.us-east-1\.on\.aws", endpoint
        ):
            raise live.OperatorError(
                "Unexpected VM endpoint; refusing to send its token"
            )
        deadline = time.monotonic() + 820
        while time.monotonic() < deadline:
            state = live.aws(
                session,
                "lambda-microvms",
                "get_microvm",
                microvmIdentifier=vm["microvmId"],
            )["state"]
            if state == "TERMINATED":
                raise live.OperatorError("VM terminated before evidence collection")
            if state == "RUNNING":
                connection = http.client.HTTPSConnection(endpoint, timeout=15)
                try:
                    connection.request(
                        "GET", "/status", headers={"X-aws-proxy-auth": token}
                    )
                    response = connection.getresponse()
                    if response.status != 200:
                        raise live.OperatorError(
                            "Unexpected evidence HTTP status; redirects are not followed"
                        )
                    data = response.read(262145)
                    if len(data) > 262144:
                        raise live.OperatorError("Experiment evidence exceeded 256 KiB")
                    report = json.loads(data)
                finally:
                    connection.close()
                if report.get("status") in ("ok", "error"):
                    result["report"] = report
                    result["status"] = report["status"]
                    result["stage"] = report.get("stage", "unknown")
                    break
            time.sleep(5)
        else:
            raise live.OperatorError("Experiment observation deadline exceeded")
    except Exception as error:  # noqa: BLE001 - preserve only credential-free observer error types
        result["observerError"] = type(error).__name__
    finally:
        if vm is not None:
            live.aws(
                session,
                "lambda-microvms",
                "terminate_microvm",
                microvmIdentifier=vm["microvmId"],
            )
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                result["vmState"] = live.aws(
                    session,
                    "lambda-microvms",
                    "get_microvm",
                    microvmIdentifier=vm["microvmId"],
                )["state"]
                if result["vmState"] == "TERMINATED":
                    break
                time.sleep(2)
            path.write_text(json.dumps(result, indent=2))
            if result.get("vmState") != "TERMINATED":
                raise live.OperatorError(
                    "VM termination unconfirmed; stop before another experiment"
                )
        else:
            path.write_text(json.dumps(result, indent=2))
    return result


# Return isolated source/role/log identifiers for name/archive; refuse source overwrite and propagate cloud failures.
def provision(session, name, archive):
    check_name(name)
    bucket = live.stack_outputs(session, "FactoryArtifacts")["ArtifactBucket"]
    key = f"compose-experiments/{name}/source.zip"
    group = "/aws/lambda-microvms/" + name
    live.aws(
        session,
        "s3",
        "put_object",
        Bucket=bucket,
        Key=key,
        Body=archive.read_bytes(),
        IfNoneMatch="*",
    )
    live.aws(session, "logs", "create_log_group", logGroupName=group)
    live.aws(
        session, "logs", "put_retention_policy", logGroupName=group, retentionInDays=7
    )
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": ["sts:AssumeRole", "sts:TagSession"],
            }
        ],
    }
    roles = {}
    for kind in ("build", "runtime"):
        role_name = name + "-" + kind
        roles[kind] = live.aws(
            session,
            "iam",
            "create_role",
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
        )["Role"]["Arn"]
        statements = [
            {
                "Effect": "Allow",
                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": f"arn:aws:logs:{live.REGION}:{live.ACCOUNT}:log-group:{group}:*",
            }
        ]
        if kind == "build":
            statements.append(
                {
                    "Effect": "Allow",
                    "Action": "s3:GetObject",
                    "Resource": f"arn:aws:s3:::{bucket}/{key}",
                }
            )
        else:
            statements.append(
                {
                    "Effect": "Allow",
                    "Action": [
                        "bedrock:InvokeModel",
                        "bedrock:InvokeModelWithResponseStream",
                    ],
                    "Resource": [
                        f"arn:aws:bedrock:{live.REGION}:{live.ACCOUNT}:inference-profile/us.openai.gpt-6.1-sol",
                        f"arn:aws:bedrock:{live.REGION}:{live.ACCOUNT}:project/default",
                        *[
                            f"arn:aws:bedrock:{region}::foundation-model/openai.gpt-6.1-sol"
                            for region in ("us-east-1", "us-east-2", "us-west-2")
                        ],
                    ],
                }
            )
        live.aws(
            session,
            "iam",
            "put_role_policy",
            RoleName=role_name,
            PolicyName="experiment",
            PolicyDocument=json.dumps(
                {"Version": "2012-10-17", "Statement": statements}
            ),
        )
    time.sleep(10)
    return {"bucket": bucket, "key": key, "logGroup": group, "roles": roles}


# Run local build/sandbox gates, then two fresh AWS VMs; retain resources/evidence for inspection rather than auto-delete images.
def main(argv=None):
    parser = live.parser(
        "Disposable rootful Docker Compose experiment; billable image build and model inference, no GitHub writes"
    )
    parser.add_argument(
        "--name", default="software-factory-compose-" + uuid.uuid4().hex[:12]
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Stage/build and check the offline sandbox; no AWS actions",
    )
    args = parser.parse_args(argv)
    source, archive, evidence = prepare(args.name)
    print("Experiment/evidence: " + str(evidence), flush=True)
    live.local(
        "docker", "build", "--platform", "linux/arm64", "-t", args.name, str(source)
    )
    live.local(
        "docker",
        "run",
        "--rm",
        "--user",
        "factory",
        "--network",
        "none",
        "--security-opt",
        "seccomp=unconfined",
        "--env",
        "AWS_EC2_METADATA_DISABLED=true",
        args.name,
        "python3.13",
        "-c",
        'from src.worker.server import Runtime; Runtime("/workspace").validate()',
    )
    if args.local_only:
        return
    session = live.operator(args.profile)
    live.idle(session)
    live.confirm(
        "Create isolated roles/logs/image with ALL capabilities and run two 15-minute VMs with Codex inference (no deployed factory changes or GitHub writes)?"
    )
    resources = provision(session, args.name, archive)
    (evidence / "resources.json").write_text(json.dumps(resources, indent=2))
    image = live.aws(
        session,
        "lambda-microvms",
        "create_microvm_image",
        name=args.name,
        baseImageArn=f"arn:aws:lambda:{live.REGION}:aws:microvm-image:al2023-1",
        baseImageVersion="3",
        buildRoleArn=resources["roles"]["build"],
        codeArtifact={"uri": f"s3://{resources['bucket']}/{resources['key']}"},
        additionalOsCapabilities=["ALL"],
        cpuConfigurations=[{"architecture": "ARM_64"}],
        resources=[{"minimumMemoryInMiB": 2048}],
        egressNetworkConnectors=[CONNECTOR + "INTERNET_EGRESS"],
        logging={"cloudWatch": {"logGroup": resources["logGroup"]}},
        hooks={
            "port": 8080,
            "microvmImageHooks": {
                "ready": "ENABLED",
                "readyTimeoutInSeconds": 60,
                "validate": "ENABLED",
                "validateTimeoutInSeconds": 60,
            },
            "microvmHooks": {
                "run": "ENABLED",
                "runTimeoutInSeconds": 60,
                "terminate": "ENABLED",
                "terminateTimeoutInSeconds": 30,
            },
        },
        clientToken=args.name,
    )
    resources.update(imageArn=image["imageArn"], imageVersion=image["imageVersion"])
    (evidence / "resources.json").write_text(json.dumps(resources, indent=2))
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        details = live.aws(
            session,
            "lambda-microvms",
            "get_microvm_image_version",
            imageIdentifier=image["imageArn"],
            imageVersion=image["imageVersion"],
        )
        (evidence / "image-version.json").write_text(
            json.dumps(details, indent=2, default=str)
        )
        if details["state"] == "FAILED":
            raise live.OperatorError(
                "Experimental image build failed; inspect retained logs/evidence"
            )
        if details["state"] == "SUCCESSFUL" and details["status"] == "ACTIVE":
            break
        time.sleep(10)
    else:
        raise live.OperatorError(
            "Image build deadline exceeded; inspect its status before another build"
        )
    succeeded = True
    for number in (1, 2):
        result = run_trial(
            session,
            args.name,
            image["imageVersion"],
            resources["roles"]["runtime"],
            number,
            evidence,
        )
        print(
            f"Trial {number}: {result['status']} at {result['stage']}; VM {result.get('vmState', 'unknown')}",
            flush=True,
        )
        succeeded = succeeded and result["status"] == "ok"
        if "microvmId" not in result:
            raise live.OperatorError(
                "Launch ID unknown; stop and inspect account VMs before proceeding"
            )
    print(
        "Retained experimental image/roles/source/logs are listed in resources.json; no VM should remain running."
    )
    if not succeeded:
        raise live.OperatorError(
            "Compose feasibility not established; inspect both fresh-VM reports"
        )


if __name__ == "__main__":
    raise SystemExit(live.run(main))
