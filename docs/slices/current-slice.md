# Current Slice

Status: done

## Slice Goal
- Run a Job through a simpler CDK lifecycle workflow with fresh observations and shared confirmed termination.

## Slice Boundary
- Operator starts one Standard execution using a validated pinned image; fresh worker/lifecycle observations move execution to Succeed only after worker ok and confirmed TERMINATED, or Fail with a retained recovery ID and explicit unconfirmed-cleanup reason.

## Stories In Scope
- `US-13 Run a job through a simplified CDK lifecycle workflow`

## Stories Completed In This Slice
- `US-13 Run a job through a simplified CDK lifecycle workflow`

## Stories Remaining In This Slice
- None.

## Contract Inputs
- [openapi.yaml](../../openapi.yaml): unchanged Job, lifecycle, authentication, and exact status responses.
- [docs/contracts.md](../contracts.md): AWS StartExecution idempotency, dynamic launch ID/endpoint, and repository-script completion boundary.

## Decision Inputs
- [DECISIONS.md](../../DECISIONS.md): high-level CDK preference, deployment-owned pin validation, shared termination, scoped permissions, token-safe output, 28/30-minute bounds, and 5/10-second polling.
- [todo item 8](../plans/todo.md#8-simplify-the-vm-lifecycle-state-plan): explicit state sequence and allowed ASL bookkeeping.

## Shared State Required Now
- Deployment-controlled image/version, runtime role, connection, connectors and logs; structured Job, dynamic VM ID/endpoint, one port-scoped token, fresh responses, recorded outcome, and confirmed termination.
- US-12 is complete locally; previous evidence remains in git history. Deployed resources, active image pin, secrets, permissions, and PRs are unchanged.

## Operations / Endpoints / Surfaces In This Slice
- `infra/workflow.py`: high-level connected states, supported SDK integrations, bounded dynamic HTTP polling, and shared cleanup.
- `infra/stacks.py`: high-level Standard StateMachine and scoped integration permissions.
- `tests/test_workflow.py`, `tests/test_infra.py`: synthesized ASL and simulated fresh transitions/failure paths.

## Tests Required
- Test-first state-plan, fresh startup/status/termination polling, token reuse, invalid launch/token/status responses, API/HTTP failures, known-ID cleanup, unconfirmed termination, token-free terminal outputs, and persistent-pending timeout checks.
- Focused workflow/infrastructure tests, `make verify`, `make synth`, whitespace and affected local links/formatting; investigate coverage changes without narrowing scope.

## Not Now
- US-14 suite consolidation and separately authorized US-05/US-06 live demonstrations.
- Deployment, live Jobs, image build/activation, new credentials/repository scope, dependencies, compute, Job fields, README.md, AGENTS.md, or DECISIONS.md changes.

## Done When
- Per-execution image inspections are removed and supported high-level states/integrations implement the approved fresh-polling/shared-termination plan.
- Success requires worker ok and fresh confirmed TERMINATED; catchable known-ID failures share termination, cleanup failures retain the ID and explicitly report unconfirmed cleanup, and terminal outputs exclude tokens.
- Scoped permissions, launch idempotency, request limits, normal VM/idle bounds, and overall timeout remain intact; required local checks and live-verification gaps are recorded here.

## Completion Summary
- US-13 is complete locally. `infra/workflow.py` now connects high-level Pass, Choice, Wait, Succeed and Fail constructs; `infra/stacks.py` owns a high-level Standard StateMachine with the existing 28-minute timeout. The synthesized definition has 27 states, no per-execution image/version inspection, and one launch using the execution name as client token. Deployment still requires an explicit image-version parameter without a default; image validation and new-source/version checks remain deployment responsibilities.
- Verified pinned CDK 2.275.0 constructor APIs and a credential-free `CallAwsService` probe; the five MicroVM SDK tasks synthesize the existing `lambdamicrovms` integration resources using explicit `lambda:` IAM actions scoped to the image ARN. Reviewed pinned CDK HTTP implementation and CDK documentation through the IaC MCP search. `HttpInvoke` generates wildcard state-machine resource permission without the existing GET condition and couples its static `apiRoot` to the request URL; only `PollWorker` uses CustomState to retain dynamic launch-derived HTTPS endpoints and the existing narrow HTTP/connection/secret grants. No dependencies, compute, custom providers, or extra permissions were added; obsolete image-inspection permissions were removed from the synthesized execution policy.
- Startup and termination wait five seconds before fresh lifecycle calls; only PENDING/TERMINATING respectively repeat. Pending worker status waits ten seconds then calls HTTP again, never rereading stale response data or checking lifecycle on each worker poll. One nonempty port-8080 token is requested with 30-minute expiration and reused. HTTP requests retain their ten-second timeout and exact status-body checks. Worker ok and all catchable known-ID failures record outcomes and share one termination sequence. Missing ID fails directly; missing endpoint/token, malformed status, worker error, and API/HTTP failures cannot succeed.
- Confirmed termination shapes the existing successful output (`microvmId`, `status`, `termination`) before JobSucceeded. JobFailed now receives sanitized bookkeeping output with explicit JobFailed or CleanupUnconfirmed error/cause; known IDs survive, unconfirmed cleanup never claims TERMINATED, and both failed and successful terminal state inputs exclude the token and raw service causes. Earlier execution history still contains the JWE and must remain access-restricted. Normal VM lifespan/idle configuration is unchanged. Workflow timeout/cancellation still bypasses Catch cleanup; lifespan/manual recovery remain mandatory.
- Preserved CloudFormation logical ID `Workflow` when switching to StateMachine, preventing this construct refactor from proposing workflow replacement solely due to a generated ID. CDK automatically retains role/policy dependencies. Initial regression checks failed on the old image inspections, CfnStateMachine, missing sanitized recovery output, absent recorded failure reason, and changed logical ID; the final implementation passes those checks.
- Focused `uv run --locked python -m unittest discover -s tests -p test_workflow.py -v` (nine) and `-p test_infra.py -v` (eight) passed. Workflow tests execute the actual stack-synthesized ASL with simulated service responses from launch through fresh readiness/status/termination observations, checking one launch/token, reused headers, strict invalid-response handling, catch routing, retained IDs, token-free terminal state, and persistent startup/worker/termination waits reaching overall timeout. These are local transition simulations, not a real Step Functions interpreter, MicroVM, or end-to-end live Job; malformed Job validation remains worker-owned and is covered by the retained worker/server tests.
- Final `make verify` passed formatting, lint, coverage/tests and logging: 58 discovered, 55 passed, three Docker-only host skips. Three tests were added relative to US-12; no focused worker/security/lifecycle tests were removed. Overall coverage remains 95%; `infra/workflow.py` and `infra/stacks.py` are 100%, and worker/tool coverage is unchanged. Coverage source scope and branch/missing-line reporting were not narrowed. CDK construct execution coverage is not proof of remote state-transition coverage. Early formatting/import checks were corrected only in affected files using targeted formatting/import edits.
- `make synth` passed without AWS lookups or deployment; generated ASL and role/policy dependencies were inspected. Existing Node 25.4.0/jsii compatibility and CDK feature-flag notices remain; Node 22/24 is recommended. `git diff --check` and current-slice local-link/heading/fence checks passed. The unchanged HTTP and submission contracts were reviewed; this story does not change their request/response schemas.
- `make preflight` was not rerun because no worker, image, managed configuration, runtime dependencies, source staging, or sandbox behavior changed; run `make preflight` before any newly authorized image deployment. No dedicated semantic ASL validator, type-check, or CI command is configured, and no AWS validation API was called. No deployment, live Job, model/GitHub call, image activation, credentials, secret scopes, or existing resources/PRs were changed. Local synthesis does not establish live integration support, authentication, scoped role/model access, or real termination. Deployment/live checks require separate authorization, reviewed permissions, a newly validated explicit image pin, retained VM ID, restricted history access, and confirmed cleanup.

## Next Slice Recommendation
- `US-14 Verify generic job outcomes with focused and end-to-end tests`: consolidate duplicates without removing security/lifecycle coverage, and cover successful publication, script failure, and publication failure through confirmed termination.
- Defer separately authorized US-05/US-06 live demonstrations until the follow-ups are complete; local simulations do not replace either gate.
