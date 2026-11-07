# Dispatch one claimed Job with a persisted latest-target baseline; failures block later Jobs and never expose payloads or credentials.
import http.client
import json
import os
from contextlib import closing

import boto3
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from src.receiver.handler import _load_config, _resolve_baseline

AWS_CONFIG = Config(
    connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 1}
)


# Claim the oldest returned pending Job, persist its latest configured target SHA, then start one workflow; failures retain the claim.
def handle(event, context):
    table = os.environ["JOB_TABLE_NAME"]
    machine = os.environ["STATE_MACHINE_ARN"]
    session = boto3.Session(region_name=os.environ.get("AWS_REGION", "us-east-1"))
    with closing(session.client("dynamodb", config=AWS_CONFIG)) as client:
        query = {
            "TableName": table,
            "KeyConditionExpression": "PK = :partition",
            "FilterExpression": "#status = :status",
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": {
                ":partition": {"S": "JOBS"},
                ":status": {"S": "IN_PROGRESS"},
            },
            "ConsistentRead": True,
            "ScanIndexForward": True,
        }
        # shortcut: one-page non-atomic selection, add pagination and atomic claims before production use.
        if client.query(**query).get("Items"):
            return
        query["ExpressionAttributeValues"] = {
            ":partition": {"S": "JOBS"},
            ":status": {"S": "PENDING"},
        }
        pending = client.query(**query).get("Items", [])
        if not pending:
            return
        item = pending[0]
        key = {"PK": item["PK"], "SK": item["SK"]}
        client.update_item(
            TableName=table,
            Key=key,
            UpdateExpression="SET #status = :status",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":status": {"S": "IN_PROGRESS"}},
        )
        try:
            config = _load_config(webhook=False)
            job = TypeDeserializer().deserialize(item["job"])
            if config is None or job["repository"] != config["repository"]:
                raise ValueError("Invalid deployment configuration or repository")
            baseline = _resolve_baseline(session, config)
            if baseline is None:
                raise ValueError("Unresolved execution baseline")
            client.update_item(
                TableName=table,
                Key=key,
                UpdateExpression="SET job.baseSha = :sha",
                ExpressionAttributeValues={":sha": {"S": baseline}},
            )
        except (
            BotoCoreError,
            ClientError,
            OSError,
            http.client.HTTPException,
            ValueError,
            TypeError,
            AttributeError,
            KeyError,
        ):
            raise RuntimeError("Queued Job baseline preparation failed") from None
    deserialize = TypeDeserializer().deserialize
    job["baseSha"] = baseline
    try:
        with closing(session.client("stepfunctions", config=AWS_CONFIG)) as client:
            client.start_execution(
                stateMachineArn=machine,
                input=json.dumps(
                    {
                        "jobs": [job],
                        "queue": {
                            name: deserialize(value) for name, value in key.items()
                        },
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
    except (BotoCoreError, ClientError, OSError, ValueError, TypeError):
        raise RuntimeError("Queued Job workflow start unconfirmed") from None
