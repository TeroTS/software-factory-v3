# Current Slice

Status: done

## Operator-approved follow-up: allow the PR target branch to advance

- Request/approved plan: stop requiring the PR target head to equal the Job's
  pinned baseline. `baseSha` still establishes task-commit ancestry; the PR must
  still target the configured branch and match the verified local/remote task
  head. Compatibility with the current target branch remains a merge-time check,
  not a worker success guarantee.
- Removed only PR `baseRefOid` equality, its `gh pr list --json` field, and the
  unused `base` argument in `verify_pr()` and connected callers/fixtures.
  Independent script verification, baseline ancestry, nonempty clean commits,
  remote/task/PR head consistency, PR state/count, and target-branch identity
  checks remain. No Job/status API, workflow, dependency, or GitHub policy change.
- Updated `DECISIONS.md`, `SPEC.md`, and `docs/contracts.md` to distinguish a
  pinned starting baseline from a moving target head. Existing uncommitted
  worker-event-removal changes were preserved.
- TDD: a regression advances the real local fixture's target branch while the
  simulated agent publishes from the original baseline. It failed with the old
  PR equality check, then passed after removal. A separate real Git test creates
  a task commit without baseline ancestry and confirms rejection at
  `git merge-base --is-ancestor`. Retained PR rejection cases cover wrong target,
  task branch/head, closed/merged PRs, invalid PR numbers, and missing/duplicate
  PRs; the shared fixture asserts `baseRefOid` is no longer requested from GitHub.
- `uv run --locked python -m unittest discover -s tests -p test_worker.py -v`:
  all 24 passed. The equivalent `test_job_outcomes.py` invocation: all five passed.
  `make verify` passed formatting/lint, 62 discovered tests (59 passed, three
  Docker-only skips), coverage, and logging. Overall and worker coverage remain
  95%, with unchanged measurement scope (470 production statements, 17 missed).
- `make preflight` passed the ARM64 image build, runtime validation, three real
  sandbox/runtime/configuration checks, 24 worker/publication checks, and seven
  lifecycle checks with external networking disabled and no host credentials.
  `git diff --check` and changed-Markdown local-link/fence checks also passed.
- No deployment, live AWS/GitHub Job, model call, publication, or merge-policy
  changes were performed. Synthesis was not rerun for this follow-up because
  infrastructure is unchanged; `make synth` remains available for a fresh local
  asset/template. The locally built image is not a new deployed version pin.

## Operator-approved follow-up: remove worker event handling

- Request: remove worker-level agent event streaming and analysis. Scope is
  `worker/task.py`, its fixture/regression tests, and the accepted invocation
  requirements in `DECISIONS.md` and `SPEC.md`; no lifecycle/status API or
  workflow change.
- `run_agent()` runs one Codex process with the prompt on stdin, waits for exit,
  logs redacted stdout/stderr and the actual process exit code, and propagates
  nonzero exit. Removed `--json`, event parsing/collection, event-type failure
  inference, and event-based initial sandbox-shell validation. The fixture no
  longer fabricates events. Plain diagnostics and failure-shaped JSON do not
  override a successful process exit; success-looking output cannot override a
  nonzero exit.
- Retained managed sandbox policy, image/runtime sandbox validation, the prompt's
  initial shell check, one invocation, independent final verification, Git/PR
  consistency checks, scoped credentials, and unchanged status polling/cleanup.
  The worker no longer independently proves that the initial agent shell check
  passed. Human review remains required.
- Output redaction now also recognizes escaped credential-field delimiters:
  removing event decoding initially exposed nested JSON credential values in a
  regression test. The regression failed before the redaction fix and passed
  afterward, without reintroducing event parsing.
- TDD: updated worker tests failed against the old invocation/parser; after the
  change, `uv run --locked python -m unittest discover -s tests -p test_worker.py -v`
  passed all 22 tests. `make verify` passed formatting, lint, 60 discovered tests
  (57 passed, three Docker-only skips), coverage, and logging. Overall and worker
  coverage remain 95%; measured production statements dropped from 499 to 470
  through deletion, with unchanged measurement scope.
