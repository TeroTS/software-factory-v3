

# Using Lambda MicroVMs as a sandbox for OpenAI Agents API
<a name="microvms-integrations-openai-agents-api"></a>

You can use AWS Lambda MicroVMs as the self-hosted environment for agents built with the OpenAI Agents API, keeping workspace files, tool execution, and network access in infrastructure you control. OpenAI runs the agent harness; the Lambda MicroVM executes your tasks. In this example, it runs `codex exec-server`, which executes the agent's tool calls and holds the session's workspace files. You decide the image contents, the network paths the executor can take, and the AWS resources its execution role is scoped to.

Each MicroVM runs as a Firecracker-isolated environment that boots from a snapshot of your image. A MicroVM's total lifetime is up to 8 hours, including running and suspended time. Terminate it after the final turn, or suspend it between turns. You build one reusable image, then launch a MicroVM from either your application or a webhook launcher. Both approaches use the same image and credentials. For more detail, review the [reference example](https://github.com/openai/openai-cookbook/tree/main/examples/agents_api/sandboxes/aws) for OpenAI Agents API self-hosted sandbox on Lambda MicroVMs.

## How it works
<a name="microvms-integrations-openai-agents-api-how-it-works"></a>

In the webhook-managed path, a launcher Lambda function starts a MicroVM when the session first needs an executor. It reuses the existing MicroVM, resuming it first if suspended. Your application terminates it after the final turn. Figure 1 shows the flow:

1. Your application creates a self-hosted session and sends input to the Agents API.

1. When the session needs an executor, OpenAI sends an `agent.session.action_required` webhook with an `environment_connection` action to an Amazon API Gateway endpoint in your account.

1. A launcher Lambda function verifies the webhook signature, retrieves the current session, and confirms the handler owns it.

1. The launcher calls `RunMicrovm` with the image, execution role, network connectors, and a `runHookPayload` that carries the session's connection values.

1. The MicroVM's `/run` hook uses its IAM execution role to retrieve the executor key from AWS Secrets Manager and start Codex.

1. The executor registers with OpenAI at `https://api.openai.com`. It then opens a secure WebSocket connection to `wss://codex-cloud-environments.chatgpt.com` to receive tool commands and return results.

1. Your application can upload input files and download output files through the MicroVM's authenticated HTTPS endpoint. It follows the session event stream and can suspend the MicroVM between turns.

1. After the final turn, your application terminates the MicroVM.

