# Current Slice

Status: done

## Slice Goal
- Verify generic Job outcomes through worker execution, status polling, and confirmed cleanup while retaining focused security checks.

## Slice Boundary
- Operator runs the credential-free suite covering successful publication, script failure, and publication failure; each simulated execution ends in its correct Succeed/Fail outcome only after confirmed simulated termination. Separately authorized live runs use the same public Job and lifecycle interfaces.

## Stories In Scope
- `US-14 Verify generic job outcomes with focused and end-to-end tests`

## Stories Completed In This Slice
- `US-14 Verify generic job outcomes with focused and end-to-end tests` (local implementation; live checks require separate authorization).

## Stories Remaining In This Slice
- None.

## Contract Inputs
- [openapi.yaml](../../openapi.yaml): Job, lifecycle hooks and exact status responses.
- [docs/contracts.md](../contracts.md): repository scripts, publication checks, authorization and StartExecution idempotency.

## Decision Inputs
- [DECISIONS.md](../../DECISIONS.md): generic outcome suite, focused security coverage, separately authorized live runs, two-gate order and recovery safeguards.

## Shared State Required Now
- Existing configuration-authorized generic repository fixture, pinned baseline, script exits, one agent invocation, matching Git/PR state, stable worker status and retained VM ID through confirmed cleanup.
- Existing deployed resources/image pins and credentials are unchanged; local simulations cannot validate them.

## Operations / Endpoints / Surfaces In This Slice
- `tests/`: shared generic worker/workflow fixtures, focused regressions and joined outcome checks.
- Existing `Makefile` commands discover the new suite automatically; image checks remain unchanged.
- This note: test classification, local evidence and separately authorized live-suite commands.

## Tests Required
- One focused suite invocation covers success, setup/baseline/final verification failure, and publication failure through lifecycle/status and synthesized workflow cleanup.
- Retain credential, authorization, sandbox, stable status, termination, Git/PR consistency, invalid-response/API failure and timeout tests.
- Focused tests, `make verify`, `make preflight`, `make synth`, whitespace and affected documentation links; investigate coverage changes without narrowing scope.

## Not Now
- US-05/US-06 live demonstrations; deployment, real Jobs, model calls, GitHub publication, new dependencies or broader credential/repository access.
- README.md, AGENTS.md, DECISIONS.md and production worker/workflow changes unless required by a proven regression.

## Done When
- Generic fixtures replace remaining smoke-task assumptions, actual duplicates are removed without losing protected behavior, and repeatable outcome checks join worker results to confirmed simulated cleanup.
- Live commands, prerequisites, costs and manual recovery are explicit, with no live actions in ordinary verification and no claim that simulations establish gate acceptance.

## Completion Summary
- Shared `WorkerFixture` and `WorkflowFixture` reuse the existing generic repository, botocore Stubber responses, agent/publication simulation, and actual stack-synthesized ASL. `tests/worker_process.py` runs the real worker `main()` in a child process with a credential-free environment, local Git origin and simulated external services. It is test support, not a production runner or failure API. No worker, infrastructure, image policy, dependency, contract, or public interface changed.
- `tests/test_job_outcomes.py` submits a structured Job through the real HTTP run hook, launches the real worker subprocess, observes its exact HTTP status, executes the shared termination hook, and requires a subsequent simulated lifecycle confirmation before the synthesized workflow can finish. Five named tests cover success, failing setup, failing baseline verification, failing independent final verification, and uncertain publication failure. Assertions include one launch/token/agent at most, matching published Git/PR data on success, retained recovery ID, correct Succeed/Fail, stable status after termination, exit-code diagnostics and secret-free logs/output. Publication failure intentionally retains a simulated PR and pushed branch: failure does not guarantee no remote effects. Setup/baseline failures invoke no agent and publish nothing.
- The workflow simulator now accepts service callbacks as well as fixed responses, joining worker results to workflow transitions without duplicating an interpreter or service fixtures. Cloud lifecycle state is still simulated: local process exit and hook execution do not establish actual MicroVM termination, ingress authentication, token validity, runtime-role refresh, model access or live GitHub publication. Child-process execution is not included in parent coverage; retained focused tests measure worker behavior directly.
- Removed the implementation-location-only `verify_pr.__module__` assertion and the duplicate empty-sandbox-event case already covered by `test_sandbox_probe_failure_stops_before_completion`. Moved the standalone infrastructure termination ResultPath assertion into the existing synthesized state-plan test, retaining the protection without repeated decoding. No credential/authorization/lifecycle/verification/Git/PR failure cases were discarded.
- Replaced the remaining calculator-specific sandbox fixture with greeting source/tests and executable setup/verify scripts. The real sandbox still searches, changes existing code/tests, verifies, commits, checks baseline ancestry and clean Git metadata; no calculator operand or AST-preservation gate was restored. Infrastructure tests still check the accepted deployment defaults, rather than treating those defaults as generic worker requirements.

