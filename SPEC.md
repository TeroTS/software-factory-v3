# Product Specification

Source: `DECISIONS.md` and operator-approved follow-ups in
[docs/plans/todo.md](docs/plans/todo.md).

US-01–US-04 retain the original gate requirements. US-07–US-14 are ordered
follow-ups before US-05/US-06; existing story IDs remain stable. As delivered,
these follow-ups replace demonstration-specific worker validation with repository
scripts and manual GitHub review, and replace the 30-second pending poll interval
with 10 seconds. Reconcile conflicting decisions, vocabulary, and interface
contracts before implementing the affected story; this backlog does not authorize
deployment, live jobs, or destructive replacement.

For US-08–US-14, record story-specific implementation details, dependency choices,
progress, verification evidence, and live-run guidance only in
`docs/slices/current-slice.md`. These stories do not require or authorize updates
to `README.md`, `AGENTS.md`, or `DECISIONS.md`. Required interface-contract changes
remain in scope; ask the operator before resolving a conflict through changes to
accepted decisions or repository guidance.

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
- Production readiness, hostile or unapproved repositories, parallel jobs,
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
- US-07–US-14: Deliver the runtime, worker, configuration, workflow, and test
  follow-ups below before the remaining gate demonstrations.
- US-05: Demonstrate controlled failure and orchestrated cleanup.
- US-06: Separately confirm automatic hard-lifespan termination.

### US-07: Run the worker on a maintained image Python runtime
Primary actor: Operator

Trigger: The image runtime needs a maintained Python version.

Happy-path action: Build and validate an image using an explicitly selected,
maintained interpreter.

Visible outcome: The worker, its subprocesses, and repository scripts execute
successfully on the new Linux ARM64 runtime.

Authoritative state transition: The image runtime changes from Python 3.9 to a
newly validated, explicitly selected maintained version.

Slice boundary: Image interpreter selection and affected startup, compatibility,
and tests; one focused image check executes the worker with the new interpreter.
Source: todo item 1.

Acceptance criteria:
- Evaluate Python 3.13 as a candidate, first checking versioned package
  availability on Amazon Linux 2023 ARM64. Do not treat this candidate as an
  accepted version before selection and compatibility validation.
- Explicitly select the interpreter in the Dockerfile, worker startup,
  subprocesses, and repository test/script execution. Update compatibility
  settings, affected tests, and documentation consistently.
- Run `make verify` and `make preflight`; validate the newly built image before
  deployment. Factory Python 3.14 tooling does not establish image compatibility.
- Preserve snapshot cleanliness, sandbox enforcement, and failure handling;
  deployment and any externally managed runtime installation require separate
  authorization.

Deferred follow-ups:
- US-08: Use boto3 for selected AWS operations.

### US-08: Access runtime identity and the GitHub secret through boto3
Primary actor: Operator

Trigger: A compatible maintained image runtime is selected under US-07.

Happy-path action: Run a job whose worker uses boto3 to check runtime identity
and retrieve the authorized repository's GitHub token.

Visible outcome: The job initializes with the same scoped identity and secret
checks without parsing AWS CLI responses for these two operations.

Authoritative state transition: Runtime authorization and token retrieval move
from CLI subprocesses to validated SDK responses before repository/model access.

Slice boundary: STS and Secrets Manager access, image dependency integration, and
failure tests; one focused worker initialization check verifies scoped access.
Source: todo item 2.

Acceptance criteria:
- Replace STS identity and Secrets Manager calls in `worker/task.py` with boto3
  clients. Retain the expected account/role guard before secret access and
  nonempty token validation.
- Initially retain CLI credential export, expiration checks, and AWS CLI
  availability. Do not use private botocore expiration attributes or assume
  SDK refresh also refreshes credentials inside the separate Codex process.
- Configure bounded API requests and sanitized failure reporting. Failed
  authorization or secret retrieval reports error without secret-bearing logs,
  automatic Job retries, or model access.
- Use botocore `Stubber` in affected tests; retain identity, expiration,
  invalid-secret, and redaction regressions. Pin the approved dependency using
  project conventions; validate actual MicroVM credential-provider compatibility
  under separate live-run authorization.
- Run focused tests, `make verify`, and applicable image preflight checks.

Deferred follow-ups:
- US-09: Make worker execution stages explicit.

### US-09: Execute a job through explicit worker stages
Primary actor: Operator

Trigger: Worker execution needs a readable stage boundary without changing behavior.

