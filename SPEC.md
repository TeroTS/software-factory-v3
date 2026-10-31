# Product Specification

Source: `DECISIONS.md`

## Summary
- An operator-led software factory PoC that turns one approved coding task into
  a tested GitHub PR inside an AWS Lambda MicroVM, then confirms VM termination.
  This specification defines accepted behavior, not implementation status or
  proof of runtime feasibility. Preparation outputs and verification evidence
  belong only in [the current slice](docs/slices/current-slice.md).
  The [approved Gate 2 reset](docs/plans/gate2.md) replaces the disposable Gate 1
  infrastructure/image/worker using native Python CDK, preserving the existing
  GitHub-token secret and published PR.

## Goals
- Gate 1: manually prove the real coding task, agent-side publication, and
  confirmed manual termination before creating Gate 2 orchestration resources.
- Gate 2: use native Python CDK infrastructure and a newly validated pinned
  image/worker in a manually started Step Functions Standard execution that
  launches exactly one VM, reuses one auth token across status polls, confirms
  termination, and finishes in `Succeed` without a running local controller.
- Demonstrate one controlled Gate 2 failure with no PR, its cause in CloudWatch,
  confirmed termination, and workflow `Fail`.
- Separately prove automatic hard-lifespan termination. Retain VM IDs and gate
  verification outcomes locally, build/runtime logs in CloudWatch, and published
  work in GitHub.

## Non-Goals
- Production readiness, hostile or arbitrary repositories, parallel jobs,
  multi-agent operation, load SLOs, durable queues, or concurrency-lock services.
- Custom agent loops, agent restarts, automatic whole-task reruns, separate
  worker/polling deadlines, inference proxies, model API keys, OpenAI Agents API
  integration, or changing provider/model to bypass a feasibility failure.
- Local publication, Git bundles, workspace downloads or recovery, direct pushes
  to `main`, agent-side PR merging, or changes to demonstration deployment config.
- VPCs, NAT Gateways, load balancers, databases, webhooks, polling/orchestration
  Lambdas, or a general-purpose remote shell endpoint.
- Required observation of test-failure repair or exhaustive live fault injection,
  including credential, authentication, network, stopped-execution,
  unresolved-test-failure, and terminal-closure demonstrations. Required failure
  handling is not deferred with those demonstrations.

## User Stories

### US-01: Prepare and pin the demonstration baseline
Primary actor: Operator

Trigger: The approved demonstration fixture is not yet prepared.

Happy-path action: Prepare and test the calculator fixture in
`TeroTS/factory-smoke-test`, then record its committed baseline SHA.

Visible outcome: One tested demonstration baseline is available for both gates.

Authoritative state transition: The README-only repository becomes a committed,
tested calculator baseline pinned by its recorded GitHub commit SHA.

Slice boundary: Demonstration source/tests and local baseline evidence; one
focused end-to-end check prepares the fixture and verifies the recorded commit
and test result. No factory deployment or agent invocation.

Acceptance criteria:
- The dependency-free baseline exposes `calculator.py:add(a, b)` with passing
  standard-library tests in `tests/test_calculator.py`; no third-party packages
  or installation are required.
- From the demonstration repository root, run
  `python3 -m unittest discover -s tests -v`; record the command, actual exit
  code, baseline outcome, resulting SHA, and expected acceptance test.
- Fix the agent task as adding `multiply(a, b)` and positive, negative, and zero
  operand tests while preserving addition. Both gates use this task and the same
  tested baseline, target `main`, and publish separate unique task branches/PRs.
- The original README-only SHA
  `92a9c6f062c65d9fa1765005ae60627fb7e5ae31` is not the demonstration baseline.
  Do not invent a replacement SHA before preparation or merge either gate's PR.

Deferred follow-ups:
- US-02: Manually run the task through publication.
- US-05: Demonstrate a controlled orchestration failure after Gate 2 is available.

### US-02: Manually obtain a tested task PR
Primary actor: Operator

Trigger: US-01 supplies the pinned baseline and acceptance test.

Happy-path action: Launch the approved agent task manually with AWS CLI in one
MicroVM using the shared lifecycle/worker implementation.

Visible outcome: Codex publishes one meaningful, tested task PR from inside the
VM, and `GET /status` reports `{"status":"ok"}`.

Authoritative state transition: The initialized job moves from
`{"status":"pending"}` to stable `{"status":"ok"}` only after verified task
completion and PR creation.

Slice boundary: The Gate 1 image, scoped execution resources, lifecycle/worker,
GitHub publication, status API, and runtime evidence required for one real
end-to-end task. Manual termination is US-03; this story alone does not pass
Gate 1.

