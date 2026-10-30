# AWS Lambda MicroVM Software Factory PoC

## Objective and scope

Status: proposed, not implemented; based on [software_factory_poc.md](../../../software_factory_poc.md). Approved direction: simplicity first. Prove a real coding task manually in Gate 1, then orchestrate the same image and worker with Step Functions in Gate 2. Remaining resource, sizing, timing, and demonstration choices below require approval. Approved job-level time bounds are a VM maximum lifespan and an overall workflow timeout only; no separate worker or polling deadlines.

One headless Codex CLI invocation owns editing, testing, fixing failures, committing, pushing a task branch, and creating a GitHub PR inside one MicroVM. Step Functions manages lifecycle and receives only terminal success/error; it neither implements another agent loop nor restarts Codex after failures. A failed test stays in Codex's loop, not an immediate terminal error.

No Agents API, custom reasoning loop, remote shell endpoint, AWS command-execution API, orchestration/polling Lambda, local controller/publication step, Git bundle, temporary local repository, or artifact download is needed. No VPC, NAT Gateway, load balancer, queue, database, webhook, load SLO, task recovery, durable queue, or concurrency lock is planned. The operator runs only one VM/execution at a time; Step Functions does not serialize independent executions.

## Architecture and technical decisions

- A one-time build uses private S3 artifacts to produce a reusable image containing Python, Git, `gh`, pinned Linux ARM64 Codex, and the selected repository's toolchain.
- The port-8080 lifecycle/worker server initializes one job, starts Codex asynchronously, streams command/test output to CloudWatch and exposes `/status`; no separate worker deadline is implemented. AWS lifecycle hooks start/stop it; Codex shell tools execute locally inside the VM.
- The worker retrieves one Secrets Manager runtime-credentials secret containing model and GitHub credentials. Supply `CODEX_API_KEY` and `GH_TOKEN` at runtime; configure Git authentication with `gh auth setup-git`. Codex calls OpenAI directly and publishes to GitHub.
- Step Functions Standard uses direct AWS SDK lifecycle/token integrations and `arn:aws:states:::http:invoke` for authenticated HTTPS polling through AWS-managed ingress and internet-egress connectors. Auth tokens are restricted to port 8080.
- HTTP Tasks require one EventBridge connection. Use a non-conflicting API-key header such as `X-Poc-Connection` with a static marker the worker ignores. Actual authentication is dynamic JWE header `X-aws-proxy-auth`; connection headers override HTTP Task headers, so never configure that header in the connection.
- Status is pending while work continues, then exactly one terminal response: `{"ok": true}` only after passing tests and confirmed PR creation, otherwise `{"error": "cause"}` for unrecoverable failure. VM termination can interrupt the worker without a terminal response. HTTP 200 or successful process exit alone is insufficient. No logs, diff, SHA, PR URL, or token-usage artifacts need returning or local storage; logs stay in CloudWatch and published work in GitHub.

## Implementation phases

### Gate 1 — Prove execution before creating orchestration

Do not create Step Functions or its EventBridge connection until the real task succeeds and the manually launched VM is confirmed terminated.

#### 1. Confirm demonstration inputs

Confirm one repository and pinned base SHA; a small, objectively testable source/test task (not deployment configuration); existing setup/test commands; a model available to the API project; permission to push a dedicated branch/create a PR inside the VM; and permission to expose repository content to the model provider. Propose a small repository with existing automated tests. Its GitHub token needs repository access plus contents and pull-request write permissions.

Run baseline tests first. Record the base SHA, baseline outcome, and expected acceptance test to distinguish existing failures from agent failures.

#### 2. Create execution resources and inspect permissions

Proposed Gate 1 resources: one private S3 artifact bucket in `eu-central-1`, one image build role, one runtime execution role, one runtime-credentials secret, one CloudWatch log group for image/worker logs, and one reusable image. Defer orchestration permissions and the Step Functions execution role until Gate 2.

