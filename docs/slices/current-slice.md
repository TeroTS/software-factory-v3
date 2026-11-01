# Current Slice

Status: done

## Slice Goal
- Let the operator submit the approved task to Step Functions and observe a tested PR and confirmed VM cleanup without a local controller.

## Slice Boundary
- Operator, after Gate 1 and the approved CDK reset, manually starts one Standard execution. One replacement worker/Codex invocation publishes a tested PR; workflow success requires worker `ok` and service-confirmed `TERMINATED`.

## Stories In Scope
- `US-04 Submit a task that succeeds with orchestrated cleanup`

## Stories Completed In This Slice
- `US-04 Submit a task that succeeds with orchestrated cleanup`

## Stories Remaining In This Slice
- None.

## Contract Inputs
- [openapi.yaml](../../openapi.yaml): Job, lifecycle hooks, port 8080, and exact status responses.
- [docs/contracts.md](../contracts.md): serialized Job in StartExecution input/runHookPayload and idempotent execution naming.

## Decision Inputs
- [DECISIONS.md](../../DECISIONS.md): native CDK reset, scoped permissions, native Bedrock/sandbox, publication, timeout, token reuse, and manual recovery.
- [Gate 2 plan](../plans/gate2.md): native resource ownership and replacement-worker acceptance; destructive transition requires separate authorization.