Happy-path action: Run a job through environment preparation, repository
preparation, agent invocation, and independent completion verification.

Visible outcome: The same job succeeds or fails with unchanged stage ordering,
validation, publication, and sanitized diagnostics.

Authoritative state transition: `execute()` becomes a coordinator of focused
functions while the Job lifecycle remains pending to stable ok/error.

Slice boundary: `worker/task.py` and connected worker tests; one focused
simulated-service job verifies the complete staged path. Source: todo item 3.

Acceptance criteria:
- Extract `prepare_environment()`, `prepare_repository()`, `invoke_agent()`, and
  `verify_completion()`; keep coordination and the final credential check in
  `execute()`.
- Use explicit inputs/returns and existing helpers. Introduce no class, generic
  workflow framework, or dependency merely for extraction.
- Preserve order, trust-boundary validation, secret redaction, failure
  propagation, and one Codex invocation with independent completion checks.
- Update callers and meaningful success/failure tests; run focused tests and
  `make verify`. This extraction alone does not change completion guarantees.

Deferred follow-ups:
- US-10: Separate static Codex policy from invocation arguments.

### US-10: Run Codex with factory-owned static configuration
Primary actor: Operator

Trigger: Static Codex settings are mixed with per-job invocation arguments.

Happy-path action: Run a job with approved Codex settings loaded from
factory-owned configuration baked into the image.

Visible outcome: The configured model/provider and security policies apply
without the target repository overriding approved constraints.

Authoritative state transition: Static settings move from inline CLI arguments
to validated factory configuration; per-job arguments remain runtime values.

Slice boundary: Codex configuration, invocation, and image integration; one
focused invocation check confirms effective settings. Source: todo item 4.

Acceptance criteria:
- Move model/provider, sandbox mode/network access, web search, approval, and
  shell environment policies into factory-owned configuration while retaining
  accepted values and excluding secret values.
- Keep `exec`, `--ephemeral`, repository path, `.git` access, and stdin
  prompt input in Python as invocation-specific arguments. The operator-approved
  event-removal follow-up supersedes the earlier `--json` requirement: wait for
  process exit, log redacted diagnostics afterward, and propagate nonzero exit
  without collecting or analyzing events or validating the initial shell check
  from command events. Retain managed sandbox policy, image/runtime sandbox
  validation, the prompt's initial shell check, and independent completion checks.
- Verify loading and precedence with the pinned Codex version before choosing
  the location. Repository configuration must not override approved constraints;
  retain explicit enforcement where configuration alone is insufficient.
- Test effective settings and invocation arguments, including override attempts;
  run focused tests, `make verify`, and `make preflight`.

Deferred follow-ups:
- US-11: Run jobs against configuration-authorized repositories.

### US-11: Run a job in an authorized repository using its scripts
Primary actor: Operator

Trigger: An approved repository other than the demonstration needs a coding job.

Happy-path action: Submit the existing structured Job for a configuration-authorized
repository with executable setup and verification scripts.

Visible outcome: One Codex invocation publishes a task PR without calculator or
Gate 2 smoke-repository assumptions in the worker.

Authoritative state transition: The authorized Job moves from pending to stable
ok only after independent script verification and matching Git/PR publication;
unrecoverable failures become stable error.

Slice boundary: All `worker/` code and affected configuration, callers, image,
contracts, fixtures, and tests; one focused job against another authorized
repository proves the script-based workflow. Source: todo item 5.

Acceptance criteria:
- Remove smoke-repository names, calculator task/file restrictions, fixed Python
  test commands, and repository-specific validation from `worker/`. Use existing
  configuration conventions for authorization, PR target, and necessary values;
  generated infrastructure references remain deployment-controlled.
- Preserve the Job fields (`repository`, `baseSha`, `task`), bounded structured
  input, lifecycle/status contracts, and explicit rejection of unauthorized jobs.
  Configuration authorization is not unrestricted or hostile-repository support.
- Run `./scripts/setup` and baseline `./scripts/verify` from the pinned target
  repository before Codex. Missing, non-executable, or failing scripts fail
  explicitly. Instruct the same agent to run passing final `./scripts/verify`
  before publication; independently run verification afterward.
- Preserve one unique branch, one Codex edit–test–fix/publication invocation,
  nonempty committed changes, baseline ancestry, clean workspace, matching remote
  head, and one open unmerged PR. Do not restart the agent, rerun the whole task,
  merge a PR, or publish directly to the configured target branch.