| Principal | Required access |
|---|---|
| Build role | `s3:GetObject` on the image artifact; CloudWatch log creation/writes |
| Runtime execution role | CloudWatch log writes; `secretsmanager:GetSecretValue` on only the runtime-credentials secret |
| Provisioning identity | Resource setup; image creation/inspection/deletion; state-machine and EventBridge connection deployment; `iam:PassRole` for their specific roles |
| Step Functions execution role (Gate 2) | `lambda:RunMicrovm`, `lambda:GetMicrovm`, `lambda:CreateMicrovmAuthToken`, `lambda:TerminateMicrovm`; `iam:PassRole` for the MicroVM execution role; `states:InvokeHTTPEndpoint` for workflow GET status requests; `events:RetrieveConnectionCredentials` on the connection; `secretsmanager:GetSecretValue` and `secretsmanager:DescribeSecret` on its managed connection secret |
| Operator using supplied AWS profile | Gate 1: manual launch, inspection, token creation, termination, log reads, and `iam:PassRole` for the runtime execution role. Gate 2: `states:StartExecution`, `states:DescribeExecution`, execution-history/log reads; retain manual recovery access. |

Build/runtime roles require the documented Lambda trust policy allowing `sts:AssumeRole` and `sts:TagSession`; the Gate 2 role trusts `states.amazonaws.com`. Add KMS decryption only for a customer-managed key. Never copy the administrator profile/credentials into the image. Verify policies, trust relationships, and scoped artifact, secret, and log access.

#### 3. Prepare a snapshot-safe image; build after Step 4

Use container base `public.ecr.aws/lambda/microvms:al2023-minimal` for its documented snapshot-compatible OpenSSL libraries and managed base image `arn:aws:lambda:eu-central-1:aws:microvm-image:al2023-1`. The installed CLI accepts only `ARM_64`; all binaries/tooling must be compatible.

Proposed sizing: **2 GB / 1 vCPU baseline**, bursting automatically to **8 GB / 4 vCPU**, with **8 GB maximum disk space**. Disk does not grow with compute bursting. Package pinned Codex, `gh`, Git, required toolchain, Python server, and an empty writable workspace; exclude credentials, model keys, task identifiers, and live external connections.

| Hook | Proposed behavior |
|---|---|
| `/ready` | Confirm server readiness without starting the agent |
| `/validate` | Check agent executable, workspace writes, and basic process execution |
| `/run` | Validate job configuration, initialize fresh state, spawn worker, return promptly |
| `/terminate` | Stop worker/process group and flush available logs |

Clone repositories and call the model asynchronously in the worker, not synchronously inside `/run`; the installed CLI limits that hook to 60 seconds. After Step 4, build the image, wait for a successful active image version, and pin it for Step 5. Verify successful build logs and no credential-dependent snapshot validation.

#### 4. Implement one shared single-task worker

Both manual launch and Step Functions use this server's `/run`; do not create a separate feasibility runner.

1. Retrieve runtime credentials and configure `gh auth setup-git`.
2. Clone the approved repository, check out the pinned base SHA, create a unique task branch, and install declared dependencies.
3. Start Codex once with the task and instructions to edit source, run approved tests, inspect/fix failures, and repeat until tests pass, an unrecoverable failure occurs, or the VM is terminated. Stream JSONL events and stderr to CloudWatch.
4. After the final test command passes with a nonempty change, the same agent commits, runs `git push`, and runs `gh pr create` against the selected base branch. It must not merge.
5. On exit, report success only for passing tests and confirmed PR creation; report an error for incomplete work or setup/process failure. VM termination may prevent any terminal response. Verify through the real Step 5 task, not merely Codex's exit code.

Supply structured job data in `runHookPayload`, never an interpolated shell command. Start with Codex's documented `workspace-write` sandbox and enable outbound networking needed for GitHub. Verify unattended/tool compatibility; do not silently switch to unrestricted execution. On publication timeouts, the agent checks for its existing branch/PR before retrying to avoid duplicates. Neither worker nor workflow restarts Codex or reruns the whole task.

#### 5. Manually prove the real task and terminate

Launch one VM with AWS CLI using the pinned image, runtime role, managed connectors, and structured `runHookPayload`. Apply the proposed 30-minute lifespan and idle policy specified below.

- Retain its ID for recovery if the terminal closes, wait for `RUNNING`, create one port-8080 token, and request `/status` over HTTPS directly, without Step Functions/EventBridge.
- Confirm the same worker runs unattended Codex in the intended sandbox, runtime credentials/repository setup/model access work, and meaningful source changes result. Record the passing test command and exit code; verify agent-side commit, push, PR creation, CloudWatch output, and terminal response. Observed test-failure repair is not required.
- Manually terminate on success or failure; use `get-microvm` to confirm `TERMINATED`. Record VM ID and outcomes locally, keeping logs in CloudWatch and changes in GitHub.

Stop if execution, publication, or termination fails. A generic child-process smoke test does not satisfy Gate 1.