### Test classification and verification

| Protected behavior | Focused checks retained / consolidated |
| --- | --- |
| Scoped credentials, expiration, SDK failures and redaction | `test_worker.py` identity/secret/CLI guards, botocore Stubber cases, command and streamed-event redaction |
| Repository authorization and bounded structured Jobs | `test_server.py` invalid envelopes/configuration; worker rejects unauthorized input before credentials |
| Setup, verification, agent and publication consistency | `test_worker.py` script availability/exits/order, stage failures, probe, ancestry/clean commits/remote/PR checks; joined outcomes in `test_job_outcomes.py` |
| Lifecycle hooks, process groups and stable status | `test_server.py` readiness, asynchronous startup, spawn errors, termination; joined real worker/status/terminate path |
| Fresh polling, malformed responses, API errors and cleanup | `test_workflow.py` actual synthesized ASL, token reuse, known/unknown IDs, cleanup-unconfirmed output, persistent-pending timeout |
| Infrastructure trust, scoped policies, pins and source staging | `test_infra.py`, `test_image_pin.py`, `test_image_runtime.py` |
| ARM64 interpreter, managed configuration precedence and real sandbox tools | Docker-only `test_sandbox.py`, using the generic script fixture |
| Logging | `test_logging_smoke.py` and existing `make dev` |

- `uv run --locked python -m unittest discover -s tests -p test_job_outcomes.py -v`: five passed, covering all required outcome categories. Initial execution passed against existing production behavior; this story changes tests, not production behavior.
- Final `make verify`: formatting, lint, branch coverage/tests and logging passed; 60 tests discovered, 57 passed, three Docker-only skips. Compared with US-13, five joined checks replace three redundant/structural-only tests. Overall coverage remains 95% (499 statements, 18 missed; 124 branches, 13 partial). `worker/task.py` remains 95%, `worker/server.py` 92%, both infrastructure modules 100%. Source scope and coverage settings are unchanged. An initial format/lint pass exposed formatting issues; the first full verify caught a missing colon introduced while fixing formatting. Both were corrected before the final passing run.
- `make preflight`: ARM64 image built, credential-free Runtime validation passed, all three real sandbox/runtime/configuration checks passed, 22 focused worker/publication tests passed, and seven lifecycle tests passed with networking disabled. Joined ASL checks run on the host because CDK is a development-only dependency, not an image dependency.
- `make synth`: passed without AWS lookups or deployment. Existing Node 25.4.0/jsii compatibility and feature-flag notices remain; use recommended Node 22/24. No type-check or CI command is configured.
- Final focused outcome rerun: five passed. `git diff --check` and automated current-slice local-link, unique-heading, fence and `bash -n` snippet checks passed. Failure-path review found no material P0/P1 gap in this story's local regression boundary; live-verification gaps remain explicit below.
- No live AWS/GitHub suite, model call, deployment, image activation, secret change or PR publication was run. Local checks do not replace either remaining live demonstration or validate a new active image pin.

### Repeatable local outcome suite

Run from the factory root; no credentials or Docker required:

```sh
uv run --locked python -m unittest discover -s tests -p test_job_outcomes.py -v
```

`make verify` includes this suite. `make preflight` separately checks the actual image runtime and sandbox; neither command deploys or creates cloud resources.

### Separately authorized live outcome suite (not executed)

Prerequisites before **each** run:
- Obtain explicit operator approval for repository inputs, model submission, costs, publication and any fixture/permission/deployment changes. Gate 1 must already have passed; use a separately validated, explicitly pinned Gate 2 image and reviewed roles/connection/network/logging. The local preflight image is not an active MicroVM image pin. Do not reuse stale deployed worker/image evidence.
- Supply the reviewed Standard `STATE_MACHINE_ARN`, runtime `LOG_GROUP`, configuration-authorized `REPOSITORY` and configured `TARGET_BRANCH`. Keep one active Job at a time. Review scripts' access to the runtime role/token; no broader token scope or repository authorization is implied.
- Operator uses `AWSAdministratorAccess-172318459814` in `us-east-1`, with Start/DescribeExecution, restricted GetExecutionHistory, lifecycle get/terminate and log-inspection access. Runtime uses its scoped execution role and Secrets Manager repository token; never include credential values in Job files, prompts or this note. GitHub inspection uses separately authorized repository access.
- Prepare reviewed local Job JSON files, each containing exactly `repository`, full `baseSha`, and nonblank `task`. Pin real commits; do not invent SHAs. Success uses passing executable scripts and a meaningful task. Script failure uses an approved fixture commit whose setup (or baseline verify) exits nonzero. For publication failure, a proposed fault injection (not approved by this slice) is passing scripts with publication write permission withheld in an isolated runtime secret/configuration for the same repository; do not mutate the existing shared secret or broaden permissions. The operator must approve this injection and its configuration/deployment separately before using it. Do not add a failure Job field or production failure switch.
- Expected costs include MicroVM compute/storage, selected-model inference for success/publication scenarios, Step Functions transitions/HTTP tasks, image builds/storage if newly authorized, logs and Secrets Manager. These are billable live Jobs, not a free local check. Retain recovery access and record each real execution ARN, VM ID, script exits, PR findings, log cause and confirmed lifecycle result here.