- The configured PR target branch may advance during a Job. Require task-commit
  ancestry from pinned `baseSha` and matching local/remote/PR task heads, not
  equality between the PR target head (`baseRefOid`) and `baseSha`. Worker success
  does not prove compatibility with the current target branch; that remains a
  merge-time check. Do not change GitHub merge policy as part of this follow-up.
- Coordinate removal of demonstration-specific evidence with US-12. Do not claim
  automated proof of verification-before-publication after chronology validation
  is removed; manual GitHub review judges change correctness and test adequacy.
- Preserve scoped credentials, identity guards, sandbox enforcement, redaction,
  and failure reporting. Review scripts' access to runtime credentials and
  reconcile the controlled-repository trust assumption before enabling another
  repository; never silently broaden credential scope.
- Update affected interface contracts and fixtures.
  The factory's own Make-based workflow remains unchanged. Cover another
  authorized repository, unauthorized input, missing/failing scripts, and
  verification/publication failures; run focused tests, `make verify`, and
  `make preflight` without adding a generic workflow framework.

Deferred follow-ups:
- US-12: Remove obsolete demonstration evidence validation entirely.

### US-12: Review a task PR without calculator-specific acceptance gates
Primary actor: Human reviewer

Trigger: Repository-agnostic jobs use the script contract from US-11.

Happy-path action: Inspect the published GitHub diff and tests to judge task
correctness and test adequacy.

Visible outcome: A mechanically verified task PR is available for human review
without rejection for altering baseline code or missing calculator operand cases.

Authoritative state transition: Automated completion relies on final script and
Git/PR checks rather than baseline AST preservation or command chronology; task
correctness remains a human review decision, not an automatic merge.

Slice boundary: Removal of `worker/evidence.py`, calculator acceptance, and
connected callers/tests/image references; one focused job changing existing code
produces a matching PR without the obsolete evidence gates. Source: todo item 6.

Acceptance criteria:
- Remove `preserve_nodes()`, `verify_source()`, `verify_events()`, and unused
  `command_words()`, including their callers and dedicated tests.
- Move `verify_pr()` into `worker/task.py`, preserving generic PR validation,
  regression coverage, and configuration-controlled repository-specific values.
- Replace `TEST_COMMAND` with `./scripts/verify`, then delete
  `worker/evidence.py` and unused imports.
- Delete `image/acceptance.py`, its worker invocation/path constant, and associated
  image/build references and calculator-acceptance tests.
  Do not relocate or retain those checks in demonstration testing.
- Retain generic worker coverage in `tests/test_worker.py` and elsewhere for
  credentials, verification failures, clean commits, and PR consistency; replace
  repository-specific fixtures where necessary rather than deleting the suite.
- Independent final verification and Git/PR checks remain required. Neither
  those checks nor manual diff review proves verification preceded publication;
  no structural protection against weakened tests remains. Reflect these reduced
  automated guarantees in affected interface contracts and the current slice note.
- Run focused tests, `make verify`, and `make preflight`; investigate coverage
  changes without hiding removed code through narrower measurement scope.

Deferred follow-ups:
- US-13: Simplify lifecycle orchestration using higher-level CDK constructs.

### US-13: Run a job through a simplified CDK lifecycle workflow
Primary actor: Operator

Trigger: The repository-agnostic worker and validated pinned image are available.

Happy-path action: Start one Standard execution whose CDK-defined workflow polls
the worker and shares confirmed termination after either job outcome.

Visible outcome: Success reaches Succeed only after worker ok and confirmed
TERMINATED; failures retain a recovery ID and distinguish unconfirmed cleanup.

Authoritative state transition: Execution moves from running to Succeed/Fail
through fresh lifecycle/status observations and a shared termination sequence.

Slice boundary: `infra/workflow.py`, stack integration, and transition/synthesis
checks; one focused orchestration check follows launch through confirmed cleanup.
Source: todo item 8, including its detailed state list.

Acceptance criteria:
- Remove `InspectImage`, `ImageReady`, `InspectVersion`, and `VersionReady` from
  per-execution flow. Deployment supplies a newly validated, explicitly pinned
  image/version plus role, networking, and logs; launch failures remain caught.
  VM ID and endpoint are dynamic launch results, never static configuration.