### Gate 2 — Add orchestration only after Gate 1 passes

#### 6. Create resources and implement lifecycle workflow

Create one Step Functions Standard state machine/execution role and one EventBridge connection, which automatically stores credentials in a managed Secrets Manager secret. Reuse the proven image/worker. Validate the definition before deployment and inspect Gate 2 permissions/trust. The connection must not define/override `X-aws-proxy-auth` or `X-aws-proxy-port`.

```text
RunMicrovm → Wait/GetMicrovm until RUNNING → CreateAuthToken once
          → HTTP PollStatus → Choice → pending: Wait → HTTP PollStatus
          → terminal result/error: TerminateMicrovm
          → Wait/GetMicrovm until TERMINATED → Succeed / Fail
```

1. Launch using `arn:aws:states:::aws-sdk:lambdamicrovms:runMicrovm` and the pinned image version. Set `clientToken` from the execution name; retain returned VM ID/endpoint in execution state.
2. Use `Wait`/`getMicrovm` until `RUNNING`; wait while provisioning and report unexpected termination as an error. After readiness, reserve lifecycle queries for failure diagnosis and termination confirmation, not every status poll.
3. Call `arn:aws:states:::aws-sdk:lambdamicrovms:createMicrovmAuthToken` once for port 8080. Propose 30-minute expiration covering polling within the proposed 28-minute overall workflow timeout; reuse across polls. Verify supported expiration/reuse live; no refresh loop.
4. Invoke `arn:aws:states:::http:invoke` with `Method: GET`, dynamic `https://<endpoint>/status`, `InvocationConfig.ConnectionArn`, dynamic `X-aws-proxy-auth`, and `X-aws-proxy-port: 8080`. Propose a bounded 10-second request timeout; HTTP Tasks allow at most 60 seconds.
5. Inspect JSON response body, not just HTTP 200. If pending, propose a 30-second `Wait`, then repeat only HTTP request/response checking. Preserve only terminal `{"ok": true}` or `{"error": "cause"}` for final outcome. Missing/invalid terminal responses must never become success.
6. JWE passes through execution state: omit it from final output/application logs and restrict execution-history access. Route catchable post-launch errors to common cleanup, retaining VM ID when catching errors.
7. Call `terminateMicrovm`; use `getMicrovm`/`Wait` until `TERMINATED`. Finish in `Succeed` for `ok`, otherwise `Fail` with cause. Report cleanup failures instead of claiming termination. A failed launch without a known ID ends in `Fail`; ambiguous launches remain bounded by VM lifespan. Never restart the task.

Proposed job-level bounds: **28-minute overall workflow timeout** and **30-minute VM maximum lifespan** only; no separate worker or polling deadline. Set idle suspension beyond lifespan. Workflow timeout bounds orchestration; VM lifespan independently bounds resource use. Their clocks start at workflow execution and VM launch, so the values do not provide a fixed cleanup margin. Overall timeout ends execution as `TIMED_OUT`; it and manual `StopExecution` do not run `Catch` cleanup or stop the worker. Work may continue until VM termination, which can interrupt publication without a terminal response. Use lifespan fallback or manual termination. Retry failed `StartExecution` with the same execution name/input, not a new job. Closing the initiating terminal does not stop AWS-hosted execution.

The workflow does not clone repositories, import commits, push/create PRs, download artifacts, or run an agent retry loop. Verify success, one controlled failure with cleanup, and hard lifespan separately; use small negative-path tests where practical rather than exhaustive live fault injection.

### Job configuration and illustrative commands (not executed)

`JOB_JSON` contains repository, pinned base SHA, and task. State-machine configuration supplies image version, execution role, connectors, and credentials-secret ARN. Serialize per-VM configuration into `runHookPayload` within **16 KB**; never put secret values in execution input. Use PascalCase ASL parameters (`ImageIdentifier`, `RunHookPayload`, `MaximumDurationInSeconds`, etc.).

Call `RunMicrovm` with idle policy `{"maxIdleDurationSeconds":3600,"suspendedDurationSeconds":0,"autoResumeEnabled":false}` and `maximumDurationInSeconds: 1800`.

