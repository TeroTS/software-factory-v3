

# Running and using MicroVMs
<a name="microvms-launching"></a>

This section describes how to start MicroVMs, connect to your running applications, manage the MicroVM lifecycle, and handle scaling.

## Starting a MicroVM
<a name="microvms-launching-run"></a>

Use the `run-microvm` command to launch a new MicroVM from a specified image. Lambda provisions the required resources, creates a dedicated HTTPS endpoint, and starts your application from the image snapshot.

```
aws lambda-microvms run-microvm \
  --image-identifier {{arn:aws:lambda:us-east-1:123456789012:microvm-image:my-microvm-image}} \
  --ingress-network-connectors "arn:aws:lambda:{{us-east-1}}:aws:network-connector:aws-network-connector:ALL_INGRESS" \
  --egress-network-connectors "arn:aws:lambda:{{us-east-1}}:aws:network-connector:aws-network-connector:INTERNET_EGRESS" \
  --idle-policy '{"autoResumeEnabled":true,"maxIdleDurationSeconds":900,"suspendedDurationSeconds":1800}' \
  --maximum-duration-in-seconds 14400
```

A MicroVM is created when you call `run-microvm`. Each MicroVM has its own dedicated endpoint. There is no load-balancing across MicroVMs from a single endpoint – each endpoint is linked to a single MicroVM.

The only required parameter is `--image-identifier` (which must be the ARN of the MicroVM image). All other parameters are optional.

### Key parameters
<a name="microvms-launching-key-params"></a>


| Parameter | Description | 
| --- | --- | 
| --image-identifier | (Required) The ARN of the MicroVM image to run. | 
| --image-version | The version of the MicroVM image to run. Defaults to the latest active version. | 
| --execution-role-arn | The IAM role that provides runtime permissions for the MicroVM to interact with other AWS services. | 
| --idle-policy | Controls automatic suspend and resume behavior. See idle policy configuration in the following section. | 
| --maximum-duration-in-seconds | The maximum total lifespan of the MicroVM, including both running and suspended time. Lambda terminates the MicroVM when this duration is exceeded. Range: 1–28,800 seconds (8 hours). | 
| --run-hook-payload | A string payload (max 16 KB) delivered to the /run lifecycle hook when the MicroVM starts. | 
| --logging | Logging configuration. Customize the CloudWatch log group and stream, or disable logging entirely. | 
| --ingress-network-connectors | The ARN(s) of ingress connectors that enable inbound HTTPS connectivity. | 
| --egress-network-connectors | The ARN(s) of egress connectors for outbound connectivity (internet or VPC). | 

**Note**  
To disable ingress connectivity, use the Lambda-provided `NO_INGRESS` connector. For more details about network connectors, see [Networking](microvms-networking.md).

### Idle policy configuration
<a name="microvms-launching-idle-policy"></a>

When enabled, the idle policy controls automatic suspension and resumption. The presence of traffic through the MicroVM's endpoint signals activity. If no traffic arrives for the configured idle duration, the MicroVM is treated as idle and suspended.


| Field | Description | 
| --- | --- | 
| autoResumeEnabled | When true, the MicroVM automatically resumes when traffic arrives at its endpoint while suspended. | 
| maxIdleDurationSeconds | The number of seconds without traffic after which the MicroVM is suspended. Maximum: 28,800 (8 hours). | 
| suspendedDurationSeconds | The number of seconds a MicroVM remains in the suspended state before Lambda terminates it. | 

**Note**  
For asynchronous applications that do not actively send or receive traffic through the endpoint, disable automatic suspension or configure a suitable idle duration.

### Runtime payloads
<a name="microvms-launching-payload"></a>

With the `runHookPayload` parameter, you can pass per-MicroVM configuration data (max 16 KB string) at run time. Lambda delivers this payload as part of the request body to the `/run` lifecycle hook. Lambda also injects the `microvmId` into the request body.

The `/run` hook receives a JSON body with the following structure:

```
{
  "microvmId": "mvm-01234567-abcd-ef01-2345-6789abcdef01",
  "runHookPayload": "tenant-specific-string"
}
```

Use runtime payloads to provide configuration that varies per MicroVM – for example, tenant IDs, session tokens, signed URLs, or Secrets Manager paths. Unlike environment variables (which are set at image level and shared across all MicroVMs from that image), the run hook payload is unique to each MicroVM.

```
aws lambda-microvms run-microvm \
  --image-identifier {{arn:aws:lambda:us-east-1:123456789012:microvm-image:my-microvm-image}} \
  --run-hook-payload 'tenant-specific-string'
```

