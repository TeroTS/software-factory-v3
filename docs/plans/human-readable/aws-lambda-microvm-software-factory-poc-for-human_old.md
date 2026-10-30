# AWS Lambda MicroVM Software Factory PoC — Implementation Plan

**Status:** Proposed; not implemented. Condensed from the [source plan](../aws-lambda-microvm-software-factory-poc.md), based on [software_factory_poc.md](../../../software_factory_poc.md). Documentation and CLI checks support feasibility; live image creation, agent execution, OpenAI/GitHub access, and orchestration remain unverified.

## Objective and scope

Demonstrate one coding task completed inside one isolated MicroVM: edit source, run tests, fix failures within one headless Codex invocation, then commit, push a task branch, and create a GitHub PR without merging. Step Functions manages VM lifecycle and receives terminal success/error status.

**Approved:** simplicity first; one Codex invocation owns coding and publication; direct Step Functions HTTP polling after readiness; one auth token per execution; explicit termination; two implementation gates using the same image and worker. Gate 1 proves execution manually; Gate 2 adds orchestration only after Gate 1 passes.

**Still proposed:** demonstration inputs, resource sizing/configuration, timing values, and verification examples below require approval before implementation. They are not deployed configuration.

Run only one VM/execution at a time, enforced by the operator—not a concurrency service. No Agents API, custom reasoning loop, agent restart/retry loop, remote shell endpoint, local controller/publication, artifact download, Git bundle, or temporary local repository. No VPC, NAT Gateway, load balancer, queue, database, webhook, polling Lambda, or orchestration Lambda is required. Load SLOs, task recovery, durable queues, and concurrency locks are outside scope.

## Architecture and contracts

| Component | Responsibility |
|---|---|
| Step Functions Standard | Manual `StartExecution`; launch VM; wait for readiness; create one token; poll status; terminate and confirm termination; finish in `Succeed` or `Fail`. |
| Reusable MicroVM image | Python lifecycle/worker server on port 8080, pinned Linux ARM64 Codex CLI, Git, GitHub CLI (`gh`), and repository toolchain. |
| Worker and Codex | Initialize one job; start Codex asynchronously; enforce its deadline; stream command/test output to CloudWatch; perform coding and GitHub publication inside the VM. Shell tools execute locally there. |
| Secrets Manager | One runtime-credentials secret for model API key and GitHub token, retrieved only at runtime. Supply `CODEX_API_KEY` and `GH_TOKEN`; configure Git with `gh auth setup-git`. |
| EventBridge connection | Required configuration for Step Functions HTTP Tasks; actual VM authentication uses a dynamic JWE token. |
| External services | Direct OpenAI model API; GitHub repository/branch/PR; CloudWatch build and runtime logs. |

Use AWS-managed ingress and internet-egress connectors, with auth tokens scoped to **port 8080**. The connection uses a static, non-conflicting API-key marker such as `X-Poc-Connection`, ignored by the worker. Never configure `X-aws-proxy-auth` or `X-aws-proxy-port` in the connection: connection headers override HTTP Task headers.

The only application route is HTTPS `GET /status`, returning pending status while work continues, then exactly `{"ok": true}` after passing tests and confirmed PR creation, or `{"error": "cause"}` for unrecoverable failure/deadline expiry. Failed tests stay in Codex’s existing edit–test–fix loop. HTTP 200 or a successful Codex process exit alone is not task success. Missing/invalid terminal responses must never count as success.

AWS lifecycle hooks start/stop the worker. Logs stay in CloudWatch; changes stay in GitHub. Do not return/store local logs, diffs, commit SHAs, PR URLs, or token-usage artifacts as workflow results. Keep the JWE out of final workflow output/application logs and restrict execution-history access.

## Gate 1 — Prove real execution before orchestration

Do not create Step Functions or its EventBridge connection until a real agent task succeeds and the manually launched VM is confirmed terminated.

### 1. Confirm demonstration inputs

Approve one small repository with existing automated tests, pinned base SHA, objectively testable source/test task, setup/test commands, and a model available to the selected API project. Confirm permission to expose repository content to the model provider and to push a dedicated task branch/create a PR inside the VM. The GitHub token needs repository access, including contents and pull-request write permissions.