Acceptance criteria:
- Provision only Gate 1 resources in `us-east-1`: one private S3 artifact bucket,
  separate image-build/runtime roles, one GitHub-token runtime-credentials
  secret, one CloudWatch log group, and one reusable image. Use provisioning
  profile `AWSAdministratorAccess-172318459814`; never transfer its credentials
  into the image. Inspect scoped policies and trust relationships against the
  [source permission matrix](docs/plans/aws-lambda-microvm-software-factory-poc.md#step-2--create-only-the-execution-resources).
  Defer Step Functions, its role/permissions, and the EventBridge connection.
- Build a pinned, active image using
  `public.ecr.aws/lambda/microvms:al2023-minimal` and managed base
  `arn:aws:lambda:us-east-1:aws:microvm-image:al2023-1`. Include Linux ARM64 Python,
  Git, `gh`, and a pinned Codex executable supporting the native provider. Use
  2 GB / 1 vCPU baseline, bursting to 8 GB / 4 vCPU, and 8 GB maximum disk;
  revisit sizing only when measurements justify it. Factory Python 3.14/`uv`
  tooling does not establish MicroVM Python or ARM64 compatibility.
- The snapshot has an empty writable workspace and no credentials, task IDs,
  live external connections, or per-job state. On port 8080, implement full
  `/aws/lambda-microvms/runtime/v1/` hook paths: `ready` checks server readiness
  without starting Codex; `validate` checks executables, workspace writes, and
  basic process execution without credential-dependent operations; `run`
  validates structured job configuration, initializes fresh state, and spawns
  the worker asynchronously within the run-hook limit; `terminate` stops its
  process group and flushes available logs. Cloning/model calls occur only in
  the worker. Cover setup/process failures with small tests where practical.
- Supply the repository, pinned base SHA, and task as structured
  `runHookPayload` within 16 KB, never an interpolated shell command or secret
  values. Deployment supplies the pinned image, roles, managed ingress and
  internet-egress connectors, and credentials-secret ARN. Run one VM/job at a
  time; independent executions are not automatically serialized.
- Retrieve only the repository-scoped GitHub token at runtime, with contents
  and pull-request write permissions, and configure Git via `gh auth setup-git`.
  Scope runtime secret access to that secret, logs to the designated group, and
  Bedrock access to the selected profile, destination models/Regions, and default
  project. Use execution-role credential discovery/refresh, not administrator
  credentials or model API keys.
- Use native `amazon-bedrock-runtime`, `us.openai.gpt-6.1-sol`,
  `AWS_REGION=us-east-1`, disabled hosted web search, and Codex's `workspace-write`
  sandbox with outbound GitHub access. Verify actual selected-model access,
  runtime credential discovery/refresh, unattended harmless shell-tool
  execution, and sandbox/tool compatibility before accepting the task result.
  Stop on failure rather than changing provider/model or disabling the sandbox.
  The inference profile may route within the United States beyond `us-east-1`.
- Clone and check out the pinned baseline, create a unique task branch, and run
  one Codex invocation owning editing, testing, fixing, and publication. Failed
  tests feed the same agent loop, not an immediate terminal error. Neither
  worker nor workflow restarts Codex or reruns the whole task.
- Before Codex commits, pushes, and runs `gh pr create`, final approved tests
  pass with the recorded command and actual exit code. Verify positive,
  negative, and zero multiplication and preserved addition; inspect a nonempty,
  meaningful source/test diff for weakened tests. The commit descends from the
  pinned baseline and the PR targets `main`, remains unmerged, and is available
  for human review. On publication timeouts, the same agent checks for an
  existing branch/PR before retrying to avoid duplicates.
- After retaining the VM ID and waiting for `RUNNING`, obtain one port-8080 JWE
  with 30-minute expiration and reuse it for HTTPS status requests carrying
  `X-aws-proxy-auth` and `X-aws-proxy-port: 8080`. HTTP 200 returns exactly one of
  `{"status":"pending"}`, `{"status":"ok"}`, or `{"status":"error"}`; terminal
  status is stable. Return no cause, logs, diff, commit SHA, PR URL, or usage
  artifacts. A successful process exit, HTTP 200, or missing status is not
  success. Causes and available command/test output remain in CloudWatch.
- Exclude secrets from input, prompts, and application logs. Sending synthetic
  code and relevant output to the selected model is approved. Repository code
  can access the runtime role and GitHub token: this accepted trade-off applies
  only to the controlled demonstration repository.
- Apply a 30-minute VM maximum lifespan and idle policy
  `maxIdleDurationSeconds: 3600`, `suspendedDurationSeconds: 0`,
  `autoResumeEnabled: false`. Retain the pinned CLI/image version, VM ID, and
  verification outcomes locally. Execution/publication failure blocks Gate 2
  and still requires manual termination/recovery under US-03.

Deferred follow-ups:
- US-03: Manually terminate the VM and establish Gate 1's result.
- US-04: Submit the task through orchestration with a newly validated image and
  worker after Gate 1 passes.

### US-03: Confirm manual termination and the Gate 1 result
Primary actor: Operator

Trigger: The manually launched task has completed or failed.

Happy-path action: Manually terminate the retained MicroVM and inspect its
lifecycle until termination is confirmed.

Visible outcome: Local evidence confirms VM cleanup and whether Gate 1 passed.

Authoritative state transition: The retained MicroVM reaches `TERMINATED`,
confirmed by the service's lifecycle inspection.

Slice boundary: Manual AWS CLI termination, lifecycle confirmation, and local
gate evidence; one end-to-end check verifies the actual VM's terminated state.

Acceptance criteria:
- Terminate on either success or failure, retain the VM ID for recovery, and
  confirm `TERMINATED`; a termination request alone is insufficient.
- Accept Gate 1 only when US-02's real task, runtime checks, publication, and
  this termination confirmation all pass. Stop if execution, publication, or
  termination fails; do not create Gate 2 resources.
- Retain gate outcomes locally and manual termination/log-inspection access.
  Report cleanup failures instead of claiming confirmed termination.
- Termination destroys the workspace. GitHub retains published work and
  CloudWatch retains available logs; no failed-workspace recovery or export.

Deferred follow-ups:
- US-04: Submit the successful task through Step Functions after Gate 1 passes.
- US-06: Independently verify automatic hard-lifespan termination.

### US-04: Submit a task that succeeds with orchestrated cleanup
Primary actor: Operator

Trigger: Gate 1 has passed; Gate 2 follows the approved native Python CDK reset.

Happy-path action: Manually start one Step Functions Standard execution for the
approved task and pinned baseline.

Visible outcome: One separate tested task PR is published and the workflow
finishes in `Succeed` after confirming VM termination, without a local controller.

Authoritative state transition: The Step Functions execution moves from running
to `Succeed` only after worker `{"status":"ok"}` and confirmed `TERMINATED`.

Slice boundary: Native Python CDK infrastructure, a replacement image/worker,
Gate 2's state machine, execution role, EventBridge connection, authenticated
polling, and cleanup; one focused end-to-end run proves publication, token
reuse, and confirmed cleanup. Reconcile reset requirements and resolve worker
acceptance and image provisioning within this story before implementation.

Acceptance criteria:
- Define all Gate 2 infrastructure using native Python CDK; do not include the
  old CloudFormation template or adopt its stack. Resolve image resource support,
  bootstrap requirements, creation/update lifecycle, and successful active version
  pinning before implementation or destructive transition. Do not silently use
  manual-only provisioning or introduce an unapproved custom-resource provider.
- Replace disposable Gate 1 resources/image only after explicit operator
  authorization, preserving the GitHub-token secret and unmerged Gate 1 PR.
  Validate the newly built image's actual runtime-role/model/sandbox/tool
  compatibility; Gate 1's evidence does not establish compatibility of a rebuild.
- The replacement worker retains the existing Job and lifecycle/status contracts.
  One Codex invocation owns editing, testing, fixes, and publication. Before
  reporting stable `ok`, verify passing approved tests with actual exit code `0`,
  meaningful source/test changes without weakened tests, baseline ancestry,
  and agent-side publication of one unique-branch PR to `main`, left unmerged.
  Process exit or HTTP 200 alone is insufficient. Unrecoverable failures report
  stable `error`, with causes in CloudWatch and no secrets in input/prompts/logs.
  This specifies required evidence, not reuse of calculator-specific worker code.
- Only after Gate 1 passes, add one Standard state machine/execution role and
  one EventBridge connection with its automatically managed Secrets Manager
  secret. Validate the definition and inspect scoped roles/trust policies before
  deployment using the source permission matrix. All US-02 task, publication,
  credential, networking, status, snapshot, and normal VM-limit rules still apply.
- Launch exactly one VM with the execution name as launch client token. Retry
  uncertain `StartExecution` with the same name and input rather than a new job.
  Retain the VM ID; wait/get until `RUNNING`, create the port-scoped token once,
  and poll directly using HTTP Tasks and the EventBridge connection. No separate
  agent runner, polling Lambda, or local controller is introduced.
- The connection supplies only a static `X-Poc-Connection` marker ignored by
  the worker; it must not override dynamic `X-aws-proxy-auth` or
  `X-aws-proxy-port`. Prove authenticated status access and reuse of the one
  30-minute token across polls; no token-refresh loop.
- Wait 30 seconds after pending responses and limit each HTTP request to
  10 seconds. Check readiness before polling, not with `getMicrovm` on every
  poll; later lifecycle checks serve diagnosis and termination confirmation.
- On terminal success, terminate and wait/get until `TERMINATED` before
  `Succeed`. Catchable post-launch errors share cleanup states and retain the
  VM ID before `Fail`. Worker errors, unexpected termination, HTTP failures,
  and invalid/missing responses never become success. Cover these paths and
  cleanup routing with small simulated tests where practical.
- A failed launch without a known VM ID ends in `Fail`; ambiguously launched
  VMs remain lifespan-bounded. Report cleanup failures, never assert unconfirmed
  termination, and do not restart Codex or automatically rerun the task.
- Configure the 28-minute overall workflow timeout, with no additional job-level
  worker/polling deadline. Verify configuration and simulate persistent pending/
  timeout behavior where practical. Workflow timeout or manual `StopExecution`
  does not run `Catch` cleanup or stop the worker; VM lifespan and manual recovery
  are fallbacks. Different workflow/VM start times do not guarantee two minutes
  for cleanup.
- Restrict execution-history access because the JWE passes through execution
  state; omit it from final output and application logs. Retain local VM/gate
  evidence, CloudWatch causes/logs, and the unmerged GitHub PR; no workspace
  artifacts are downloaded.

Deferred follow-ups:
- US-05: Demonstrate controlled failure and orchestrated cleanup.
- US-06: Separately confirm automatic hard-lifespan termination.

### US-05: Observe a controlled orchestration failure with cleanup
Primary actor: Operator

Trigger: Gate 2 is available and a SHA is confirmed absent from the repository.

Happy-path action: Submit an otherwise valid job using that nonexistent base SHA.

Visible outcome: The workflow finishes in `Fail` after confirmed VM termination,
with no PR and the checkout failure cause retained in CloudWatch.

Authoritative state transition: The Step Functions execution moves from running
to `Fail` after worker `{"status":"error"}` and confirmed `TERMINATED`.

Slice boundary: The existing worker's checkout-failure path and shared workflow
cleanup; one focused end-to-end negative run, not a new runner or failure API.

Acceptance criteria:
- Checkout fails before Codex starts; no agent/model invocation or PR publication
  occurs. Worker status becomes stable `{"status":"error"}` without exposing
  the cause in the response; CloudWatch retains the cause.
- The workflow retains the VM ID, follows shared cleanup, confirms `TERMINATED`,
  and finishes in `Fail`. Record the VM ID and verification outcomes locally.
- A missing response or cleanup failure cannot satisfy this demonstration or
  be represented as successful task completion/confirmed cleanup. US-04's
  authentication, secret handling, timeout, and recovery rules remain in force.

Deferred follow-ups:
- US-06: Separately verify automatic termination without an explicit request.

### US-06: Verify the independent hard-lifespan fallback
Primary actor: Operator

Trigger: The newly validated pinned Gate 2 image is available for a separate
lifespan check, using a base SHA confirmed absent from the repository.

Happy-path action: Launch the non-publishing job with a two-minute maximum
lifespan and observe it without requesting termination.

Visible outcome: The VM automatically reaches `TERMINATED` under its hard
lifespan, independently of explicit workflow cleanup.

Authoritative state transition: The MicroVM reaches service-confirmed
`TERMINATED` through automatic hard-lifespan enforcement.

Slice boundary: A separate manual VM launch and lifecycle observation using the
Gate 2 pinned image/worker; one focused end-to-end check of the automatic safety
bound. No new orchestrator or job deadline.

Acceptance criteria:
- Use the nonexistent-base-commit job, the pinned Gate 2 image, a two-minute maximum
  lifespan, and the normal one-hour idle policy with suspension/resume disabled.
  No Codex/model call or PR is needed; do not explicitly terminate during the
  automatic-termination check.
- Confirm automatic `TERMINATED` and record the VM ID and result locally. If
  automatic termination fails, record failure and terminate manually; manual
  recovery does not count as passing this check.
- This is separate from successful execution and controlled Gate 2 failure;
  normal jobs retain the accepted 30-minute VM lifespan.

Deferred follow-ups:
- None.

## Open Questions
- Within US-04 preparation, resolve native CDK image resource support and
  bootstrap requirements before implementation. Any necessary dependency or
  architecture change requires approval; do not infer support from the CLI API.