When you no longer need a MicroVM, terminate it to stop all charges. For instructions, see [Terminating a MicroVM](#microvms-launching-terminate).

## Connecting to a MicroVM
<a name="microvms-launching-connecting"></a>

Every MicroVM gets a unique public HTTPS endpoint URL, assigned when you call `run-microvm`. You connect to your application running inside the MicroVM through this URL.

### Authentication
<a name="microvms-launching-create-token"></a>

All requests to a MicroVM endpoint require a JWE authentication token. There is no unauthenticated access option. Generate a token with `create-microvm-auth-token`:

```
aws lambda-microvms create-microvm-auth-token \
  --microvm-identifier {{microvm-id}} \
  --expiration-in-minutes 30 \
  --allowed-ports '[{"allPorts":{}}]'
```

Tokens are scoped to specific ports and have a configurable expiration. You can restrict access to a single port, a port range, or all ports:

```
{ "port": {{number}} }
{ "range": { "startPort": {{N}}, "endPort": {{N}} } }
{ "allPorts": {} }
```

### Port routing
<a name="microvms-launching-port-routing"></a>

By default, Lambda routes inbound traffic to port 8080 within your MicroVM. To route to a different port, include the `X-aws-proxy-port` header in your request. The target port must be within the `allowedPorts` defined in the authentication token.

### Protocols
<a name="microvms-launching-websocket"></a>

Lambda MicroVMs supports HTTP/2, WebSockets, gRPC, and SSE over the endpoint URL.

For WebSocket connections, pass the authentication token and target port through subprotocols:

```
// JavaScript WebSocket example
const protocols = [
  "lambda-microvms",                              // Required base protocol
  "lambda-microvms.authentication.<{{auth-token}}>",  // Auth token
  "lambda-microvms.port.9000"                     // Target port
];
const ws = new WebSocket('wss://<{{microvm-endpoint}}>/path', protocols);
```

Lambda removes MicroVM-specific subprotocols from the request before forwarding it to your application.

### SDK examples
<a name="microvms-launching-sdk"></a>

The following examples show how to run a MicroVM and connect to it using the AWS SDKs.

------
#### [ Python ]

**Example – Running a MicroVM and connecting with boto3**  

```
import boto3, requests
client = boto3.client("lambda-microvms")
run_resp = client.run_microvm(
    imageIdentifier="arn:aws:lambda:us-east-1:123456789012:microvm-image:my-microvm-image",
    idlePolicy={"autoResumeEnabled": True, "maxIdleDurationSeconds": 900, "suspendedDurationSeconds": 300}
)
microvm_id = run_resp["microvmId"]
endpoint = run_resp["endpoint"]
print(f"MicroVM {microvm_id} running at {endpoint}")
token_resp = client.create_microvm_auth_token(
    microvmIdentifier=microvm_id, expirationInMinutes=30, allowedPorts=[{"allPorts": {}}]
)
token = token_resp["authToken"]["X-aws-proxy-auth"]
resp = requests.get(f"https://{endpoint}/health", headers={"X-aws-proxy-auth": token})
print(resp.status_code, resp.json())
```

------
#### [ Node.js ]

**Example – Running a MicroVM and connecting with the AWS SDK for JavaScript**  

```
import { LambdaMicrovmsClient, RunMicrovmCommand, CreateMicrovmAuthTokenCommand } from "@aws-sdk/client-lambda-microvms";
const client = new LambdaMicrovmsClient({});
const { microvmId, endpoint } = await client.send(new RunMicrovmCommand({
  imageIdentifier: "arn:aws:lambda:us-east-1:123456789012:microvm-image:my-microvm-image",
  idlePolicy: { autoResumeEnabled: true, maxIdleDurationSeconds: 900, suspendedDurationSeconds: 300 }
}));
const { authToken } = await client.send(new CreateMicrovmAuthTokenCommand({
  microvmIdentifier: microvmId, expirationInMinutes: 30, allowedPorts: [{ allPorts: {} }]
}));
const resp = await fetch(`https://${endpoint}/health`, {
  headers: { "X-aws-proxy-auth": authToken["X-aws-proxy-auth"] }
});
console.log(await resp.json());
```

------

### Sending requests
<a name="microvms-launching-sending-requests"></a>

------
#### [ Bash ]

**Example – Sending a request with cURL**  

```
curl 'https://<{{microvm-endpoint}}>' \
  -H 'X-aws-proxy-auth: <{{TOKEN}}>' \
  -H 'X-aws-proxy-port: 8080'