Run baseline tests first. **Record:** base SHA, baseline outcome, and expected acceptance test. Avoid deployment-configuration tasks.

### 2. Provision execution resources and access

Propose one private S3 artifact bucket in `eu-central-1`, image-build role, runtime execution role, runtime-credentials secret, CloudWatch log group for image/worker logs, and reusable image. Defer orchestration resources/permissions until Gate 2.

| Principal | Required permissions |
|---|---|
| Build role | `s3:GetObject` on the image artifact; CloudWatch log creation/writes. |
| Runtime role | CloudWatch log writes; `secretsmanager:GetSecretValue` only on the runtime-credentials secret. |
| Provisioning identity | Resource setup; image creation/inspection/deletion; state-machine/connection deployment; `iam:PassRole` for their specific roles. |
| Step Functions role (Gate 2) | `lambda:RunMicrovm`, `lambda:GetMicrovm`, `lambda:CreateMicrovmAuthToken`, `lambda:TerminateMicrovm`; pass the runtime role; `states:InvokeHTTPEndpoint` for workflow GET status requests; `events:RetrieveConnectionCredentials` on the connection; `secretsmanager:GetSecretValue`/`DescribeSecret` on its managed connection secret. |
| Operator | Gate 1: launch/inspect/create token/terminate, read logs, pass runtime role. Gate 2: `states:StartExecution`, `states:DescribeExecution`, history/log reads; retain manual recovery access. |

Build/runtime roles need the documented Lambda trust policy allowing `sts:AssumeRole` and `sts:TagSession`; the workflow role trusts `states.amazonaws.com`. Add KMS decryption only for a customer-managed key. Never place administrator-profile credentials in the image. **Verify:** scoped artifact/secret/log policies and trust relationships.

### 3. Prepare a snapshot-safe image

Use container base `public.ecr.aws/lambda/microvms:al2023-minimal` (snapshot-compatible OpenSSL) and managed base image `arn:aws:lambda:eu-central-1:aws:microvm-image:al2023-1`. The installed CLI supports only `ARM_64`; all binaries/tooling must match.

**Proposed sizing:** 2 GB/1 vCPU baseline, automatic bursting to 8 GB/4 vCPU, maximum disk 8 GB. Disk does not grow with compute bursting.

Package pinned Codex, Git/`gh`, toolchain, Python server, and an empty writable workspace. Include no credentials, task identifiers, or live external connections.

| Lifecycle hook | Proposed behavior |
|---|---|
| `/ready` | Confirm server readiness without starting the agent. |
| `/validate` | Check executable, workspace writes, and basic process execution; no credential-dependent operations. |
| `/run` | Validate configuration, initialize fresh state, spawn worker, return promptly. Clone/model calls run asynchronously; CLI run-hook timeout is 60 seconds. |
| `/terminate` | Stop worker/process group and flush available logs. |

After implementing Step 4, build the image, confirm a successful active version, and pin it for launch. **Verify:** successful build logs and credential-free snapshot validation.

### 4. Implement one worker for both gates

Both manual launch and Step Functions supply structured job data in `runHookPayload`, never an interpolated shell command. Use the same `/run` hook and worker, not a separate feasibility runner.

1. Retrieve credentials; configure Git authentication.
2. Clone the approved repository, check out the pinned base SHA, create a unique task branch, and install declared dependencies.
3. Start Codex once with the task and approved test commands. It edits, tests, inspects failures, repairs, and reruns tests until passing or deadline expiry. Stream JSONL events and stderr to CloudWatch.
4. With a nonempty change and a passing final test command, the same agent commits, runs `git push`, and runs `gh pr create` against the selected base branch. Never merge.
5. On exit, report success only for passing tests plus confirmed PR creation; otherwise report task/process/setup failure or deadline expiry with a cause.

Start with Codex’s documented `workspace-write` sandbox and enable outbound network access for GitHub. Verify unattended/tool compatibility; do not silently fall back to unrestricted execution. On publication timeouts, check for the existing branch/PR before retrying to prevent duplicate PRs. Neither worker nor workflow restarts Codex or reruns the entire task.

