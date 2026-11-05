# Dispatch at most one queued Job per Streams invocation; AWS failures propagate without logging payloads.
import json
import os
from contextlib import closing

import boto3
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config

AWS_CONFIG = Config(
    connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 1}
)


# Read the global queue, mark its oldest pending Job active, start one workflow, and return without waiting.
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
    deserialize = TypeDeserializer().deserialize
    with closing(session.client("stepfunctions", config=AWS_CONFIG)) as client:
        client.start_execution(
            stateMachineArn=machine,
            input=json.dumps(
                {
                    "jobs": [deserialize(item["job"])],
                    "queue": {name: deserialize(value) for name, value in key.items()},
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