![Webhook-managed provisioning for an Agents API sandbox on AWS Lambda MicroVMs](https://docs.aws.amazon.com/lambda/latest/dg/images/microvms-openai-agents-webhook-architecture.png)


Figure 1. Webhook-managed provisioning for an Agents API sandbox on AWS Lambda MicroVMs.

Keep the application key and webhook signing secret outside the MicroVM. The launcher passes only the executor-key secret ARN; the VM retrieves the key using its execution role.

## Key properties
<a name="microvms-integrations-openai-agents-api-properties"></a>


| Property | Benefit | 
| --- | --- | 
| Firecracker isolation | Hardware-virtualized boundary for each session's MicroVM | 
| Snapshot-based startup | Each MicroVM launches from a snapshot of your prepared image | 
| IAM through the execution role | The /run hook reads the executor key with the MicroVM's execution role; the key is not stored in the image | 
| Stateful duration | Each MicroVM can run up to 8 hours, including suspended time | 
| Suspend and resume between turns | Memory and disk state are preserved so follow-up turns continue in the same environment | 
| Usage-based compute | Running VMs incur compute charges. Suspension stops compute charges; snapshot storage and reads/writes are billed separately. | 

## Prerequisites
<a name="microvms-integrations-openai-agents-api-prereqs"></a>
+ An AWS account with Lambda MicroVMs access, an AWS CLI version that includes `lambda-microvms`, and permissions to build images and run MicroVMs. To create the Amazon Simple Storage Service (Amazon S3) artifact bucket and AWS Identity and Access Management (IAM) image build role, see [Create your first Lambda MicroVM](microvms-getting-started.md).
+ An OpenAI application key (`OPENAI_API_KEY`) for creating sessions and submitting input.
+ A separate OpenAI executor key (`OPENAI_EXECUTOR_API_KEY`) with the same organization, project, and user or service account as the session. Store it in Secrets Manager, for example as `codex/agents-api/executor`. The `/run` hook passes it to Codex as `CODEX_API_KEY`.
+ A MicroVM execution role that can read only the executor-key secret. If the secret uses a customer managed AWS Key Management Service (AWS AWS KMS) key, add decryption permission. See [Security and permissions](microvms-security.md).

## Preparing the MicroVM image
<a name="microvms-integrations-openai-agents-api-image"></a>

Build an image that includes the Codex CLI, your tool dependencies, a working directory such as `/workspace`, and an HTTP server for the lifecycle hooks.


| Hook | Behavior | 
| --- | --- | 
| /aws/lambda-microvms/runtime/v1/ready | Returns HTTP 200 when the server is ready for Lambda to take the snapshot. The executor stays disconnected. | 
| /aws/lambda-microvms/runtime/v1/run | Reads the session connection values and secret ARN, fetches the executor key, and starts the executor. | 
| /aws/lambda-microvms/runtime/v1/validate | Optional. Checks that Codex runs and the workspace is writable. | 
| /aws/lambda-microvms/runtime/v1/suspend | Required for suspend/resume. Stops the executor and flushes writes before AWS snapshots the VM. | 
| /aws/lambda-microvms/runtime/v1/resume | Required for suspend/resume. Fetches the key again and restarts the executor for the same environment. | 
| /aws/lambda-microvms/runtime/v1/terminate | Optional. Stops the executor and flushes writes before termination. | 

Package the `Dockerfile` and hook server in an Amazon S3 artifact, then build the `codex-executor` image with 8 GB of baseline memory (4 vCPU baseline). Enable the selected hooks in the image configuration and set the hook port to match your server. Keep session IDs, credentials, and live executor connections out of the image snapshot. For build and hook configuration, see [MicroVM images](microvms-images.md).

At launch, your application or launcher serializes the following values as JSON in `runHookPayload`:


| Value | Purpose | 
| --- | --- | 
| session.environment.id | Identifies the environment the executor connects to | 
| session.environment.remote\_url | Provides the executor's OpenAI connection URL | 
| Executor-key secret ARN | Lets the hook retrieve the key using the MicroVM's execution role | 

Return from the `/run` hook once the executor process starts, without waiting for the turn to finish, to avoid a hook timeout.

## Launching MicroVMs
<a name="microvms-integrations-openai-agents-api-launching"></a>

You can launch MicroVMs from your application or from a webhook launcher. In both cases, keep the session event stream open until the turn finishes.

### Application-managed provisioning
<a name="microvms-integrations-openai-agents-api-launching-application"></a>

1. Create a self-hosted session whose working directory matches the image, then open its event stream.

1. Call `RunMicrovm` with the image ARN and version, execution role, network connectors, and `runHookPayload`. Save the returned `microvmId` alongside the session ID.

1. Wait for `agent.session.environment.connected`, then send input and follow the turn's result.

1. Retrieve output files and end the turn as described in [Ending a turn](#microvms-integrations-openai-agents-api-ending-turn).

### Webhook-managed provisioning
<a name="microvms-integrations-openai-agents-api-launching-webhook"></a>

Your application creates the session, opens its stream, and submits input. The launcher handles the rest when the webhook arrives.

1. Deploy a `POST /webhook` route in Amazon API Gateway that invokes the launcher. Grant the launcher permission to read its credentials, inspect, launch, resume, and terminate MicroVMs from the selected image, pass the MicroVM execution role, and use the configured network connectors.

1. Register the endpoint for `agent.session.action_required` and `agent.session.failed`. Store the endpoint's signing secret in Secrets Manager with the launcher's application key.

1. Verify the webhook signature against the raw request body before accessing sessions or launching compute. Then retrieve the current session and confirm the handler owns it, for example by matching a dedicated saved agent.

1. If `environment_connection` is still pending, inspect the recorded MicroVM with `GetMicrovm`. Resume it with `ResumeMicrovm` if suspended. Call `RunMicrovm` only if no VM is recorded, then save its ID. If saving fails, terminate the VM you launched. If the executor connects before the connection timeout, the waiting input proceeds without resubmission.

1. For an `agent.session.failed` event, retrieve the current session and terminate its recorded MicroVM only if its status is still failed. Ignore deleted sessions, resolved actions, and sessions owned by another handler.

The [reference example](https://github.com/openai/openai-cookbook/tree/main/examples/agents_api/sandboxes/aws) saves the MicroVM ID in session metadata. Concurrent webhook deliveries can launch extra VMs before that ID is saved. Before production use, prevent concurrent launches for the same session and deduplicate webhook events.

## Ending a turn
<a name="microvms-integrations-openai-agents-api-ending-turn"></a>

Figure 2 shows the MicroVM lifecycle for a single turn, and the optional suspend and resume path for follow-up turns.

![MicroVM lifecycle for one Agents API turn](https://docs.aws.amazon.com/lambda/latest/dg/images/microvms-openai-agents-turn-lifecycle.png)


Figure 2. MicroVM lifecycle for one Agents API turn. `RunMicrovm` launches the MicroVM, the `/run` hook starts the executor, the environment connects, the turn runs, and the turn ends. In the single-turn path, you retrieve files and call `TerminateMicrovm`. In the multi-turn path, the MicroVM suspends after the turn and resumes on the next connection request.

On the session stream, wait for `agent.session.turn.completed`, `agent.session.turn.failed`, or `agent.session.turn.cancelled` for the main agent, where `event.turn.subagent_id` is `null`. Subagents share the MicroVM, so their terminal events don't trigger cleanup. Retrieve the files you need. After the final turn, call `TerminateMicrovm` and confirm the VM reaches `TERMINATED`. For follow-up turns, keep it running or suspend it. Run cleanup if your application encounters an error.

Turn outcomes arrive as stream events, not webhook subscriptions. The `agent.session.failed` webhook covers session failures but not every failed turn. For this reason, don't terminate on `agent.session.idle` alone, because it can arrive before waiting input starts.

To bound the MicroVM's lifetime if cleanup doesn't run, configure the following settings at launch:


| Setting | Guidance | 
| --- | --- | 
| maximumDurationInSeconds | Set a ceiling for the workload, such as 900 for a 15-minute test. This limit applies even while work is in progress. | 
| maxIdleDurationSeconds | Cover the expected workload. Lambda measures inbound traffic, so the executor's outbound connection doesn't reset this timer. | 
| suspendedDurationSeconds / autoResumeEnabled | Use 0 / false for the single-turn pattern, which doesn't resume suspended MicroVMs. | 

For lifetime and idle controls, see [Running and using MicroVMs](microvms-launching.md).

Delete the session separately. Deleting a session doesn't stop the MicroVM or send a deletion webhook. Keep the image and executor-key secret for reuse. For handling follow-up turns, see [Sandbox lifecycle](https://developers.openai.com/api/docs/guides/agents-api/environments/providers/aws).

## Suspending and resuming between turns
<a name="microvms-integrations-openai-agents-api-suspend-resume"></a>

To preserve memory and disk state between turns, suspend the MicroVM after the main agent's turn completes and all tools and subagents finish. Resume the same MicroVM when the session next needs an executor.
+ Set a nonzero `suspendedDurationSeconds`. Snapshot storage charges apply while the MicroVM is suspended.
+ Enable the `/suspend` hook to flush writes and close connections, and the `/resume` hook to refresh credentials and reconnect the executor.
+ The application-managed path resumes the VM before sending the next input. In the webhook-managed path, submitting input triggers the connection request that resumes the VM. The turn proceeds once the executor reconnects.

**Note**  
`maximumDurationInSeconds` limits total running and suspended time to 8 hours. To resume on demand, call `ResumeMicrovm`. Automatic resume requires inbound traffic to the MicroVM; Agents API input alone doesn't resume it.

## Networking
<a name="microvms-integrations-openai-agents-api-networking"></a>

Allow the executor's required outbound connections to OpenAI and access to Secrets Manager. To reach private resources or apply your own network restrictions, attach a VPC egress connector at launch time. See [Working with egress network connectors](microvms-networking.md#microvms-networking-connectors).

The example supports file uploads and downloads through the MicroVM's HTTPS endpoint, using an authentication token scoped to the server's port.

## Monitoring
<a name="microvms-integrations-openai-agents-api-monitoring"></a>

Run either example, then confirm that `hello.txt` downloads, the MicroVM reaches `TERMINATED`, and the deleted session returns HTTP 404.

**Launcher logs.** Review the launcher Lambda function's logs in CloudWatch Logs:

```
aws logs tail /aws/lambda/codex-agents-api-webhook --since 10m
```

**Running MicroVMs.** Set `MICROVM_IMAGE_ARN` to your image ARN, use your deployment's AWS Region, and list the MicroVMs for the image:

```
aws lambda-microvms list-microvms \
  --image-identifier "$MICROVM_IMAGE_ARN" \
  --query 'items[].{id:microvmId,state:state}' --output table
```

Log session and MicroVM IDs together so you can trace a run end to end. Keep credentials and raw webhook bodies out of logs.

## Troubleshooting
<a name="microvms-integrations-openai-agents-api-troubleshooting"></a>


| Symptom | What to check | 
| --- | --- | 
| Webhook signature is rejected | Use the endpoint's signing secret and verify the unmodified request body. | 
| No MicroVM launches | Check the webhook subscription, the session ownership filter, the pending environment\_connection action, and the launcher's IAM permissions. | 
| Image build fails | Check the Amazon S3 artifact, the image build role, and the /ready hook response. | 
| /run fails or times out | Check secret access and executor startup. Return after starting the process, not after the turn. | 
| Executor can't connect | Check the environment ID, remote URL, key ownership, and outbound network access. | 
| MicroVM stops during a turn | Check the maximum lifetime and idle policy. Outbound executor traffic doesn't count as inbound activity. | 

For connection failures, inspect `agent.session.environment.failed` events and the executor logs. For the shared executor contract, see [Self-hosted sandboxes](https://developers.openai.com/api/docs/guides/agents-api/environments/providers/aws).

## Related resources
<a name="microvms-integrations-openai-agents-api-related"></a>
+ [AWS Lambda MicroVMs](lambda-microvms-guide.md)
+ [Running and using MicroVMs](microvms-launching.md)
+ [Security and permissions](microvms-security.md)
+ [RunMicrovm API reference](https://docs.aws.amazon.com/cli/latest/reference/lambda-microvms/run-microvm.html)
+ [OpenAI Agents API self-hosted sandboxes](https://developers.openai.com/api/docs/guides/agents-api/environments/providers/aws)
+ [OpenAI Agents API on AWS guide](https://developers.openai.com/api/docs/guides/agents-api/environments/providers/aws)