- `make preflight` passed the ARM64 image build, runtime validation, all three
  real sandbox/configuration tests, 22 worker/publication tests, and seven
  lifecycle tests with external networking disabled. No dependencies changed.
- `make synth` passed without deployment; the existing Node 25/jsii compatibility
  warning and CDK feature-flag notice remain (Node 22/24 is recommended).
  `git diff --check` and changed-Markdown local-link/fence checks passed.
- Operational limit: agent output is buffered in memory until process exit, not
  available as live progress; forced termination can lose buffered diagnostics.
  Image/runtime validation is not proof of the agent's initial shell check or
  live model/publication behavior. No deployment, live AWS/GitHub Job, model call,
  or publication was authorized or performed; a new deployed image still needs
  separate authorization and validation.

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

### Requested single live happy-path run: prerequisite check

- Operator requested one live AWS/GitHub happy-path end-to-end test. Read-only prechecks authenticated the expected AWS account `172318459814` and GitHub operator; no Job was started and no deployment, secret, permission or GitHub mutation was made.
- `FactoryWorkflow` is still pinned to image `2.0`; its deployed definition contains the pre-US-13 `InspectImage`/`InspectVersion`/`Launch`/`Poll` state plan, not the current simplified workflow. No running execution was listed for this state machine.
- Image `2.0` is `SUCCESSFUL`/`ACTIVE`, but its source artifact is the earlier `a05fdf2c933f9f12422e714aec75a0d31240e7947af64fedc9c573991b431698.zip`. Fresh `make synth` passed (existing Node 25/jsii warning); `uv run --locked python tools/verify_image.py --version 2.0` correctly rejected the deployed pin because it does not match the current synthesized source. Initial image reads using a bare name were rejected; reads using the full ARN succeeded.
- Demonstration `main` remains at `74daa6e2bca790d0a62d4d9ab47f07bca48fbc12` with `README.md`, `calculator.py` and `tests/`, but no required `scripts/setup` or `scripts/verify`. The historical Gate 1/US-04 success evidence remains available in this file at commit `8a8c821`; it does not validate the current worker/image.
- Blocked before submission: obtain separate approval to prepare and test executable repository scripts on a new pinned demonstration baseline, validate/build and deploy the current native CDK image/workflow with reviewed permissions and an explicitly verified new image pin, then run the requested single Job. Preserve all existing PRs and the GitHub-token secret. No automatic rerun, PR merge, controlled-failure run or hard-lifespan check is included.

### Operator-authorized current-source happy-path preparation

- Operator approved the prerequisite plan and exactly one live happy-path Job, including fixture scripts/new baseline, validated image/workflow deployment, scoped permission review, agent publication, inspection and confirmed cleanup. No additional Job attempts, PR merge, secret replacement or US-05/US-06 runs are authorized.
- Preparation started `2026-11-02T08:05:28Z`. Full lifecycle listing returned `{"items":[]}` and no running workflow executions. Before any writes, PR #3 was OPEN at `14bd7bd93934281aa43407c6b2bd0c091eb8d025`; PR #1 was CLOSED/unmerged at `1ffdc641d6aa0d26b4a0a3634f4527453956fac8` and #2 CLOSED/unmerged at `2d303c08c0c9d0756ee536b0f402b6c56a0f3bc1`. Preserve these observed states/heads, rather than reopening historical PRs.
- `make verify` passed: 60 tests, three Docker-only skips, 95% branch-aware coverage (same 499 statements/18 missed). `make preflight` passed validation, three real ARM64 sandbox/runtime/configuration checks, 22 worker/publication checks and seven lifecycle checks, without host credentials or networking. Node 25/jsii warning remains.
- CDK template-only diff was reviewed: new image artifact and explicit existing-repository/main configuration, build-role access moved to the new exact S3 ZIP, no runtime secret/model/trust expansion; workflow removes image-inspection permissions and uses the simplified state plan. No resource replacement or deletion is proposed. Initial `diff` invocation combined incompatible template-only and parameter flags; rerunning without the parameter succeeded. AWS definition validation of synthesized current workflow returned `OK`, zero diagnostics (prospective version used only for validation, not selected as a pin).
- The authorized fixture clone contains unchanged calculator/addition tests. Executable script contract assertion failed before creation, then passed for minimal dependency-free `scripts/setup` (`python3 --version`) and `scripts/verify` (standard-library unittest discovery). Baseline three tests passed; a disposable failing-test fixture proved nonzero propagation. ARM64 Python 3.13.16 ran both scripts and all three baseline tests successfully. Docker required the macOS `/private/tmp` bind path after `/tmp` was rejected; no image/runtime change was needed.

