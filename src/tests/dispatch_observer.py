# Test-only Lambda wrapper: observe stream metadata, then run the unchanged production Dispatcher.
import json
import os
from unittest.mock import patch

from src.dispatcher import handler


# After dispatch returns, record only event kind/key/status, never Job payloads or credentials.
def handle(event, context):
    # The isolated billable Streams check substitutes only GitHub lookup; Floci uses the real resolver against its fixed local endpoint.
    baseline = os.environ.get("DISPATCH_TEST_TARGET_SHA")
    if baseline is None:
        result = handler.handle(event, context)
    else:
        with patch.object(handler, "resolve_baseline", return_value=baseline):
            result = handler.handle(event, context)
    for record in event.get("Records", []):
        image = record.get("dynamodb", {}).get("NewImage", {})
        print(
            "HANDOFF_RECORD "
            + json.dumps(
                {
                    "eventName": record.get("eventName"),
                    **{
                        name: image.get(name, {}).get("S")
                        for name in ("PK", "SK", "status")
                    },
                }
            )
        )
    return result
