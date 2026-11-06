# Test-only Lambda wrapper: observe stream metadata, then run the unchanged production Dispatcher.
import json

import handler


# After dispatch returns, record only event kind/key/status, never Job payloads or credentials.
def handle(event, context):
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
