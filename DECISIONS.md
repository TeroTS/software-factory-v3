# AWS Lambda MicroVM Software Factory PoC Decisions

Status: Accepted v2; includes the operator-approved native Python CDK Gate 2 reset. Implementation and verification evidence are recorded only in [the current slice](docs/slices/current-slice.md).

## Problem Summary

Prove that an existing headless coding agent can implement a small, tested change and publish a GitHub PR inside an isolated AWS Lambda MicroVM, with AWS-hosted orchestration managing its lifecycle.

Source: [AWS Lambda MicroVM Software Factory PoC Plan](docs/plans/aws-lambda-microvm-software-factory-poc.md). Preserve its approved architecture and two gates. The decisions below accept the remaining proposals and fix demonstration inputs; they do not assert runtime feasibility.

## Goals and Success Criteria

- **Gate 1 — execution feasibility:** manually launch one VM using AWS CLI, complete the real coding task through agent-side commit, push, and PR creation, then manually terminate and confirm `TERMINATED`. Retain the VM ID and verification outcomes locally. Stop if execution, publication, or termination fails; do not create Gate 2 resources before this passes.
- **Gate 2 — orchestration:** use native Python CDK infrastructure and a newly validated image/worker under the [approved reset plan](docs/plans/gate2.md), rather than carrying the disposable Gate 1 implementation forward unchanged. A manually started Step Functions Standard execution launches exactly one VM for the task, obtains one authentication token, polls worker status, terminates, confirms `TERMINATED`, and finishes in `Succeed` without a running local controller.
- Each successful task must make a nonempty, meaningful source/test change, record the passing test command and actual exit code, and create a commit descended from the pinned baseline. Codex must push the unique task branch and create one PR inside the VM; neither PR is merged by the agent.
- Demonstrate one controlled Gate 2 failure ending in `Fail`, with the cause retained in CloudWatch logs, no PR, and confirmed VM termination.
- Separately verify automatic hard-lifespan termination. Small negative-path tests cover other worker/workflow failures where practical; exhaustive live fault injection and observed repair of a failing test are not required.

## Primary Roles / Actors

- **Operator:** prepares inputs and scoped credentials, runs baseline tests, provisions resources, starts one job at a time, records gate evidence, and retains manual recovery access.
- **Codex:** owns the edit–test–fix loop and GitHub publication in one invocation inside the VM.
- **Lifecycle/worker server:** initializes one job, starts Codex asynchronously, streams available logs, and exposes pending or terminal status.
- **Step Functions:** manages the VM lifecycle and status polling, not the coding loop or publication.
- **Human reviewer:** inspects the PR and tests; any later merge is outside this demonstration.

## Non-Goals

- Production readiness, arbitrary hostile repositories, multi-agent operation, parallel jobs, load SLOs, or a durable task queue.
- Custom reasoning loops, agent restarts, automatic whole-task reruns, or separate worker/polling deadlines.
- Local publication, Git bundles, downloaded workspace artifacts, or workspace recovery after termination.
- An inference proxy, model API keys, OpenAI Agents API integration, or changing the approved provider/model to bypass a feasibility failure.
- VPC, NAT Gateway, load balancer, database, webhook, polling Lambda, orchestration Lambda, general-purpose remote shell endpoint, or concurrency-lock service.

## Locked Decisions

### Product / Scope