For one reviewed scenario at a time, set `JOB_FILE` to its absolute approved JSON path and `NAME` to a new valid execution name. Run from the factory root in Bash without shell tracing. Reuse exactly the same name/input if StartExecution acknowledgement is uncertain; never rerun the Job under a new name to recover an uncertain request.

```sh
set -euo pipefail
export AWS_PROFILE=AWSAdministratorAccess-172318459814 AWS_REGION=us-east-1
EXECUTION_ARN=$(aws stepfunctions start-execution \
  --state-machine-arn "$STATE_MACHINE_ARN" --name "$NAME" \
  --input "file://$JOB_FILE" --query executionArn --output text)
printf '%s\n' "$EXECUTION_ARN"
# Poll without dumping input or execution history; stop after a bounded observation period.
for attempt in $(seq 1 180); do
  STATUS=$(aws stepfunctions describe-execution --execution-arn "$EXECUTION_ARN" \
    --query status --output text) || break
  printf '%s\n' "$STATUS"
  [ "$STATUS" = RUNNING ] || break
  sleep 10
done
# Extract only the launch ID from restricted history; never print raw history/token data.
VM_ID=$(aws stepfunctions get-execution-history --execution-arn "$EXECUTION_ARN" \
  --query "events[?type=='TaskSucceeded'].taskSucceededEventDetails.output" \
  --output json | uv run --locked python -c \
  'import json,sys; items=[json.loads(s) for s in json.load(sys.stdin)]; print(next(x["MicrovmId"] for x in items if isinstance(x,dict) and x.get("MicrovmId")))')
printf '%s\n' "$VM_ID"
aws lambda-microvms get-microvm --microvm-identifier "$VM_ID" \
  --query state --output text
# Inspect available sanitized application diagnostics; never dump raw execution state.
aws logs tail "$LOG_GROUP" --since 1h
# Inspect matching branch/head/base, open/unmerged state and human-review diff/tests.
gh pr list --repo "$REPOSITORY" --state all \
  --json number,state,mergedAt,baseRefName,headRefName,headRefOid,baseRefOid
```

Run the block for success, then script failure, then publication failure, supplying each reviewed Job and its separately approved state-machine configuration. A live suite passes only if all three records satisfy:

| Scenario | Required observations |
| --- | --- |
| Success | Workflow `SUCCEEDED`, independent verify exit 0, nonempty clean descendant commit and matching remote/head/open unmerged PR to the configured target; service `TERMINATED` |
| Script failure | Workflow `FAILED`, worker error and actual nonzero script exit in CloudWatch, no agent/publication for setup/baseline failure; service `TERMINATED` |
| Publication failure | Workflow `FAILED`, worker error and publication cause in CloudWatch after passing setup/baseline verification; service `TERMINATED`. Inspect existing branches/PRs: remote effects may exist despite failure |

Inspect successful final output with `aws stepfunctions describe-execution --execution-arn "$EXECUTION_ARN" --query output --output text`. For failures, inspect only error/cause via `--query '{status:status,error:error,cause:cause}'`; `CleanupUnconfirmed`, timeout, cancellation, missing worker evidence, or merely requesting termination fails the outcome check. Inspect the specific published PR with `gh pr view` / `gh pr diff` after identifying it; do not merge. A script/publication failure run is not US-05's confirmed-absent-SHA demonstration.

If history does not yield a known VM ID, stop the suite and use restricted launch/lifecycle records to recover it; never claim cleanup from an empty ID. Timeout/StopExecution bypass Catch cleanup. For a known VM still running or unconfirmed cleanup, request manual recovery and repeat the lifecycle observation until service-confirmed `TERMINATED`:

```sh
aws lambda-microvms terminate-microvm --microvm-identifier "$VM_ID"
aws lambda-microvms get-microvm --microvm-identifier "$VM_ID" --query state --output text
```

A successful request is not confirmation; retain the ID and report unresolved cleanup. Normal Jobs retain the 30-minute hard lifespan and one-hour nonsuspending idle policy. Do not delete shared resources, secrets, fixture commits, branches or PRs as suite cleanup. The separate US-06 automatic-lifespan check must not request termination unless recovering a failed check.

## Next Slice Recommendation
- `US-05 Observe a controlled orchestration failure with cleanup`: requires separate live-run authorization, confirmed-absent baseline SHA, newly validated active Gate 2 image pin and scoped permissions. Local suite success does not establish this gate outcome.
- `US-06 Verify the independent hard-lifespan fallback` follows separately; do not combine it with normal explicit cleanup.