### Current-source deployment and retained single-run input

- Published only executable `scripts/setup` and `scripts/verify` in the authorized demonstration repository; new tested baseline is `f810f1231a7f8110c45dfd68c2bfa09b6eeb5fae` on `main`. Existing calculator source/tests were unchanged. This baseline is specific to the approved current-source rerun; historical gate evidence retains its original SHA.
- Reviewed actual build/runtime/workflow role trust and inline policies; no attached managed policies. Runtime secret/model/log scope remains unchanged. Recorded secret version-stage metadata without reading its value, for final preservation comparison.
- `npm run deploy -- FactoryImage --exclusively --require-approval never --parameters FactoryImage:AuthorizedRepository=TeroTS/factory-smoke-test --parameters FactoryImage:PrTargetBranch=main` succeeded. The approval override was used only after explicit operator authorization and permission/diff review. Native CDK created image `3.0`, source `s3://software-factory-poc-gate2-artifacts-172318459814-us-east-1/image/797242f6dfc61e3405680dd3870a2f7fc00d2bd8b43788cb181812f2a1f48df4.zip`.
- `uv run --locked python tools/verify_image.py --version 3.0` passed exact-source `SUCCESSFUL`/`ACTIVE` verification before workflow pinning. Read-back confirmed base `3.1`, ARM64/2048 MiB, all four enabled port-8080 hooks, internet egress, authorized demonstration repository, `main`, expected runtime role and unchanged secret ARN.
- `npm run deploy -- FactoryWorkflow --exclusively --require-approval never --parameters FactoryWorkflow:ImageVersion=3.0` succeeded without replacement. Actual ACTIVE/Standard definition exactly matches the reviewed current ASL, including 1680-second timeout, 1800-second lifespan, one-hour nonsuspending idle policy, fresh polls and shared termination. AWS validation returned `OK`/zero diagnostics; connection is AUTHORIZED; deployed role no longer grants image-inspection calls. No active VM or workflow execution existed immediately before submission.
- Retain the single authorized execution name **before submission**: `happy-path-f8afee5d5d994cf7b5214c63461ebd4d`. Submit compact ordered Job JSON: `{"repository":"TeroTS/factory-smoke-test","baseSha":"f810f1231a7f8110c45dfd68c2bfa09b6eeb5fae","task":"Add multiply(a, b) and tests for positive, negative, and zero operands while preserving addition behavior. Do not modify deployment configuration."}`. Retry an uncertain acknowledgement only with this exact name/input; no second task attempt is authorized.
- Acceptance is pending one real execution and PR inspection. Deployment/local preflight does not establish current-image SDK credentials, model access, authenticated polling, publication or cleanup.

### Current-source live execution: passed