- Demonstration repository: [TeroTS/factory-smoke-test](https://github.com/TeroTS/factory-smoke-test), with PRs targeting `main`.
- Prepare a tiny dependency-free Python baseline: `calculator.py` exposes `add(a, b)` with passing standard-library `unittest` tests in `tests/test_calculator.py`.
- Agent task: add `multiply(a, b)` and tests for positive, negative, and zero operands, preserving addition behavior. Do not modify deployment configuration.
- Use the same pinned, tested baseline and task for both gates, with separate unique task branches and PRs. Leave both PRs unmerged for human review.
- Approve fixture preparation before the demonstration, agent-side task-branch pushes and PR creation, and sending synthetic repository code and relevant command/test output to the selected Bedrock model. No GitHub writes or model calls were performed during this interview.

### UX / Workflow

- Manual AWS CLI launch and termination establish Gate 1. Only after Gate 1 passes, add Step Functions and its EventBridge connection for Gate 2.
- Gate 1 uses one lifecycle/worker and pinned image, not a separate feasibility runner. Gate 2 may replace that disposable implementation, but must revalidate the new image/worker against the same execution, publication, sandbox, and status requirements. Use the Gate 2 pinned image/worker for its success, controlled-failure, and hard-lifespan demonstrations.
- One Codex invocation owns the complete edit–test–fix and publication workflow. Test failures feed its existing loop rather than immediately becoming terminal errors.
- The operator starts only one VM/job at a time. Independent Step Functions executions are not automatically serialized.

### Data / State / Ownership

- GitHub owns source, task commits, branches, and PRs. CloudWatch stores build/runtime logs. Secrets Manager stores only the GitHub token in the runtime-credentials secret.
- Restrict the GitHub token to the demonstration repository with contents and pull-request write permissions. Retrieve it at runtime and configure Git using `gh auth setup-git`.
- Use execution-role credentials for Bedrock; never copy administrator credentials into the image or inject a model API key.
- Termination destroys the workspace. No failed-run workspace recovery or local artifact export is planned.

### Interfaces / Contracts

- Supply structured job data through `runHookPayload`, never an interpolated shell command. Keep it within the documented 16 KB limit and never include secret values.
- The job carries the repository, pinned base SHA, and task. The fixed demonstration uses `main` as the PR base and the approved test command below. Deployment supplies the pinned image, roles, connectors, and credentials-secret ARN.
- `GET /status` returns HTTP 200 with exactly one of:
  - `{"status":"pending"}` while work is active.
  - `{"status":"ok"}` after verified task completion and PR creation.
  - `{"status":"error"}` for an unrecoverable failure.
  It returns no error cause, logs, diff, commit SHA, PR URL, or token-usage artifacts; error causes remain in CloudWatch logs. These response shapes supersede those in the source plan.
- Use port 8080 and one port-scoped JWE with a 30-minute expiration per execution. Reuse it; no token-refresh loop.
- Step Functions polls directly with HTTP Tasks and an EventBridge connection. The connection uses a static `X-Poc-Connection` marker ignored by the worker; it must not override `X-aws-proxy-auth` or `X-aws-proxy-port`.

### Operations / Deployment

- Factory development uses Python 3.14, `uv`, development-only Ruff and coverage.py dependencies, standard-library `unittest`, and Make commands. This does not add dependencies to the demonstration repository or establish the MicroVM's Python version/ARM64 compatibility; those remain Gate 1 checks.
- Use `us-east-1`, the native Codex `amazon-bedrock-runtime` provider, and `us.openai.gpt-6.1-sol`. Set `AWS_REGION=us-east-1` and disable hosted web search. The inference profile can route within the United States, not only `us-east-1`.
- Gate 1 resources: one private S3 artifact bucket, separate image-build and runtime roles, one runtime-credentials secret containing only the GitHub token, one CloudWatch log group, and one reusable MicroVM image.
- Gate 2 defines all infrastructure through native Python CDK, without `CfnInclude`, importing the Gate 1 template, or adopting the old stack. Remove and recreate the old stack resources and separately managed image only after explicit operator authorization; preserve the existing GitHub-token secret and Gate 1 PR. Resolve CDK-managed image creation/update, version pinning, and bootstrap requirements before implementation or deletion; manual-only image provisioning is not a substitute.
- Gate 2 adds one Step Functions Standard state machine and execution role, and one EventBridge connection with its automatically managed Secrets Manager secret.
- Build on `public.ecr.aws/lambda/microvms:al2023-minimal` and managed base `arn:aws:lambda:us-east-1:aws:microvm-image:al2023-1`, using Linux ARM64 tools and a pinned Codex executable supporting the selected native provider.
- Image sizing: 2 GB / 1 vCPU baseline, bursting up to 8 GB / 4 vCPU, with 8 GB maximum disk. Revisit only if measurements justify it.
- Normal bounds: 28-minute overall workflow timeout and 30-minute VM maximum lifespan. Idle policy: `maxIdleDurationSeconds: 3600`, `suspendedDurationSeconds: 0`, `autoResumeEnabled: false`.
- Wait 30 seconds between pending status responses; limit each HTTP Task request to 10 seconds. These are poll/request settings, not additional job-level deadlines.

## Core Business Rules

- Success requires passing approved tests, a nonempty change, and confirmed PR creation. A successful Codex process exit or HTTP 200 alone is insufficient.
- Codex commits, pushes, and runs `gh pr create` only after passing final tests. It must not push directly to `main`, merge PRs, or weaken tests to manufacture success.
- On publication timeouts, the same agent checks for an existing branch/PR before retrying to avoid duplicates. Neither the worker nor workflow restarts Codex or reruns the job automatically.
- Gate 1 must verify actual selected-model access through the runtime role, credential discovery/refresh, unattended harmless shell-tool execution, and sandbox compatibility before accepting the coding/publication result. Stop rather than silently changing model/provider or disabling the sandbox.

## UX / Workflow Rules

- Lifecycle hooks use AWS's full `/aws/lambda-microvms/runtime/v1/` paths on port 8080:
  - `ready`: server readiness, with no agent started.
  - `validate`: executable availability, writable workspace, and basic process execution without credential-dependent operations.
  - `run`: validate job configuration, initialize fresh state, spawn the worker asynchronously, and return promptly within the run-hook limit. Cloning and model calls happen in the worker.
  - `terminate`: stop the worker/process group and flush available logs.
- Gate 2 follows launch → wait/get until `RUNNING` → create token once → HTTP status/choice/wait → terminate → wait/get until `TERMINATED` → `Succeed` or `Fail`.
- Use the execution name as the launch client token. Retry an uncertain `StartExecution` with the same name and input rather than creating a new job.
- Catchable post-launch failures use shared cleanup states and retain the VM ID. Unexpected termination, invalid/missing responses, HTTP failures, and worker failures must not become success.
- Check VM readiness before polling; do not call `getMicrovm` on every status poll. Use later lifecycle checks for failure diagnosis and termination confirmation.
- A failed launch with no known VM ID ends in `Fail`; an ambiguously launched VM remains bounded by its lifespan. Report cleanup failures instead of claiming confirmed termination.

## Data / State / Ownership Decisions

- Current repository inspection found public `main` at `92a9c6f062c65d9fa1765005ae60627fb7e5ae31` containing only a README. **This is not the demonstration baseline.**
- After preparing the approved fixture, run baseline tests and record the resulting commit SHA, baseline outcome, and expected acceptance test before either gate. Reuse that SHA without merging either demonstration PR.
- Retain VM IDs and gate outcomes locally for evidence and recovery; logs remain in CloudWatch and published work remains in GitHub.
- No credentials, task identifiers, live external connections, or generated per-job state enter the image snapshot. Initialize runtime state after launch.
- Exclude secrets from prompts, application logs, and job input. The JWE necessarily passes through execution state: restrict execution-history access and omit it from final workflow output and application logs.
- Repository code inside the VM can exercise the runtime role and access the GitHub token. This is an accepted trade-off only for the controlled synthetic demonstration repository.

## Interface / Contract Expectations

- Repository setup requires no third-party dependencies or package installation. Run tests from the repository root with `python3 -m unittest discover -s tests -v`.
- Acceptance tests cover multiplication of positive and negative values and zero, while existing addition tests continue to pass. Record the actual command and exit code and inspect the diff for weakened tests.
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
- Small tests should cover worker setup/process failures, invalid/missing terminal responses, HTTP failures, and cleanup routing where practical. Verify the overall timeout configuration and simulate persistent pending/timeout behavior where practical. Defer exhaustive live credential, authentication, network, stopped-execution, unresolved-test-failure, and terminal-closure demonstrations.

## Assumptions

- The operator will prepare the approved synthetic fixture and provide a repository-scoped GitHub token through Secrets Manager during implementation, not through chat or execution input.
- The tested baseline SHA, pinned CLI/image version, resource identifiers, and gate evidence are preparation/deployment outputs still to be recorded, not unresolved product policies.
- Documentation and prior CLI/definition checks support the architecture but do not prove live image creation, GPT-6.1 Sol inference, runtime-role credential refresh, sandbox compatibility, publication, or end-to-end orchestration. The gates establish these facts.
- The demonstration has no sensitive application data. The operator maintains single-job discipline and manually recovers resources when necessary.

## Open Questions

- Gate 2 CDK image-provisioning support and bootstrap requirements must be resolved during US-04 preparation. This is an implementation-design question, not authorization to add a custom-resource provider or substitute manual-only provisioning; seek approval if a new dependency or architecture change is necessary.

## Ready for Spec Generation

- Status: yes
- Reason: The approved direction, gate order, remaining proposals, demonstration task, publication/data permissions, status contract, limits, and verification scope are accepted. `$decision-to-spec` can proceed without inventing policy. It must preserve the outstanding preparation steps and runtime verification gaps rather than inventing a baseline SHA or claiming feasibility is already proven.