```

------
#### [ Python ]

**Example – Sending a request with the requests library**  

```
import requests
response = requests.get('https://<{{microvm-endpoint}}>', headers={'X-aws-proxy-auth': '<{{TOKEN}}>'})
print(response.text)
```

------
#### [ Node.js ]

**Example – Sending a request with fetch**  

```
const response = await fetch('https://<{{microvm-endpoint}}>', {
  headers: { 'X-aws-proxy-auth': '<{{TOKEN}}>', 'X-aws-proxy-port': '8080' }
});
console.log(await response.text());
```

------

## Lifecycle hooks
<a name="microvms-launching-lifecycle-hooks"></a>

Lifecycle hooks let you run custom logic at key points in the MicroVM lifecycle – when it starts, suspends, resumes, or terminates. Use hooks to initialize per-tenant state, flush data before suspend, refresh credentials on resume, or clean up resources before termination.

Each hook is an HTTP endpoint your application exposes. Lambda sends a POST request to the hook at the appropriate lifecycle event. Hooks listen on the path `/aws/lambda-microvms/runtime/v1/<hook-name>` on the port you configure.

Your MicroVM begins receiving external traffic after the `/run` hook returns HTTP 200. Until then, the endpoint does not forward requests to your application.


| Hook | When invoked | Purpose | 
| --- | --- | --- | 
| /aws/lambda-microvms/runtime/v1/run | After MicroVM starts from snapshot | Initialize per-tenant state, reset unique values, perform health checks. Traffic begins after this hook returns. | 
| /aws/lambda-microvms/runtime/v1/resume | After MicroVM resumes from suspended state | Re-establish network connections, refresh credentials, validate state. The MicroVM remains in SUSPENDED state while this hook executes; it transitions to RUNNING after the hook returns. | 
| /aws/lambda-microvms/runtime/v1/suspend | Before MicroVM suspends | Flush pending writes, close connections, release resources. | 
| /aws/lambda-microvms/runtime/v1/terminate | Before MicroVM terminates | Flush data, notify external systems, clean up. This hook runs only on final termination, not when suspending. Use the /suspend hook for pre-suspend cleanup. | 

For hooks that run during image creation (`/ready` and `/validate`), see [MicroVM image build hooks](microvms-images.md#microvms-images-build-hooks).

**OpenAPI specification:**

```
{
  "openapi": "3.0.2",
  "info": {
    "title": "Lambda MicroVMs Application Hook Interface",
    "version": "2025-12-03"
  },
  "paths": {
    "/ready": {
      "post": {
        "description": "Called by Lambda during MicroVM image creation to determine if the application has initialized.",
        "operationId": "Ready",
        "responses": {
          "200": { "description": "Successful invocation." },
          "503": { "description": "Application is not yet ready. Lambda retries until timeout." }
        }
      }
    },
    "/resume": {
      "post": {
        "description": "Called by Lambda when resuming a MicroVM that is in the SUSPENDED state.",
        "operationId": "Resume",
        "responses": {
          "200": { "description": "Successful invocation." }
        }
      }
    },
    "/run": {
      "post": {
        "description": "Called by Lambda when a new MicroVM is run from a MicroVM image.",
        "operationId": "Run",
        "requestBody": {
          "content": {
            "application/json": {
              "schema": { "$ref": "#/components/schemas/RunRequestContent" }
            }
          }
        },
        "responses": {
          "200": { "description": "Successful invocation." }
        }
      }
    },
    "/suspend": {
      "post": {
        "description": "Called by Lambda when suspending a MicroVM.",
        "operationId": "Suspend",
        "responses": {
          "200": { "description": "Successful invocation." }
        }
      }
    },
    "/terminate": {
      "post": {
        "description": "Called by Lambda when terminating a MicroVM, before resources are released.",
        "operationId": "Terminate",
        "responses": {
          "200": { "description": "Successful invocation." }
        }
      }
    },
    "/validate": {
      "post": {
        "description": "Called by Lambda when running a MicroVM to validate the image build. Use this hook to perform tests that validate your application behaves correctly when running. Lambda also samples the portions of the image that are used when handling this request, allowing Lambda to prefetch those portions of the image to reduce latency at run time.",
        "operationId": "Validate",
        "responses": {
          "200": { "description": "Successful invocation." },
          "503": { "description": "Validation in progress. Lambda retries until timeout." }
        }
      }
    }
  },
  "components": {
    "schemas": {
      "RunRequestContent": {
        "type": "object",
        "properties": {
          "microvmId": {
            "type": "string",
            "description": "The MicroVM identifier."
          },
          "runHookPayload": {
            "type": "string",
            "description": "Run hook payload provided to RunMicrovm."
          }
        }
      }
    }
  },
  "servers": [
    { "url": "/aws/lambda-microvms/runtime/v1" }
  ]
}
```

## Suspending and resuming MicroVMs
<a name="microvms-launching-suspend-resume"></a>

Suspend MicroVMs to reduce costs while preserving application state. While running, you pay compute charges. While suspended, you pay only snapshot storage charges.

### How to suspend
<a name="microvms-launching-how-to-suspend"></a>

There are two ways to suspend a MicroVM:

1. **Idle policy (automatic)** – Configure `maxIdleDurationSeconds` in the idle policy. If no traffic arrives at the MicroVM endpoint for that duration, Lambda suspends the MicroVM automatically.

1. **API call (explicit)** – Call `suspend-microvm` to suspend immediately:

```
aws lambda-microvms suspend-microvm --microvm-identifier {{microvm-id}}
```

### The /suspend hook
<a name="microvms-launching-suspend-hook"></a>

Before suspending, Lambda calls your `/suspend` hook. Use it to flush pending writes, close network connections, and release resources that must not persist across the suspend boundary.

### Resume behavior
<a name="microvms-launching-resume-behavior"></a>

When a MicroVM resumes (through an API call or auto-resume), Lambda restores memory and disk state from the suspend checkpoint. The MicroVM remains in `SUSPENDED` state while the `/resume` hook executes. After the hook returns HTTP 200, the MicroVM transitions to `RUNNING` and begins receiving traffic.

Use the `/resume` hook to refresh credentials, re-establish network connections, and validate state.

```
aws lambda-microvms resume-microvm --microvm-identifier {{microvm-id}}
```

### Auto-resume
<a name="microvms-launching-auto-resume"></a>

When `autoResumeEnabled=true` and traffic arrives at a suspended MicroVM's endpoint, Lambda resumes the MicroVM automatically. Lambda holds the inbound request while the resume completes (including the `/resume` hook), then delivers it to your application.

The resume adds latency to the first request. The duration depends on the size of the suspended state being restored and the duration of your `/resume` hook.

If resume does not succeed, Lambda returns 502 Bad Gateway to the caller.

**Note**  
Auto-resume adds latency only to the first request after suspend. Subsequent requests while the MicroVM is running are unaffected.

## Scaling and concurrency
<a name="microvms-launching-scaling"></a>

You create new MicroVMs by calling `run-microvm`. Each MicroVM has its own dedicated endpoint. There is no load-balancing across MicroVMs from a single endpoint.

**Account-level capacity** – Your account has a quota for the total memory that can be allocated across all your MicroVMs in the `RUNNING` or `SUSPENDED` state in a Region, and you can vertically scale to four times this quota. To request a quota increase, visit the Service Quotas console and search for Lambda MicroVMs.

**Cost model:**
+ Running MicroVMs incur compute charges.
+ Suspended MicroVMs incur snapshot storage charges but not compute charges.
+ Terminated MicroVMs incur no charges.

**Strategies for managing capacity:**
+ **Suspend idle MicroVMs** – Configure idle policies to automatically suspend MicroVMs that are not receiving traffic.
+ **Terminate MicroVMs that are no longer needed** – Use `suspendedDurationSeconds` to auto-terminate after a maximum suspended duration, or call `terminate-microvm` explicitly.
+ **Right-size idle policies** – Set `maxIdleDurationSeconds` based on your traffic patterns. Shorter idle times free up capacity faster.

## Terminating a MicroVM
<a name="microvms-launching-terminate"></a>

Terminate a MicroVM when it is no longer needed. Termination releases all compute resources and stops all charges.

Before releasing resources, Lambda calls your `/terminate` hook. Use it to flush pending data or notify external systems. The `/terminate` hook runs only on final termination – it does not run when a MicroVM is suspended.

```
aws lambda-microvms terminate-microvm --microvm-identifier {{microvm-id}}
```

## Listing MicroVMs
<a name="microvms-launching-list"></a>

List all MicroVMs in your account, optionally filtered by image:

```
aws lambda-microvms list-microvms

