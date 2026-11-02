# AWS Lambda MicroVM Software Factory PoC Decisions

Status: Accepted v3; includes the native Python CDK Gate 2 reset and the operator-approved follow-ups in [SPEC.md](SPEC.md) (US-07–US-14), detailed in [docs/plans/todo.md](docs/plans/todo.md). Implementation and verification evidence are recorded only in [the current slice](docs/slices/current-slice.md).

The original demonstration requirements remain the inputs for US-01–US-04,
not generic worker constraints. As the follow-ups are delivered, the repository
script/manual-review rules below supersede fixed calculator validation, and the
10-second pending poll interval supersedes 30 seconds. Approval of this direction
is not proof of implementation and does not select runtime/dependency versions
or authorize deployment, live AWS/GitHub jobs, or destructive replacement beyond
the expressly approved scope.

## Problem Summary

Prove that an existing headless coding agent can implement a small, tested change and publish a GitHub PR inside an isolated AWS Lambda MicroVM, with AWS-hosted orchestration managing its lifecycle.

Source: [AWS Lambda MicroVM Software Factory PoC Plan](docs/plans/aws-lambda-microvm-software-factory-poc.md) and the operator-approved [follow-up plan](docs/plans/todo.md). Preserve the approved architecture and two-gate order. The decisions distinguish demonstration inputs from repository-agnostic worker requirements; they do not assert runtime feasibility.

## Goals and Success Criteria

- **Gate 1 — execution feasibility:** manually launch one VM using AWS CLI, complete the real coding task through agent-side commit, push, and PR creation, then manually terminate and confirm `TERMINATED`. Retain the VM ID and verification outcomes locally. Stop if execution, publication, or termination fails; do not create Gate 2 resources before this passes.
- **Gate 2 — orchestration:** use native Python CDK infrastructure and a newly validated image/worker under the [approved reset plan](docs/plans/gate2.md), rather than carrying the disposable Gate 1 implementation forward unchanged. A manually started Step Functions Standard execution launches exactly one VM for the task, obtains one authentication token, polls worker status, terminates, confirms `TERMINATED`, and finishes in `Succeed` without a running local controller.
- Generic automated success requires passing independent final `./scripts/verify` with actual exit code `0`, a nonempty committed change descended from the pinned baseline, a clean workspace, matching remote branch/head, and exactly one open, unmerged PR matching the configured target and verified commits. Codex owns commit, push, and PR creation inside the VM. Human review judges change correctness and test adequacy; automated success does not assert those judgments or verification-before-publication chronology.
- Demonstrate one controlled Gate 2 failure ending in `Fail`, with the cause retained in CloudWatch logs, no PR, and confirmed VM termination.
- Separately verify automatic hard-lifespan termination. Small negative-path tests cover other worker/workflow failures where practical; exhaustive live fault injection and observed repair of a failing test are not required.

## Primary Roles / Actors

- **Operator:** prepares inputs and scoped credentials, runs baseline tests, provisions resources, starts one job at a time, records gate evidence, and retains manual recovery access.
- **Codex:** owns the edit–test–fix loop and GitHub publication in one invocation inside the VM.
- **Lifecycle/worker server:** initializes one job, starts Codex asynchronously, streams available logs, and exposes pending or terminal status.
- **Step Functions:** manages the VM lifecycle and status polling, not the coding loop or publication.
- **Human reviewer:** inspects the PR and tests; any later merge is outside this demonstration.

## Non-Goals

- Production readiness, hostile or unapproved repositories, multi-agent operation, parallel jobs, load SLOs, or a durable task queue. Repository-agnostic workers support only configuration-authorized repositories, not unrestricted repository access.
- Custom reasoning loops, agent restarts, automatic whole-task reruns, or separate worker/polling deadlines.
- Local publication, Git bundles, downloaded workspace artifacts, or workspace recovery after termination.
- An inference proxy, model API keys, OpenAI Agents API integration, or changing the approved provider/model to bypass a feasibility failure.
- VPC, NAT Gateway, load balancer, database, webhook, polling Lambda, orchestration Lambda, general-purpose remote shell endpoint, or concurrency-lock service.