- Prefer `aws_cdk.aws_stepfunctions` high-level `StateMachine`, `Pass`, `Choice`,
  `Wait`, `Succeed`, and `Fail` with explicit connections. Use
  `aws_stepfunctions_tasks` for supported integrations; verify pinned-CDK
  MicroVM/HTTP support and use `CustomState` only where necessary. Add no compute
  or dependency upgrade solely to avoid a raw state.
- Follow the state plan: Input → LaunchVM → CheckLaunch → WaitForVMToStart →
  GetVMState → IsVMRunning → GetWorkerToken → CheckWorkerToken → PollWorker →
  CheckWorkerStatus. Wait 5 seconds for startup; PENDING repeats a fresh lifecycle
  call, RUNNING continues, and unexpected states enter failure handling.
- Require a nonempty VM ID and endpoint, and one valid port-scoped token reused
  across polls. A missing endpoint with a known ID enters termination; no known
  ID ends in failure with lifespan/manual recovery as fallback.
- Pending status follows WaitForWorker for 10 seconds, then PollWorker again;
  each HTTP request remains bounded to 10 seconds. Worker ok enters RecordSuccess;
  worker error, invalid/missing responses, and catchable post-launch API/HTTP
  failures enter RecordFailure. Do not confuse completion with server readiness
  or repeatedly evaluate stale data.
- Both recorded outcomes enter TerminateVM → WaitForVMToTerminate (5 seconds) →
  GetTerminationState → IsVMTerminated. TERMINATING repeats a fresh lifecycle
  call; TERMINATED reaches EndResult, then JobSucceeded/JobFailed according to
  the recorded outcome. Unexpected states and termination/confirmation failures
  end in JobFailed with a cleanup-unconfirmed reason and retained known VM ID.
- Cleanup is that shared sequence, not extra compute or a separate operation.
  Additional output-shaping/choice/bookkeeping states are allowed where ASL
  requires them; success never follows an unconfirmed termination request.
- Preserve launch idempotency, scoped permissions, catches, result paths, the
  28-minute workflow timeout, 30-minute VM lifespan, normal idle policy, and
  token-safe final output/history restrictions. Timeout/cancellation still
  bypasses Catch cleanup; retain manual recovery and lifespan safeguards.
- Reconcile workflow requirements and test synthesized ASL, fresh polling,
  invalid inputs/responses, API failures, cleanup, and timeout paths. Run focused
  tests, `make verify`, and `make synth`; live deployment requires separate
  authorization and validation of the new image pin and permissions.

Deferred follow-ups:
- US-14: Consolidate generic checks and end-to-end outcome tests.

### US-14: Verify generic job outcomes with focused and end-to-end tests
Primary actor: Operator

Trigger: Worker and workflow follow-ups need a maintainable regression suite.

Happy-path action: Run the generic local verification suite and, under separate
authorization, the documented live end-to-end suite.

Visible outcome: Tests expose success, script failure, and publication failure
without smoke-task assumptions or removal of useful security/lifecycle coverage.

Authoritative state transition: Regression coverage changes from smoke-specific
and duplicate cases to focused generic checks plus explicit end-to-end outcomes.

Slice boundary: Test/fixture consolidation and repeatable end-to-end checks with
run guidance; one suite invocation reports the three required scenarios.
Source: todo item 7.

Acceptance criteria:
- Classify tests by protected behavior, not Gate 2 origin. Remove duplicates and
  smoke-task-specific cases; use repository-agnostic fixtures without restoring
  the calculator acceptance checks removed under US-12.
- Keep focused coverage for credential guards, authorization, lifecycle hooks,
  stable terminal status, termination, verification failures, and Git/PR
  consistency. Do not replace all focused tests with end-to-end checks.
- Cover successful setup/verification and a matching PR followed by confirmed
  termination; setup or verification failure followed by error and confirmed
  termination; and publication failure followed by error and confirmed termination.
  An uncertain publication may have remote effects; do not assume every such
  failure guarantees no PR.
- Keep simulated-service local checks credential-free. Live AWS/GitHub runs are
  explicit and separately authorized, with required credentials, costs, cleanup,
  and run commands recorded in the current slice note; ordinary `make verify`
  neither deploys nor creates resources.
- Run focused tests, `make verify`, and applicable image/synthesis checks;
  investigate coverage decreases without narrowing scope or lowering gates.
  Preserve the two-gate order; local simulations do not replace US-05/US-06's
  separately authorized real demonstrations.

Deferred follow-ups:
- US-05: Demonstrate controlled orchestration failure with cleanup.
- US-06: Independently verify automatic hard-lifespan termination.

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
