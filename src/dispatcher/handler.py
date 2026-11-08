# Dispatch one claimed Job with a persisted latest-target baseline; failures block later Jobs and never expose payloads or credentials.
import http.client
import json
import logging
import os
from contextlib import closing

import boto3
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import BotoCoreError, ClientError

from src.utils.job_utils import AWS_CONFIG, load_config, resolve_baseline

logger = logging.getLogger(__name__)


# Claim the oldest returned pending Job, persist its latest configured target SHA, then start one workflow; failures retain the claim.
def handle(event, context):
    table = os.environ["JOB_TABLE_NAME"]
    machine = os.environ["STATE_MACHINE_ARN"]
    session = boto3.Session(region_name=os.environ.get("AWS_REGION"))
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
        stage = "validation"
        try:
            config = load_config()
            job = TypeDeserializer().deserialize(item["job"])
            if config is None or job["repository"] != config["repository"]:
                raise ValueError("Invalid deployment configuration or repository")
            stage = "baseline_resolution"
            baseline = resolve_baseline(session, config)
            if baseline is None:
                raise ValueError("Unresolved execution baseline")
            stage = "baseline_persistence"
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
        ) as error:
            logger.error(
                "Queued Job baseline preparation failed: stage=%s error_type=%s",
                stage,
                type(error).__name__,
            )
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
    except (BotoCoreError, ClientError, OSError, ValueError, TypeError) as error:
        logger.error(
            "Queued Job workflow start unconfirmed: stage=workflow_submission error_type=%s",
            type(error).__name__,
        )
        raise RuntimeError("Queued Job workflow start unconfirmed") from None