```bash
PROFILE=AWSAdministratorAccess-172318459814
REGION=eu-central-1

aws stepfunctions start-execution \
  --profile "$PROFILE" --region "$REGION" \
  --state-machine-arn "$STATE_MACHINE_ARN" \
  --name "$RUN_ID" --input "$JOB_JSON"

aws stepfunctions describe-execution \
  --profile "$PROFILE" --region "$REGION" \
  --execution-arn "$EXECUTION_ARN"

# Manual recovery if needed:
aws lambda-microvms terminate-microvm \
  --profile "$PROFILE" --region "$REGION" \
  --microvm-identifier "$MICROVM_ID"
```

## Risks and constraints

- Build/runtime compatibility is unproven: smoke-test ARM64 binaries, sandbox, and repository toolchain. Start small; increase baseline only if measurements justify it. Measure launch latency; cloning, dependency setup, model calls, and tests will likely dominate duration.
- VM code can misuse model/GitHub credentials. Use a controlled repository and repository-restricted token. This accepted PoC trade-off is not safe for arbitrary hostile repositories.
- Termination destroys workspace state. Publish before reporting success; failed runs retain only available CloudWatch logs, with no local workspace recovery.
- Tests do not prove correctness: record actual exit codes, inspect diffs for weakened tests, and require human PR review before merging.
- Resource/time limits may interrupt work. Aborted/timed-out workflows rely on hard lifespan or manual termination, not automatic Step Functions cleanup.
- CLI/documentation differ: installed CLI uses `--resources` with `minimumMemoryInMiB`, not documentation's `memory`, and accepts only `ARM_64`. Output-skeleton generation failed on AWS-generated sample fields; this is not evidence of live API failure.

## Acceptance criteria and verification status

Gate 1 establishes execution feasibility; final success requires Gate 2. One submitted task must:

- Manually start through `StartExecution`, continue without a local controller, and launch exactly one VM from a pinned image.
- Run agent edits, shell commands, and tests inside that VM; record a passing command/exit code. Preserve Codex's edit–test–fix loop, without requiring observed failure repair.
- Create a commit descended from submitted base SHA and have the agent publish one task branch/PR using `git push` and `gh pr create` inside the VM, without merging.
- Keep logs in CloudWatch and changes in GitHub; return only `{"ok": true}` as worker terminal success, without downloading artifacts.
- Reuse the auth token across status polls, terminate the VM, confirm `TERMINATED`, and finish in `Succeed`.

Additional verification:

- **Controlled failure:** propose a nonexistent base commit so checkout fails before Codex starts. Confirm `{"error": "cause"}`, no PR, termination confirmed as `TERMINATED`, and `Fail` with retained cause.
- **Separate hard-lifespan check:** launch with short `maximumDurationInSeconds`, idle suspension beyond lifespan, and a non-publishing setup-failure job. Without explicit termination, confirm automatic `TERMINATED`; no agent repair/model call required.
- **Small negative-path tests where practical:** stub/simulate worker setup/process failures; missing/invalid terminal responses; workflow HTTP errors and cleanup routing. Verify configured overall workflow timeout; where practical, simulate a persistently pending worker and confirm `TIMED_OUT` without cleanup or a worker terminal response.
- **Deferred live demonstrations:** invalid credentials, unresolved test failures, missing/invalid endpoint authentication, connection failures, stopped executions, and terminal closure. This does not defer authentication/failure handling: recoverable tests stay in Codex's loop, catchable workflow/HTTP errors terminate and fail, and aborts/overall timeouts rely on lifespan.

Read-only checks on **2026-10-30**, profile `AWSAdministratorAccess-172318459814`, covered AWS identity, image discovery, quotas, resource listings, pricing, CLI help/schema, official documentation, and `validate-state-machine-definition` for all four lifecycle/auth integration ARNs plus token generation/direct HTTP polling. Definition validation passed with an EventBridge connection and failed without one. No state machine/connection was created or live HTTP request executed; image creation, Codex execution, end-to-end orchestration, and OpenAI/GitHub access remain untested. No application code or AWS resources changed during planning. Implementation requires approval of remaining proposals and demonstration inputs.

References: [networking](../../aws-lambda-microvms/microvms-networking.md), [security](../../aws-lambda-microvms/microvms-security.md), [images/sizing](../../aws-lambda-microvms/microvms-images.md), [snapshots](../../aws-lambda-microvms/microvms-images-snapshots.md), [Codex non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode), [Step Functions SDK integrations](https://docs.aws.amazon.com/step-functions/latest/dg/supported-services-awssdk.html), [HTTP Tasks/connections](https://docs.aws.amazon.com/step-functions/latest/dg/call-https-apis.html).