## Locked Decisions

### Product / Scope

The following fixture/task choices apply to the original US-01–US-04 demonstration,
not to hardcoded worker policy after US-11/US-12:

- Demonstration repository: [TeroTS/factory-smoke-test](https://github.com/TeroTS/factory-smoke-test), with PRs targeting `main`.
- Prepare a tiny dependency-free Python baseline: `calculator.py` exposes `add(a, b)` with passing standard-library `unittest` tests in `tests/test_calculator.py`.
- Agent task: add `multiply(a, b)` and tests for positive, negative, and zero operands, preserving addition behavior. Do not modify deployment configuration.
- Use the same pinned, tested baseline and task for both gates, with separate unique task branches and PRs. Leave both PRs unmerged for human review.
- Approve fixture preparation before the demonstration, agent-side task-branch publication during separately authorized runs, and sending synthetic repository code and relevant command/test output to the selected Bedrock model.

Approved follow-up direction:

- Make all code under `worker/` repository-agnostic. Remove smoke-repository names, calculator/file restrictions, fixed Python test commands, and demonstration-specific evidence assumptions. Use existing configuration conventions for repository authorization, PR target, and necessary repository-specific values; preserve the existing structured Job fields and one-job scope.
- Deliver follow-ups in SPEC order: maintained image runtime (US-07), selected boto3 calls (US-08), execution stages (US-09), Codex configuration (US-10), generic repository scripts (US-11), evidence removal/manual review (US-12), simplified CDK workflow (US-13), and generic/end-to-end tests (US-14), before the remaining US-05/US-06 demonstrations.

### UX / Workflow

- Manual AWS CLI launch and termination establish Gate 1. Only after Gate 1 passes, add Step Functions and its EventBridge connection for Gate 2.
- Gate 1 uses one lifecycle/worker and pinned image, not a separate feasibility runner. Rebuilt Gate 2 images/workers require fresh runtime-role/model/sandbox/tool validation; earlier image evidence is not transferable. Preserve execution, publication, and status guarantees except for the explicitly reduced automated evidence checks under US-11/US-12. Use a validated pinned Gate 2 image/worker for the remaining controlled-failure and hard-lifespan demonstrations.
- One Codex invocation owns the complete edit–test–fix and publication workflow. Test failures within that invocation feed its existing loop rather than immediately becoming terminal errors; setup/baseline verification failures before Codex and independent final verification failures remain terminal.
- Operator-approved simplification: do not stream, collect, or analyze Codex events in the worker, request `--json`, or infer sandbox-probe success from command events. Wait for the single process to exit, retain redacted stdout/stderr afterward, and propagate nonzero exit. Keep factory-managed sandbox policy, image/runtime sandbox validation, the prompt's initial shell check, independent final verification, and Git/PR checks. The worker does not independently prove the agent's initial shell check passed.
- The operator starts only one VM/job at a time. Independent Step Functions executions are not automatically serialized.

### Data / State / Ownership

- GitHub owns source, task commits, branches, and PRs. CloudWatch stores build/runtime logs. Secrets Manager stores only the GitHub token in the runtime-credentials secret.
- Restrict the runtime GitHub token to the authorized target repository with contents and pull-request write permissions. Repository authorization and secret selection are deployment-controlled, not arbitrary job-supplied secret references. Retrieve the token at runtime and configure Git using `gh auth setup-git`; do not silently broaden existing token or role permissions.
- Use execution-role credentials for Bedrock; never copy administrator credentials into the image or inject a model API key.
- Termination destroys the workspace. No failed-run workspace recovery or local artifact export is planned.

### Interfaces / Contracts

- Supply structured job data through `runHookPayload`, never an interpolated shell command. Keep it within the documented 16 KB limit and never include secret values.
- The job carries `repository`, `baseSha`, and `task`; preserve these field names and bounded structured input. The worker rejects unauthorized repositories before starting work. The PR target and required repository-specific values use existing configuration conventions, not new Job fields. Deployment supplies the validated pinned image/version, roles, connectors, logging, and credentials-secret ARN; CDK-created resource references remain supplied by the stack. VM ID and endpoint come dynamically from launch.
- Generic repository setup and verification use `./scripts/setup` and `./scripts/verify` in the target repository, not this factory's Make commands. Document this non-HTTP script boundary alongside the unchanged lifecycle/Job/status interfaces.
- `GET /status` returns HTTP 200 with exactly one of:
  - `{"status":"pending"}` while work is active.
  - `{"status":"ok"}` after independent final script verification and matching committed/remote Git and PR checks; human correctness review is separate.
  - `{"status":"error"}` for an unrecoverable failure.
  It returns no error cause, logs, diff, commit SHA, PR URL, or token-usage artifacts; error causes remain in CloudWatch logs. These response shapes supersede those in the source plan.
- Use port 8080 and one port-scoped JWE with a 30-minute expiration per execution. Reuse it; no token-refresh loop.
- Step Functions polls directly with HTTP Tasks and an EventBridge connection. The connection uses a static `X-Poc-Connection` marker ignored by the worker; it must not override `X-aws-proxy-auth` or `X-aws-proxy-port`.

### Operations / Deployment

- Factory development retains Python 3.14, `uv`, development-only Ruff and coverage.py, standard-library `unittest`, and Make commands. These do not replace target-repository scripts or establish MicroVM runtime/ARM64 compatibility.
- Upgrade the image to a maintained Python runtime. Evaluate Python 3.13 package availability on Amazon Linux 2023 ARM64 before selecting the version; 3.13 remains a candidate, not a locked version. Explicitly align Dockerfile interpreter selection, worker startup, subprocesses, compatibility settings, tests, and documentation. Validate a new image with `make verify` and `make preflight` before separately authorized deployment.
- Approve boto3 for worker STS identity and Secrets Manager access, plus botocore `Stubber` regression tests. Pin a compatible dependency version using repository conventions during the approved change; no version is chosen here. Retain AWS CLI availability and credential export/expiration checks initially. Verify the expected account/role before secret access and validate a nonempty token. Bound SDK requests and redact failures; use no private botocore expiration attributes or assumption that SDK refresh refreshes the separate Codex process.
- Split `execute()` into focused environment preparation, repository preparation, agent invocation, and completion verification functions, retaining coordination and final credential discovery. Preserve explicit inputs/returns, ordering, error handling, and existing helpers; introduce no class or generic workflow framework.
- Bake static Codex model/provider, sandbox/network, web-search, approval, and shell-environment policies into factory-owned image configuration. Keep invocation-specific CLI arguments, workspace/`.git` paths, and stdin prompt input in Python. Verify pinned-Codex loading/precedence before selecting a configuration location; cloned repository configuration must not override approved constraints. Retain explicit enforcement where needed and never bake secret values into configuration.
- Use `us-east-1`, the native Codex `amazon-bedrock-runtime` provider, and `us.openai.gpt-6.1-sol`. Set `AWS_REGION=us-east-1` and disable hosted web search. The inference profile can route within the United States, not only `us-east-1`.
- Gate 1 resources: one private S3 artifact bucket, separate image-build and runtime roles, one runtime-credentials secret containing only the GitHub token, one CloudWatch log group, and one reusable MicroVM image.
- Gate 2 defines all infrastructure through native Python CDK, without `CfnInclude`, importing the Gate 1 template, or adopting the old stack. Remove and recreate the old stack resources and separately managed image only after explicit operator authorization; preserve the existing GitHub-token secret and Gate 1 PR. Resolve CDK-managed image creation/update, version pinning, and bootstrap requirements before implementation or deletion; manual-only image provisioning is not a substitute.
- Gate 2 adds one Step Functions Standard state machine and execution role, and one EventBridge connection with its automatically managed Secrets Manager secret.
- Prefer higher-level `aws_cdk.aws_stepfunctions` constructs and supported `aws_cdk.aws_stepfunctions_tasks` integrations over hand-built ASL dictionaries and `CfnStateMachine`. Verify pinned-CDK MicroVM/HTTP support; use `CustomState` only where required. Do not add compute or upgrade dependencies solely to avoid raw integration states. Preserve scoped permissions, catches, result paths, timeouts, and token-safe output; validate synthesized ASL and transitions.
- Build on `public.ecr.aws/lambda/microvms:al2023-minimal` and managed base `arn:aws:lambda:us-east-1:aws:microvm-image:al2023-1`, using Linux ARM64 tools and a pinned Codex executable supporting the selected native provider.
- Image sizing: 2 GB / 1 vCPU baseline, bursting up to 8 GB / 4 vCPU, with 8 GB maximum disk. Revisit only if measurements justify it.
- Normal bounds: 28-minute overall workflow timeout and 30-minute VM maximum lifespan. Idle policy: `maxIdleDurationSeconds: 3600`, `suspendedDurationSeconds: 0`, `autoResumeEnabled: false`.
- After US-13, wait 10 seconds between pending worker status responses and 5 seconds before each repeated startup/termination lifecycle check. Each wait returns to a fresh API call, not a decision over stale data. Limit each HTTP Task request to 10 seconds. These are poll/request settings, not additional job-level deadlines; the original US-04 demonstration used a 30-second pending interval.

## Core Business Rules

- Worker success requires the independent final script and Git/PR checks specified above. Workflow success additionally requires confirmed VM termination. A successful Codex process exit, HTTP 200, or termination request alone is insufficient.
- The Job's pinned `baseSha` establishes task-commit ancestry, not a frozen PR target head. The configured target branch may advance during a Job; do not require PR `baseRefOid` to equal `baseSha`. Retain target-branch identity, matching local/remote/PR task heads, and all other completion checks. Worker success does not prove compatibility with the current target branch; that remains a merge-time check, with no merge-policy change or deployment authorized by this decision.
- Instruct Codex to run passing final `./scripts/verify` before commit, push, and `gh pr create`. It must not push directly to the configured target branch, merge PRs, or weaken tests to manufacture success. After removal of `verify_events()`, the worker does not prove that chronology from command events; later independent verification and manual GitHub diff review cannot establish it.
- Remove `preserve_nodes()`, `verify_source()`, `verify_events()`, and unused `command_words()` plus their callers/dedicated tests. Replace `TEST_COMMAND` with `./scripts/verify`, move `verify_pr()` into `worker/task.py` with generic consistency checks and regression tests, then delete `worker/evidence.py`.
- Delete `image/acceptance.py` and its worker path constant/invocation, image/build references, documentation, and calculator-acceptance tests. Do not relocate or retain those checks elsewhere. Human GitHub review judges correctness and test adequacy; no automated baseline AST-preservation or calculator operand-coverage gate remains. Passing verification does not prove tests were not weakened.
- On publication timeouts, the same agent checks for an existing branch/PR before retrying to avoid duplicates. Neither the worker nor workflow restarts Codex or reruns the job automatically.
- Gate 1 must verify actual selected-model access through the runtime role, credential discovery/refresh, unattended harmless shell-tool execution, and sandbox compatibility before accepting the coding/publication result. Stop rather than silently changing model/provider or disabling the sandbox.

## UX / Workflow Rules

- Lifecycle hooks use AWS's full `/aws/lambda-microvms/runtime/v1/` paths on port 8080:
  - `ready`: server readiness, with no agent started.
  - `validate`: executable availability, writable workspace, and basic process execution without credential-dependent operations.
  - `run`: validate job configuration, initialize fresh state, spawn the worker asynchronously, and return promptly within the run-hook limit. Cloning and model calls happen in the worker.
  - `terminate`: stop the worker/process group and flush available logs.
- Follow US-13's state plan in [SPEC.md](SPEC.md#us-13-run-a-job-through-a-simplified-cdk-lifecycle-workflow) and [todo item 8](docs/plans/todo.md#8-simplify-the-vm-lifecycle-state-plan): retain input → launch → check VM ID/endpoint → wait/get until `RUNNING` → create/check token once → fresh HTTP status/choice/wait → record outcome → shared termination/wait/get → token-free result → `Succeed` or `Fail`. API calls and decisions remain separate; necessary bookkeeping states do not justify extra compute.
- Remove per-execution `InspectImage`, `ImageReady`, `InspectVersion`, and `VersionReady` checks. Build/deployment owns image validation and explicit version pinning; launch still catches an unavailable or invalid version rather than assuming continued availability.
- Use the execution name as the launch client token. Retry an uncertain `StartExecution` with the same name and input rather than creating a new job.
- Catchable post-launch failures with a known VM ID record failure and enter the same termination sequence as success. Missing endpoint or token, unexpected lifecycle states, invalid/missing responses, API/HTTP failures, and worker errors never become success or loop indefinitely. Pending worker status waits and polls again; it means unfinished work, not server readiness. No known VM ID means direct failure.
- Cleanup is the shared termination sequence, not a separate state or operation. Require a fresh service-confirmed `TERMINATED`; repeat only for `TERMINATING`. Termination/confirmation failures or unexpected states end in failure with a cleanup-unconfirmed reason and a retained known VM ID for recovery. Final output omits the token; it does not erase earlier execution history.
- Check VM readiness before polling; do not call `getMicrovm` on every status poll. Use later lifecycle checks for failure diagnosis and termination confirmation.
- A failed launch with no known VM ID ends in `Fail`; an ambiguously launched VM remains bounded by its lifespan. Report cleanup failures instead of claiming confirmed termination.

## Data / State / Ownership Decisions

- The original README-only demonstration commit `92a9c6f062c65d9fa1765005ae60627fb7e5ae31` is not the tested demonstration baseline. Do not use it as a substitute or invent a baseline SHA.
- After preparing the approved fixture, run baseline tests and record the resulting commit SHA, baseline outcome, and expected acceptance test before either gate. Reuse that SHA without merging either demonstration PR.
- Retain VM IDs and gate outcomes locally for evidence and recovery; logs remain in CloudWatch and published work remains in GitHub.
- No credentials, task identifiers, live external connections, or generated per-job state enter the image snapshot. Initialize runtime state after launch.
- Exclude secrets from prompts, application logs, and job input. The JWE necessarily passes through execution state: restrict execution-history access and omit it from final workflow output and application logs.
- Repository code and setup/verification scripts can exercise the runtime role and access the GitHub token; sandboxing does not remove this exposure. The original acceptance covered the controlled synthetic demonstration. Before enabling another configuration-authorized repository, review this credential-access trade-off and reconcile its authorization/scoping requirements. This follow-up does not authorize running arbitrary code with existing credentials or sending unapproved sensitive repository content to the model.

## Interface / Contract Expectations

- The original demonstration fixture is dependency-free and uses `python3 -m unittest discover -s tests -v`; those choices do not constrain generic target-repository setup or test tooling.
- For generic jobs, run executable `./scripts/setup` and baseline `./scripts/verify` from the pinned target repository before Codex. Missing, non-executable, or failing scripts report terminal error explicitly. Instruct the same agent to run final verification before publication, then independently run `./scripts/verify` afterward and retain actual exit codes with sanitized output.
- Setup/test behavior belongs to the repository scripts; the worker does not hardcode a framework, calculator acceptance task, or source-preservation rule. Human review inspects the GitHub diff and test adequacy without an automatic merge or a correctness approval field in `/status`.
- Start with Codex's documented `workspace-write` sandbox and enable the outbound network access needed for GitHub operations. Unattended execution and tool compatibility are Gate 1 checks, not assumed capabilities.
- Managed ingress and internet-egress connectors provide networking. Status requests carry dynamic `X-aws-proxy-auth` and `X-aws-proxy-port: 8080` headers.
- Terminal status is stable once set. Termination may interrupt work before any terminal response exists; missing status is never success.

## Operational / Deployment Constraints

- Preserve the source plan's scoped permission matrix and trust policies. Defer orchestration permissions until Gate 2. Scope runtime secret access to the GitHub-token secret, logs to the designated group, and Bedrock access to the selected profile, destination models/Regions, and default project. Local administrator access does not establish runtime-role access.
- Use the supplied provisioning/operator profile `AWSAdministratorAccess-172318459814`; never transfer its credentials into the image. Retain manual termination and log-inspection access.
- The workflow and VM clocks start at different times. The 28/30-minute bounds do not guarantee a two-minute cleanup margin. Workflow timeout and manual `StopExecution` do not run `Catch` cleanup or stop the worker; the VM lifespan and manual recovery are the fallback.
- Controlled Gate 2 failure: submit an otherwise valid job with a SHA confirmed absent from the repository. Checkout fails before Codex starts, returns `{"status":"error"}`, creates no PR, and must lead to confirmed termination and workflow `Fail`, with the cause retained in CloudWatch logs.
- Hard-lifespan check: launch the pinned Gate 2 image with a two-minute maximum lifespan and the non-publishing nonexistent-base-commit job. Keep idle suspension at one hour. Do not explicitly terminate; confirm automatic `TERMINATED`. No Codex/model call is needed. Record failure and terminate manually if automatic termination fails.
- Validate the state-machine definition and inspect roles before deployment. Gate 2's successful live task must prove authenticated HTTP access and token reuse across polls.
- Classify tests by protected behavior, not Gate 2 origin. Remove smoke-task-specific cases and duplicates; retain focused coverage for credentials, repository authorization, setup/process/verification failures, lifecycle/status behavior, Git/PR consistency, HTTP failures, cleanup, and timeout/persistent-pending paths. Replace repository-specific fixtures where needed, not the entire worker test suite.
- Add a small end-to-end suite for successful setup/verification and matching PR with confirmed termination, setup or verification failure with error and confirmed termination, and publication failure with error and confirmed termination. Uncertain publication can have remote effects; only the controlled checkout failure requires no PR.
- Keep local simulated-service checks credential-free. Document explicit live suite commands, credentials, costs, and cleanup; ordinary `make verify` must not deploy or create live resources. Run focused tests, `make verify`, and applicable preflight/synthesis checks; investigate coverage decreases without narrowing scope or lowering gates. Live AWS/GitHub tests and deployment require separate operator authorization; local suites do not replace US-05/US-06 demonstrations or authorize destructive changes.
- Defer exhaustive live credential, authentication, network, stopped-execution, unresolved-test-failure, and terminal-closure demonstrations, not required security or failure handling.

## Assumptions

- The operator supplies approved repository inputs and repository-scoped GitHub access through Secrets Manager, never chat or execution input. Original demonstration inputs remain distinct from generic worker configuration.
- Tested baseline SHAs, pinned CLI/image versions, resource identifiers, and gate evidence are preparation/deployment outputs recorded only in the current slice, not inferred from documentation or invented here.
- Documentation and prior CLI/definition checks support the architecture but do not prove live image creation, GPT-6.1 Sol inference, runtime-role credential refresh, sandbox compatibility, publication, or end-to-end orchestration. The gates establish these facts.
- The demonstration has no sensitive application data. The operator maintains single-job discipline and manually recovers resources when necessary.

## Open Questions

- US-07: verify maintained interpreter availability on Amazon Linux 2023 ARM64 and select a compatible version; Python 3.13 is only a candidate.
- US-08: choose/pin compatible boto3 dependencies and verify real MicroVM credential-provider behavior without assuming cross-process refresh.
- US-10: verify pinned-Codex configuration loading/precedence before selecting the factory configuration location and enforcement approach.
- US-11: reconcile repository authorization, PR-target configuration, and script credential exposure with existing conventions before enabling another repository. Do not invent new Job fields or silently broaden credential scope.
- US-13: verify pinned-CDK MicroVM/HTTP task support and synthesized semantics; raw integration states are permitted where needed, not new compute or unapproved dependency upgrades. New native-image provisioning/bootstrap changes still require review; a custom provider or manual-only substitute is not implicitly authorized.

## Ready for Spec Generation

- Status: yes
- Reason: The original gate direction and approved US-07–US-14 follow-ups are reconciled here. SPEC.md defines the backlog; targeted contract updates can capture generic authorization, script execution, and reduced automated completion guarantees while retaining Job fields and lifecycle/status shapes. Preserve open compatibility/configuration questions, original demonstration boundaries, and live authorization requirements; do not invent version pins, configuration APIs, baseline SHAs, or implementation evidence.