### 5. Manually demonstrate and terminate

Launch via AWS CLI using the pinned image, runtime role, managed connectors, structured payload, and proposed lifespan/idle settings below. Retain the VM ID for recovery, wait for `RUNNING`, create one port-8080 token, and request HTTPS `/status` directly—no EventBridge connection or HTTP Task yet.

Confirm unattended Codex/sandbox operation, credentials, setup/model access, meaningful source changes, recorded passing test command/exit code, and agent-side commit/push/PR creation. Inspect CloudWatch output and terminal status. An observed test-failure repair is not required.

Manually terminate on success or failure; use `get-microvm` to confirm `TERMINATED`. **Gate 1 evidence:** record VM ID/outcomes locally; logs remain in CloudWatch and changes in GitHub. Stop if execution, publication, or termination fails. A generic child-process smoke test does not pass this gate.

## Gate 2 — Add Step Functions orchestration

### 6. Deploy and verify the lifecycle workflow

Create one Standard state machine/execution role and one EventBridge connection after Gate 1. EventBridge stores connection credentials in its own managed Secrets Manager secret. Reuse the proven image/worker. Validate the state-machine definition, inspect policies/trust, and check that connection headers cannot override VM auth/port headers.

1. **Launch:** use `arn:aws:states:::aws-sdk:lambdamicrovms:runMicrovm` with the pinned image. Derive `clientToken` from execution name; retain VM ID and endpoint in execution state.
2. **Readiness:** `Wait`/`getMicrovm` until `RUNNING`; unexpected termination is an error. Do not repeat lifecycle checks on every status poll; reserve them for failure diagnosis and termination confirmation.
3. **Authenticate once:** use `arn:aws:states:::aws-sdk:lambdamicrovms:createMicrovmAuthToken`, scoped to port 8080. Propose 30-minute expiration; reuse across polls without token refresh. Verify expiration support/reuse live.
4. **Poll:** `arn:aws:states:::http:invoke`, `Method: GET`, dynamic `https://<endpoint>/status`, `InvocationConfig.ConnectionArn`, dynamic `X-aws-proxy-auth`, and `X-aws-proxy-port: 8080`. Inspect JSON body. Pending results wait then repeat only HTTP polling; preserve only terminal success/error for final outcome.
5. **Clean up:** route terminal results, catchable post-launch/HTTP errors, and polling deadline to shared cleanup, preserving VM ID/cause. Call `terminateMicrovm`; `Wait`/`getMicrovm` until `TERMINATED`. Then `Succeed` for `ok`, otherwise `Fail` with cause. Report cleanup failures instead of claiming termination.
6. **Launch ambiguity:** fail if launch fails without a known VM ID; a VM launched despite ambiguous API failure remains bounded by its lifespan. Never restart the task.

| Proposed timing | Value |
|---|---|
| Worker deadline / polling deadline | 25 / 26 minutes; polling deadline routes to cleanup. |
| Overall workflow timeout / VM maximum lifespan | 28 / 30 minutes. |
| Auth token expiration | 30 minutes, covering polling deadline. |
| Status request timeout / poll interval | 10 / 30 seconds; HTTP Tasks allow at most 60 seconds per request. |
| Idle policy | `maxIdleDurationSeconds: 3600`, `suspendedDurationSeconds: 0`, `autoResumeEnabled: false`; suspension is beyond VM lifespan. |

Use `maximumDurationInSeconds: 1800` for launch. Amazon States Language parameters use PascalCase (`ImageIdentifier`, `RunHookPayload`, `MaximumDurationInSeconds`, etc.). Serialize per-VM job configuration into `runHookPayload` within its **16 KB** limit; never put secret values in execution input.

**Trigger:** `aws stepfunctions start-execution` with repository, pinned SHA, and task as JSON; configuration supplies image version, runtime role, connectors, and credentials-secret ARN. Use profile `AWSAdministratorAccess-172318459814`, region `eu-central-1`; inspect with `aws stepfunctions describe-execution`. Retry failed submission with the same execution name/input, not a new job.