# Filter by image
aws lambda-microvms list-microvms --image-identifier {{my-image}} --image-version {{1.0}}
```

## Error handling
<a name="microvms-launching-errors"></a>

### Run errors
<a name="microvms-launching-errors-run"></a>

The following table lists common errors returned by the `run-microvm` API:


| Error | Cause | Solution | 
| --- | --- | --- | 
| ServiceQuotaExceededException | The account has reached its memory quota for concurrent MicroVMs. | Terminate idle MicroVMs or request a quota increase. | 
| ResourceNotFoundException | The specified image does not exist or is not in CREATED state. | Verify the image identifier and confirm the build completed. | 
| ValidationException | One or more request parameters are invalid. | Check idle policy values, image identifier format, and connector ARNs. | 
| ThrottlingException | The API rate limit for this operation has been exceeded. | Implement exponential backoff with jitter. | 

### Retry strategy
<a name="microvms-launching-errors-retry"></a>

For transient errors (`ThrottlingException`, `InternalServerException`), use exponential backoff:

```
import time, random
def run_with_retry(client, params, max_retries=5):
    for attempt in range(max_retries):
        try:
            return client.run_microvm(**params)
        except client.exceptions.ThrottlingException:
            delay = (2 ** attempt) + random.uniform(0, 1)
            time.sleep(delay)
    raise Exception("Max retries exceeded")
```

For information about supported service integrations with Lambda Managed Instances, see [Integrations](microvms-integrations.md).