- Started the sole authorized Job at `2026-11-02T08:13:40.949Z`, execution ARN `arn:aws:states:us-east-1:172318459814:execution:software-factory-poc:happy-path-f8afee5d5d994cf7b5214c63461ebd4d`. It finished **SUCCEEDED** at `2026-11-02T08:14:54.797Z` (73.848 seconds).
- Retained VM ID `microvm-e5829932-517d-3396-8eba-b57138fb22a4`, image `3.0`. Restricted history proves exactly one RunMicrovm, one CreateMicrovmAuthToken, seven HTTP polls and one TerminateMicrovm; two GetMicrovm calls cover startup and termination. All HTTP responses were 200 with exactly six pending bodies followed by one ok body. The same nonempty port-8080 JWE was compared across all polls only in memory; no token/raw execution history was printed or saved.
- Workflow followed RecordSuccess → TerminateVM → WaitForVMToTerminate → GetTerminationState → IsVMTerminated → EndResult → CheckEndResult → JobSucceeded. Exact token-free final output: `{"termination":"TERMINATED","microvmId":"microvm-e5829932-517d-3396-8eba-b57138fb22a4","status":"ok"}`. Independent GetMicrovm confirms `TERMINATED`, image `3.0`, reason `Success.`; the lifecycle list contains only this terminated VM. No manual termination, local lifecycle controller, second execution or automatic rerun was used.
- CloudWatch stream `2026/11/02[3.0]microvm-e5829932-517d-3396-8eba-b57138fb22a4` retains successful initial/final scoped credential discovery, repository token/auth initialization, setup exit 0, three passing baseline tests, the initial sandbox shell marker, agent-side six passing tests, and independent final verify exit 0 with six passing tests. One Codex invocation completed native-model editing/testing/commit/push/PR publication and worker Git/PR consistency checks. Credential rediscovery reported `rotated: False`; expiration-driven rotation is not claimed. New-image build streams `3.0/e34719ea-5770-4d5c-b67f-0db2157d69c2` and `3.0/7d1af40b-af2c-42d4-966d-b08ea10cc27a` separately retain real workspace-write sandbox validation PASS.
- The agent encountered unavailable `find` (exit 127), then recovered using installed `rg` within the same invocation; no dependency/image change or agent restart was needed. This ordinary recovered command failure did not prevent successful verification/publication.
- Published [PR #4](https://github.com/TeroTS/factory-smoke-test/pull/4), OPEN/unmerged to `main`, branch `factory/task-91b4edc82941414a85241da34ca7e198`, head `45228d49b84ec7d0e73a324b2171d8a6ac23e724`. GitHub comparison confirms one commit ahead/zero behind, merge base equal to pinned `f810f1231a7f8110c45dfd68c2bfa09b6eeb5fae`, and only `calculator.py`/`tests/test_calculator.py` changed. Inspected diff preserves addition and all existing assertions, adds multiplication and positive/two-negative/mixed-sign/zero-in-both-positions cases, and does not change scripts/deployment configuration. Human approval/merge remains separate; no automated chronology or test-weakening gate was restored.
- A separate read-only checkout of the exact published head passed `./scripts/setup && ./scripts/verify` in the credential-free, network-disabled ARM64 preflight image: Python 3.13.16 and all six tests, exit 0. Existing PR #1/#2/#3 states and heads remain unchanged and all PRs are unmerged; there is exactly one new PR. Secret version-stage metadata remains unchanged/not scheduled for deletion, with no operator GetSecretValue call.
- Final `make verify` passed format/lint/logging and all 60 discovered tests (57 passed, three Docker-only skips); coverage remains 95%, scope unchanged. Earlier `make preflight`, synthesis, AWS ASL validation and exact-source image-pin checks passed as recorded above. This establishes the requested current-source live happy path only; US-05 controlled failure and US-06 automatic hard-lifespan demonstrations were not run. No further Job is authorized.

## Next Slice Recommendation
- `US-05 Observe a controlled orchestration failure with cleanup`: requires separate live-run authorization, confirmed-absent baseline SHA, newly validated active Gate 2 image pin and scoped permissions. Local suite success does not establish this gate outcome.
- `US-06 Verify the independent hard-lifespan fallback` follows separately; do not combine it with normal explicit cleanup.