Closing the initiating terminal does not stop execution. Overall timeout or manual `StopExecution` bypasses `Catch` cleanup; VM lifespan or manual termination is the fallback. Recover with `aws lambda-microvms terminate-microvm --profile AWSAdministratorAccess-172318459814 --region eu-central-1 --microvm-identifier "$MICROVM_ID"`, then confirm termination.

## Acceptance and verification

Final success requires Gate 2, not only the manual run:

- One manually submitted task launches exactly one VM from a pinned image and continues without a local controller.
- Agent, source edits, shell commands, and tests run inside that VM; record a passing test command/exit code. Observed test-failure repair is optional.
- Commit descends from submitted base SHA; agent pushes one task branch and creates one PR inside the VM, without merging.
- Logs remain in CloudWatch; changes remain in GitHub; worker terminal success is only `{"ok": true}`. No artifact download/local publication.
- Demonstrate token reuse across status polls, explicit termination, confirmed `TERMINATED`, and workflow `Succeed`.

Also verify:

- **One controlled failure:** proposed nonexistent base commit fails checkout before Codex starts. Worker reports `{"error": "cause"}`; no PR; workflow terminates/confirms VM and finishes in `Fail` retaining cause.
- **Separate hard-lifespan check:** launch with short `maximumDurationInSeconds`, idle suspension beyond lifespan, and a non-publishing setup-failure job. Without explicit termination, confirm automatic `TERMINATED`; no model call/agent repair needed.
- **Small negative-path tests where practical:** setup/process failure, worker deadline, missing/invalid terminal responses, HTTP errors, polling deadline, and cleanup routing via stubs/simulation.

Defer exhaustive live demonstrations of invalid credentials, unresolved test failures, missing/invalid endpoint auth, connection failures, stopped executions, and terminal closure. Authentication/error handling still applies: test failures remain in the agent loop; catchable errors/deadlines clean up; aborts/overall timeout rely on lifespan. No separate live demonstration of every failure mode is required.

## Risks, constraints, and existing evidence

- **Compatibility unproven:** ARM64 binaries, sandbox, repository tooling, authenticated HTTP requests, token expiration/reuse, and OpenAI/GitHub access need live verification.
- **Credential exposure:** code in the VM can misuse model/GitHub credentials. Use a controlled repository and repository-scoped token; this accepted PoC trade-off is not suitable for arbitrary hostile repositories.
- **Workspace loss:** termination destroys local state. Publish before success; failed runs retain only available CloudWatch logs, with no workspace recovery.
- **Test limitations:** record actual exit codes, inspect diffs for weakened tests, and require human PR review before merging.
- **Limits/performance:** time, compute, and disk limits may interrupt tasks. Start small; increase baseline only if measurements justify it. Measure launch latency; clone/setup/model/tests will likely dominate duration.
- **CLI differences:** installed CLI uses `--resources` with `minimumMemoryInMiB` and only `ARM_64`. Output-skeleton generation failed on AWS-generated sample fields; this is not evidence of a live API failure.
- **Abort cleanup:** stopping/timing out Step Functions does not stop the VM; lifespan/manual recovery remains essential. Independent executions are not automatically serialized.

Read-only checks on **2026-10-30** used the profile above: identity, image discovery, quotas, resources, pricing, CLI help/schema, official docs, and state-machine definition validation covering all four lifecycle/auth integration ARNs and the token-generation/direct-HTTP-polling sequence. HTTP definition validation passed with an EventBridge connection and failed without one. No live HTTP request, state machine/connection creation, application-code change, or AWS-resource change occurred during planning.

### Technical references

- AWS MicroVM [networking](../../aws-lambda-microvms/microvms-networking.md), [security](../../aws-lambda-microvms/microvms-security.md), [images/sizing](../../aws-lambda-microvms/microvms-images.md), and [snapshots](../../aws-lambda-microvms/microvms-images-snapshots.md).
- [Codex non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode).
- Step Functions [AWS SDK integrations](https://docs.aws.amazon.com/step-functions/latest/dg/supported-services-awssdk.html) and [HTTP Tasks/connections](https://docs.aws.amazon.com/step-functions/latest/dg/call-https-apis.html).