## Shared State Required Now
- Demonstration repository `TeroTS/factory-smoke-test`, baseline `74daa6e2bca790d0a62d4d9ab47f07bca48fbc12`, target `main`, fixed `python3 -m unittest discover -s tests -v`.
- Task: add multiplication with positive, negative, and zero tests, preserving addition and existing tests/deployment configuration.
- Gate 1 passed on image `7.0`, VM `microvm-69ecf16b-32c6-3bc5-baac-37b0bb0228fc` confirmed manually `TERMINATED`. Published commit `1ffdc641d6aa0d26b4a0a3634f4527453956fac8`, unmerged [PR #1](https://github.com/TeroTS/factory-smoke-test/pull/1). US-01–US-03 are complete; do not repeat Gate 1.
- Preserve secret `arn:aws:secretsmanager:us-east-1:172318459814:secret:software-factory-poc/github-token-sXayGQ` and PR #1. No secret retrieval during preparation.
- One runtime job/process group, stable status, one workflow VM ID/auth token, and a newly verified pinned image version.

## Operations / Endpoints / Surfaces In This Slice
- Native Python CDK app, private artifact storage, scoped roles/logs, native MicroVM image, EventBridge connection, and Standard workflow.
- Replacement worker, retained lifecycle server and Dockerfile, tests, local synthesis/development commands, and coverage.
- Read-only prerequisites already checked: `AWS::Lambda::MicrovmImage` is registered `FULLY_MUTABLE` in us-east-1, with native create/update/delete handlers and active/failed-version attributes. Python CDK `CfnMicrovmImage` exists in version 2.275.0. No custom provider/template import needed.
- Existing `CDKToolkit` is `UPDATE_COMPLETE`, bootstrap version 25. Reuse requires synthesis and role-policy review before deployment; no rebootstrap performed.
- Operator approved development-only `aws-cdk-lib==2.275.0`, `constructs==10.8.1`, and repository-local npm `aws-cdk==2.1147.0`; no global installation. Python 3.14/jsii compatibility must be verified locally.

### Local implementation and verification

- Added `infra/{app,stacks,workflow}.py`, `cdk.json`, and repository-local
  `package.json`/`package-lock.json`. `FactoryArtifacts` owns a new private,
  encrypted, TLS-only bucket; `FactoryImage` owns native `CfnMicrovmImage`,
  full hooks/environment, build/runtime roles, and logs; `FactoryWorkflow`
  owns the connection and Standard state machine. No template import, resource
  adoption, Lambda/custom provider, automatic bucket-emptying, or bootstrap
  mutation is introduced. The artifact bucket/log group are retained on future
  stack removal so deletion remains an explicit operator action.
- CDK uses `CliCredentialsStackSynthesizer` with the new application bucket for
  image ZIPs. Deploy storage separately before asset publishing. This does not
  require modifying the existing CDKToolkit or copying operator credentials
  into the image. Build-role artifact access is scoped to the content-addressed
  source ZIP; runtime secret/log/model scope and workflow trust/HTTP/connection/
  PassRole permissions are asserted in synthesis tests and locally inspected.
  Actual deployed-policy and service-access inspection remains outstanding.
- Source staging copies only Dockerfile, the three worker modules, and the
  dedicated fixed-task acceptance script. The workflow requires an explicit
  `ImageVersion` parameter, not latest-active fallback. Read-only
  `tools/verify_image.py` rejects pending/failed/inactive builds and mismatched
  versions/source ZIPs. The workflow rechecks image/version readiness before
  launching. Native asynchronous build completion/deletion behavior still
  needs actual deployment evidence; stack completion alone is not acceptance.
- Added a replacement `worker/task.py` and generic `worker/evidence.py`; retained
  the existing lifecycle server without modifying its implementation. One
  scoped-runtime-identity check precedes token/model access, one Codex invocation
  owns all editing/testing/recovery/publication, and stdout/stderr are redacted.
  Completion requires successful command evidence in order, independent tests,
  preserved baseline AST/test classes, meaningful source/test changes, clean Git
  state, baseline ancestry, matching remote head, and exactly one open/unmerged
  PR to main. Timeouts can recover through agent-side remote-branch/PR checks,
  never an agent restart or automatic task rerun.
- `image/acceptance.py` is a separate fixed demonstration check, not a generic
  task API or calculator-specific execution runner. It independently verifies
  multiplication/addition and observes the real committed unittest suite's
  positive, mixed-negative, two-negative, and both zero-operand categories.
  Neither successful process exit nor a positive-only new test proves the task.
- Official pinned Codex source/schema confirms its default shell environment
  filters remove variables matching `*KEY*`, `*SECRET*`, and `*TOKEN*`. Added
  tested explicit runtime environment inheritance/default-filter settings so
  sandbox shell Git/gh can use the approved repository token and role discovery.
  This implements the already accepted controlled-repository credential trade-off;
  token values are not put in prompts/configuration or supplied to preflight.
- Test-first verifier/worker/CDK/workflow tests failed for the missing behavior,
  then passed. Regression checks caught nested escaped-JSON credential leakage,
  discarded successful-command stderr, missing operand-test evidence, edits after
  the final tests, and missing shell credential inheritance; fixed each shared
  implementation and reran the focused checks. Ordinary command/test recovery
  remains within the one invocation.
- ARM64 preflight exposed two fixture issues: writable `.git` must already exist
  before Codex resolves its sandbox roots (the real worker clones first), and
  Python 3.9's shared default unittest loader retains the outer discovery path.
  Prepared the sandbox fixture's `.git` and used a fresh acceptance TestLoader;
  the real worker sandbox was not relaxed. Only Docker's outer seccomp filter
  is disabled for local nested namespaces. Network-disabled containers receive
  no host home/profile/credentials and use temporary local Git fixtures.
- Commands verified: `make setup` passed locked Python/npm installs; `make verify`
  passed formatting/lint/logging and 37 discovered tests (36 passed; the one real
  Linux sandbox test is intentionally host-skipped); `make synth` synthesized
  all three stacks; `make preflight` passed the real sandbox/search/edit/test/Git
  check plus 17 worker and six lifecycle tests on Linux ARM64 Python 3.9.
  Combined branch-aware host coverage is 93%, expanded to tools/worker/infra/image
  (from the pre-slice 2% uncovered-server baseline). No threshold/scope reduction.
  Independent acceptance subprocess paths are tested but not all attributed to
  host coverage; image acceptance is 79%, server 92%, task 94%, evidence 95%.
- Read-only AWS `validate-state-machine-definition` returned `OK`, zero diagnostics,
  for the Standard ASL. `cloudformation validate-template` accepted all three
  synthesized templates; Image requires `CAPABILITY_NAMED_IAM`, Workflow requires
  `CAPABILITY_IAM`. These syntax checks do not prove live API response mapping,
  authentication/model access, native build completion, or orchestration.
- Python 3.14/jsii/CLI synthesis passed locally, with a warning that installed
  Node 25.4.0 is outside jsii's tested Node 22/24 LTS versions. No Node version was
  installed/replaced globally. Initial npm installation against the configured
  corporate mirror failed with `ETARGET`; public-registry install of the exact
  approved version and subsequent locked `make setup` passed without changing
  the host registry configuration. README/agent guidance now describe the actual
  commands, dependencies, measurement scope, and local/live verification boundary.
- Whole-tree `git diff --check`, affected Markdown link/fence checks, and `bash -n`
  on documented shell blocks also passed. No external-link/browser check was run.
- No AWS creation/update/deletion, secret retrieval, model invocation, GitHub
  write, or real task launch occurred. Gate 1 resources/image/secret/PR remain
  untouched; US-04 stays active, not complete, until the authorized live run.

### Authorized reset and live deployment

- Operator explicitly authorized deleting the disposable Gate 1 resources/logs,
  deploying native CDK Gate 2, and one US-04 live task attempt with cleanup.
  Authorization excludes PR merging, secret replacement, automatic task reruns,
  and the separate US-05/US-06 demonstrations.
- STS reconfirmed account `172318459814`. Read-only inventory showed the three
  known Gate 1 VMs all `TERMINATED`; initial list query used the wrong collection
  key and returned null, so full `items` read-back was checked before deletion.
  Gate 1 image remains `UPDATED`, active version `7.0`. Old stack contains only
  artifact bucket/policy, build/runtime roles and logs. Bucket versioning is not
  enabled. Secret AWSCURRENT version is unchanged/not scheduled for deletion;
  PR #1 remains OPEN/unmerged at the retained commit.
- Re-ran `make verify` (36 passed, one host skip, 93% coverage), `make synth`,
  `make preflight` (24 ARM64 tests plus actual validation), and `git diff --check`:
  all passed before destructive transition. Native CDK role/image/workflow
  configuration remains unchanged from the reviewed local implementation.
- Deployment order is storage, image, exact build read-back, then explicitly
  pinned workflow. Commands below are the authorized sequence, not proof of
  successful deployment until the subsequent outcome is recorded:

  ```sh
  npm run deploy -- FactoryArtifacts --exclusively --profile AWSAdministratorAccess-172318459814
  npm run deploy -- FactoryImage --exclusively --profile AWSAdministratorAccess-172318459814
  # Wait/inspect the NEW build; never substitute an old active version after failure.
  uv run --locked python tools/verify_image.py --version NEW_VERSION
  npm run deploy -- FactoryWorkflow --exclusively --parameters FactoryWorkflow:ImageVersion=NEW_VERSION --profile AWSAdministratorAccess-172318459814
  ```

- Confirm the new image's own AWS validation, actual runtime role/native model/
  sandbox/tool compatibility, one tested separate PR, stable authenticated status,
  one token reused across polls, and workflow Succeed only after confirmed
  TERMINATED. Restrict execution-history access (JWE is held there); timeout or
  StopExecution cannot run Catch cleanup and still require lifespan/manual recovery.
- Authorized transition outcomes: old image deletion was service-confirmed by
  ResourceNotFound after DELETING; six old S3 artifacts were removed and the
  CloudFormation delete waiter succeeded. The expected final DescribeStacks
  `does not exist` response confirms stack removal, not a cleanup failure.
  Separate read-backs confirmed both old roles, old bucket and old log group/
  streams absent. The preserved GitHub secret's same AWSCURRENT version remains
  unchanged. No active VM existed before deletion.
- Native `FactoryArtifacts` deployment succeeded, owning private bucket
  `software-factory-poc-gate2-artifacts-172318459814-us-east-1`. CDK used the
  existing bootstrap bucket for its template publishing, and will use the new
  application bucket for the image source ZIP; no rebootstrap occurred.
- Initial `FactoryImage` deployment failed before any image build/task launch:
  service rejected `BaseImageVersion: 3.1`, requiring a single major version.
  Changed the native-image synthesis regression expectation to `3`; it failed
  against the old code, then passed after correcting the request. Focused CDK
  tests, `make verify` (unchanged 93%), synthesis and whitespace checks passed.
  This is a request-shape correction, not a managed-base/provider change.
- CloudFormation rolled the failed image stack back completely, removing roles
  but retaining its newly created empty log group. Confirmed zero streams,
  deleted the failed stack and this empty orphan group as deployment recovery,
  then re-deployed through native CDK. `FactoryImage` reached CREATE_COMPLETE
  after approximately 229 seconds; no custom provider or manual image creation.
- New image version `1.0` is service-confirmed SUCCESSFUL/ACTIVE; requested base
  major `3` resolved to `3.1`. Source artifact:
  `s3://software-factory-poc-gate2-artifacts-172318459814-us-east-1/image/b97c5ede14f2f00fe4ce25dee1a8de0a921dd2bfffcb6bb177e68dd3b9825eb8.zip`.
  `tools/verify_image.py --version 1.0` verified this exact newly synthesized ZIP.
  Read-back confirmed ARM64, 2048 MiB, enabled four port-8080 hooks, internet
  egress, designated logging, preserved secret ARN and expected runtime role.
  Additional capabilities were absent (none granted). Both AWS image-validation
  streams `1.0/b9f8cd2d-ab43-4f9c-b4f5-d4c5a06d166f` and
  `1.0/7ffc0bb6-1889-44c9-a7c5-3b7a3011c2b1` recorded actual sandbox-shell PASS.
- Inspected both newly deployed roles' trust/inline policies: Lambda AssumeRole
  and TagSession, designated log group, exact source ZIP for build access, and
  runtime access only to the preserved secret and selected inference profile/
  three destination models/default project. No managed administrator policies.
  New artifact bucket public-access blocks are all enabled.
- `FactoryWorkflow` native CDK deployment reached CREATE_COMPLETE. Standard
  state machine: `arn:aws:states:us-east-1:172318459814:stateMachine:software-factory-poc`;
  pinned replacement image `1.0`. Connection:
  `arn:aws:events:us-east-1:172318459814:connection/Connection-49bZP6x1Homh/5ca5f7dc-693a-46f2-8ef7-282bcc9da17c`;
  execution role:
  `arn:aws:iam::172318459814:role/FactoryWorkflow-ExecutionRole605A040B-2ArtuGMKbucA`.
- Read-back checked the 1680-second workflow/1800-second VM bounds, image pin,
  execution role trust (States principal, account/SourceArn conditions), scoped
  image/network/PassRole/HTTP GET/connection-managed-secret access, and absence
  of managed administrator policies. Connection is AUTHORIZED and exposes only
  the static X-Poc-Connection API-key name, not overriding proxy headers. Its
  separate automatically managed secret is scoped in the workflow role.
- Revalidated the deployed ASL: OK/no diagnostics. Read-only Step Functions
  TestState calls for InspectImage/InspectVersion proved native SDK PascalCase
  State/Status responses and scoped-role access. A Pass-state TestState proved
  States.JsonToString produces the exact compact status string expected by the
  choices. These prechecks create no VM/token/model invocation.
- Account-wide listing still retains the three old TERMINATED VM records after
  image deletion/recreation. A too-strict empty-list assertion was corrected
  operationally by inspecting their IDs/versions and states; there are no active
  VMs and no replacement-image task VM before the authorized run.

### Live US-04 execution

- Retain this name before StartExecution so an uncertain request can use exactly
  the same name/input, never create a duplicate job:
  `gate2-us04-d4a987c554494edabefdf7bbd9e74197`.
- Authorized execution input, serialized as compact JSON in this field order:
  repository `TeroTS/factory-smoke-test`, baseSha
  `74daa6e2bca790d0a62d4d9ab47f07bca48fbc12`, task
  `Add multiply(a, b) and tests for positive, negative, and zero operands while preserving addition behavior. Do not modify deployment configuration.`
- Started the single authorized execution at service time
  `2026-10-31T23:09:20.033000+02:00`. Execution ARN:
  `arn:aws:states:us-east-1:172318459814:execution:software-factory-poc:gate2-us04-d4a987c554494edabefdf7bbd9e74197`.
  It ended FAILED/JobFailed immediately after launch. Native task output retained
  VM `microvm-71afabd6-c071-3703-9b3d-e02d82ba2c18`; direct lifecycle inspection
  found it RUNNING despite workflow failure. Manual recovery is required and
  authorized. No task rerun was made. US-04 has not passed.
- Immediately requested manual termination and confirmed TERMINATED through
  GetMicrovm at `2026-10-31T23:09:51.850000+02:00`, reason `Success.`. This is
  manual recovery, not orchestrated-cleanup acceptance. Final account-wide
  listing contains only the four retained TERMINATED VM records.
- Execution history proves the exact failure route: Input → InspectImage →
  ImageReady → InspectVersion → VersionReady → Launch → KnownVM → JobFailed.
  Zero auth-token tasks and zero HTTP polls occurred. The known VM ID was
  present in native task output but incorrectly rejected by the nonempty-string
  guard, causing the unknown-launch Fail branch rather than shared cleanup.
- Root cause is `StringMatches: "?*"`: ASL treats `?` literally (only `*` is the
  wildcard), unlike the local simulator's Python fnmatch. Read-only AWS TestState
  reproduced KnownVM incorrectly choosing JobFailed for a valid synthetic VM ID.
  Corrected simulator semantics and added a regression that first failed, then
  replaced the shared guard with type/existence checks plus Not StringEquals
  empty string. This fixes VM-ID, endpoint and token guards in one place without
  changing lifecycle contracts or adding a runner/retry.
- Focused workflow tests (seven), `make verify` (37 passed, one Docker-only host
  skip; 93% coverage), synthesis and whitespace checks passed after the fix.
  Eight actual AWS TestState checks proved valid/empty/missing/wrong-type VM ID,
  valid/missing endpoint and valid/missing synthetic-token routing. These are
  single-state checks without launching a VM or generating a JWE/model call.
- Updated only `FactoryWorkflow` through CDK; it reached UPDATE_COMPLETE, still
  pinned to image `1.0`. Deployed-definition read-back contains the corrected Not
  guard; AWS validation returned OK/no diagnostics. An earlier validation helper
  using placeholder `connection` failed the ConnectionArn schema check; the
  actual deployed ARN/definition passed, not an ignored deployment error.
- Runtime CloudWatch stream
  `2026/10/31[1.0]microvm-71afabd6-c071-3703-9b3d-e02d82ba2c18` proves initial
  scoped execution-role credential discovery, runtime GitHub token/auth setup,
  clone/pinned checkout, unique branch `factory/task-e5d5daa022bd42d88f6ad3ce0d11a51d`,
  and all three baseline tests passing with actual exit code 0. No agent command/
  task-completion output was retained before manual interruption; model inference
  and publication compatibility are therefore unverified, not claimed absent.
- Read-only GitHub inspection confirms only the unchanged, OPEN/unmerged PR #1.
  No Gate 2 PR was created. Preserved secret still has the original AWSCURRENT
  version and is not scheduled for deletion. No workspace artifacts were exported.
- US-04 remains active/blocked: native reset/image/workflow deployment succeeded,
  but the sole authorized live attempt failed and needed manual cleanup. The
  workflow fix is deployed; a fresh task attempt needs explicit operator approval.
  US-05 and US-06 remain separate and were not run.

### Operator-authorized fresh US-04 attempt

- Operator explicitly authorized one fresh execution after the deployed guard fix.
  Read-back and actual AWS TestState confirmed the deployed KnownVM guard selects
  Endpoint for a valid ID, ASL validation is OK, and image `1.0` still matches
  the successful/active synthesized source. All four prior VM records are
  TERMINATED. No code/image/permission changes are being made for this attempt.
- Retain new execution name before submission:
  `gate2-us04-a61c1495bb5e488ca5d696256b253cbd`.
  Input is exactly the same compact, ordered repository/baseSha/task Job recorded
  above. This is a separately authorized attempt, not an automatic task rerun.
  Further attempts remain unauthorized.
- Started this authorized execution at `2026-10-31T23:24:53.780000+02:00`:
  `arn:aws:states:us-east-1:172318459814:execution:software-factory-poc:gate2-us04-a61c1495bb5e488ca5d696256b253cbd`.
  Retained VM `microvm-a3a5dab2-31d5-3ec5-809c-bb9c44bc5823`, image `1.0`.
- Authenticated polling now works: exactly one CreateMicrovmAuthToken task and
  three HTTP Tasks returned HTTP 200 with exact pending, pending, error bodies.
  Execution-history values were compared only in memory: all three polls reused
  one JWE. No token value was printed/saved or placed in application logs/input.
- Codex completed the task in one invocation using the native provider/model and
  workspace-write sandbox. Actual initial shell probe and rg search passed;
  final approved unittest command passed six tests with exit code 0 before
  separate successful commit, push and PR creation. Published commit
  `2d303c08c0c9d0756ee536b0f402b6c56a0f3bc1`, branch
  `factory/task-8c15a43737694d2b83a77da2984e8480`, unmerged
  [PR #2](https://github.com/TeroTS/factory-smoke-test/pull/2). Independent
  in-VM unittest and approved behavior/operand-category checks also passed.
- Worker then falsely rejected added tests because the new MultiplicationTest
  class correctly reuses method names from AdditionTest. Generic verifier
  compared method names alone instead of class/method identities. Reproduced
  its exact `No added tests` failure from published GitHub source, not downloaded
  VM workspace. It reported error, so this execution cannot count as success.
- Workflow reached shared Terminate and requested termination once, but CDK's
  `Stack.to_json_string` omitted Python None / ASL ResultPath:null. Native SDK's
  empty termination acknowledgment therefore replaced all input with `{}`.
  Confirm failed with States.Runtime because the retained VM ID was lost. This
  defect exists in the synthesized/deployed definition, not the raw Python ASL
  previously used by simulations. The workflow did not confirm cleanup itself.
- Direct service read-back confirmed the workflow-terminated VM TERMINATED at
  `2026-10-31T23:26:01.750000+02:00`, reason Success. No manual termination request
  was needed for this attempt; no active VM remains. Runtime output is retained
  in stream `2026/10/31[1.0]microvm-a3a5dab2-31d5-3ec5-809c-bb9c44bc5823`.
  Initial expiring scoped credential discovery passed, but post-task rediscovery
  was not reached after the verifier error. No expiration-driven rotation claim.
- Added failing regressions for class-qualified test identities and the actual
  CDK-serialized termination ResultPath. Corrected source verifier in its shared
  module and stored termination acknowledgment under `$.terminationRequest`
  instead of None, so CDK preserves the original VM/outcome/token state. No
  custom serializer, contract change, agent loop, or new dependency was added.
- Focused worker/CDK/workflow tests passed; `make verify` passed 39 tests with one
  Docker-only host skip (40 discovered), format/lint/logging and unchanged 93%
  branch-aware coverage. `make synth`, `make preflight` (25 ARM64 tests plus real
  validation), and `git diff --check` passed.
- Corrected verifier accepts the actual published PR #2's baseline/current source
  and tests. GitHub comparison confirms exactly one commit ahead, baseline SHA
  as merge base, and only the two approved paths changed. Read-only gh PR metadata
  returns the worker's exact required fields and pinned base/head values.
- Native CDK `FactoryImage` update succeeded (about 207 seconds), creating newly
  verified SUCCESSFUL/ACTIVE version `2.0`, source
  `s3://software-factory-poc-gate2-artifacts-172318459814-us-east-1/image/a05fdf2c933f9f12422e714aec75a0d31240e7947af64fedc9c573991b431698.zip`.
  Exact source/version read-back passed `tools/verify_image.py --version 2.0`.
  Full four port-8080 hooks/environment/internet egress remain present; base is
  `3.1`. Both `2.0/36ec65d5-d2d0-4172-8e8b-71506d60d050` and
  `2.0/a3605699-1f60-4a9e-a56a-5eec5945fe96` logged real AWS sandbox validation PASS.
- Native `FactoryWorkflow` update succeeded, now pinned to `2.0`. Actual deployed
  definition preserves `ResultPath: $.terminationRequest`; AWS validation is
  OK/no diagnostics. AWS TestState with an explicitly mocked empty Terminate
  acknowledgment proved VM ID/outcome retention without a termination request.
  An initial partial strict Confirm mock was rejected for a missing required
  Endpoint field; did not relax validation. Instead, an actual read-only Confirm
  task on the already terminated retained VM passed, followed by actual
  Terminated → PublicResult → Outcome → JobFailed single-state checks, producing
  the token-free error/TERMINATED result. This is verification of the fix, not
  retroactive success of the failed execution or a new task attempt.
- Final account-wide listing confirms all five task VMs TERMINATED. Secret
  AWSCURRENT version remains unchanged; PRs #1 and #2 remain unmerged. No further
  task has been started. US-04 is still active: a newly authorized task execution
  must prove stable worker ok, post-task credential rediscovery and workflow
  Succeed with confirmed termination on corrected image `2.0`. US-05/US-06 were
  not run; no success gate claim or automatic task rerun.

### Operator-authorized US-04 attempt on corrected image 2.0

- Operator said “proceed” after the corrected image/workflow deployment report,
  authorizing one fresh US-04 execution, including agent-side publication and
  cleanup. No automatic rerun, merge, deployment change, or US-05/US-06 run.
- Read-only prechecks reconfirmed account `172318459814`, all five retained VMs
  TERMINATED, no running workflow executions, and unchanged OPEN/unmerged PRs
  #1/#2. Exact synthesized source/version verification passed for newly active
  image `2.0`; deployed Standard workflow is ACTIVE, pinned to `2.0`, retains
  `$.terminationRequest`, and has the accepted 1680-second timeout. AWS ASL
  validation returned OK with no diagnostics.
- Retain execution name before submission:
  `gate2-us04-787d7a7ff18d40ab9f264f22734e29c7`.
  Input is the same compact ordered repository/baseSha/task Job recorded above.
  Retry an uncertain submission only with this name/input. Further task attempts
  remain unauthorized; acceptance is pending the actual result.
- Started at service time `2026-10-31T23:44:49.853000+02:00`, execution ARN
  `arn:aws:states:us-east-1:172318459814:execution:software-factory-poc:gate2-us04-787d7a7ff18d40ab9f264f22734e29c7`.
  Retained VM `microvm-d79268a3-d531-3bdb-825b-15a20d2566ba`.
  Initial read-only history shows readiness, token creation and authenticated
  polling reached PendingWait. This is startup evidence, not task acceptance.
- Execution finished SUCCEEDED at `2026-10-31T23:46:02.656000+02:00`, after
  OK → Terminate → CleanupWait → Confirm → Terminated → PublicResult → Outcome
  → Success. Native task history proves exactly one RunMicrovm, one
  CreateMicrovmAuthToken, and one TerminateMicrovm. All three HTTP Tasks returned
  200 with exact pending, pending, ok bodies and reused one JWE, compared only in
  memory. No token was printed/saved or put in application logs/job input.
- Final workflow output is exactly the token-free object
  `{"termination":"TERMINATED","microvmId":"microvm-d79268a3-d531-3bdb-825b-15a20d2566ba","status":"ok"}`.
  Independent GetMicrovm confirms TERMINATED on image `2.0`, reason `Success.`;
  account-wide listing contains six retained VMs, all TERMINATED. No manual
  termination request, local lifecycle controller, or subsequent task launch.
- One Codex invocation completed actual sandbox-shell execution, editing, final
  approved tests, and separate commit/push/PR creation. Runtime log stream
  `2026/10/31[2.0]microvm-d79268a3-d531-3bdb-825b-15a20d2566ba` retains all six
  passing tests, actual exit code 0, independent unittest/operand-category
  acceptance exit code 0, and successful source/publication verification.
  Both AdditionTest and MultiplicationTest reuse the three method names and now
  pass the verifier. Scoped expiring runtime-role discovery passed before work
  and after verification; rediscovered credentials were unchanged (`rotated:
  False`), so expiration-driven rotation is not claimed.
- Published [PR #3](https://github.com/TeroTS/factory-smoke-test/pull/3), OPEN and
  unmerged, branch `factory/task-e2030274e69f42ed9e7aed40199033e2`, commit
  `14bd7bd93934281aa43407c6b2bd0c091eb8d025`. Read-only GitHub metadata/comparison
  confirms main at the pinned baseline, that SHA as merge base, one commit ahead
  and zero behind, and only calculator.py/tests/test_calculator.py changed.
  Inspected diff preserves all baseline addition tests and adds positive,
  mixed-negative, two-negative and zero-in-both-positions multiplication cases.
  PRs #1/#2 remain OPEN/unmerged with their original recorded heads.
- `make verify` passed format/lint/logging and 39 tests with one Docker-only host
  skip (40 discovered); branch-aware coverage remains 93%. `git diff --check`
  passed. No source, dependency, permission or deployment changes were made.
  `make preflight`/standalone `make synth` were not repeated for this operational/
  documentation-only completion; their corrected-source results are above, and
  the live image/workflow provided the required integration evidence. The
  existing Node 25/jsii support warning remains; no host toolchain was changed.
- US-04 is complete. This successful execution proves the success path, not the
  separate controlled-failure or hard-lifespan demonstrations. US-05/US-06 were
  not run; no full Gate 2 completion claim and no further attempts authorized.

## Tests Required
- Test-first worker setup/failure, real local Git/tests/HTTP/process behavior, evidence rejection, stable status, secret redaction, and sandbox validation.
- CDK synthesis assertions for native image/full configuration, private artifacts, scoped permissions/trust, connection marker, pinned version, timeout and absence of unapproved resources.
- Simulated workflow success, pending, worker/HTTP/invalid response failures, unknown launch, unexpected termination, cleanup errors and overall timeout.
- Validate ASL using AWS validation API and review scoped policies before deployment; local checks cannot establish live support/access.
- `make verify`, local ARM64 Docker preflight, and whitespace/documentation checks.
- Explicit live end-to-end run: new successful/active image, actual scoped runtime/model/sandbox, one agent-published meaningful tested unmerged PR, authenticated token reuse, confirmed termination and workflow Succeed.

## Not Now
- US-05 live controlled failure and US-06 separate hard-lifespan check.
- Any destructive changes or additional live task attempts outside explicit
  operator authorization; the reset and separately approved attempts are recorded above.
- Generic task policies/APIs, custom providers, extra agent loops/deadlines, automatic task reruns, local publication, workspace export, PR merges, provider/model changes, sandbox removal.

## Done When
- Native CDK owns Gate 2 infrastructure and newly validated pinned image; reviewed roles and validated workflow retain existing contracts and approved limits.
- One real Standard execution publishes a separately tested PR, reuses one auth token across status polls, and ends in Succeed only after confirmed TERMINATED, without a local controller.
- Local and live evidence is recorded here; unconfirmed cleanup/builds/process exit alone never count as success.

## Completion Summary
- US-04 passed on corrected native-CDK image `2.0`: one Standard execution
  published tested unmerged PR #3, reused one token for authenticated status
  polling, and succeeded only after workflow-confirmed VM termination.
- All six retained VMs are TERMINATED; publication remains available in GitHub
  and runtime evidence in CloudWatch. No local controller or manual cleanup was
  needed. The secret and earlier PRs were not modified by this attempt.

- Operator subsequently authorized deleting the obsolete local Gate 1 template.
  Removed `infra/gate1.json` and its README/plan references, then the obsolete
  Gate 1 manual image request `image/create.json` identified by the operator
  (no callers/references found). Native Gate 2 sources and synthesized templates
  were preserved. Deletion checks and `git diff --check` passed; runtime tests
  were not repeated for this cleanup. No AWS changes were made.

## Next Slice Recommendation
- Start with [US-07: Run the worker on a maintained image Python runtime](../../SPEC.md#us-07-run-the-worker-on-a-maintained-image-python-runtime).
  Evaluate Python 3.13 package availability on Amazon Linux 2023 ARM64 before
  selecting a version; align interpreter selection, startup/subprocesses,
  compatibility settings, tests, and documentation. Run `make verify` and
  `make preflight`; a new image requires validation and separate deployment
  authorization. Do not treat the candidate version as already accepted.
- Deliver one story per slice in the specification's order: US-08 boto3 access,
  US-09 staged execution, US-10 Codex configuration, US-11 repository-agnostic
  jobs, US-12 evidence simplification, US-13 CDK lifecycle simplification, and
  US-14 generic/end-to-end tests. Reconcile affected decisions, vocabulary, and
  contracts before implementation; requirements are in [SPEC.md](../../SPEC.md)
  and [the follow-up plan](../plans/todo.md), not proof of implementation.
- Keep US-04's completed evidence intact. After US-07–US-14, return to US-05's
  nonexistent-base-SHA controlled failure and US-06's independent two-minute
  hard-lifespan check. Both live demonstrations, additional task runs, deployment,
  and destructive resource changes require separate operator authorization;
  preserve the GitHub-token secret and existing unmerged PRs.
