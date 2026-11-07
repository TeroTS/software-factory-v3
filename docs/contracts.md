# Interface Contracts


AWS-owned submission mappings and the target-repository script boundary for the
software factory PoC. Our HTTP interfaces and shared payload schemas are defined
**only in [openapi.yaml](../openapi.yaml)**.

## Operator → Step Functions: start a job

**Operation:** AWS `StartExecution`, called through the AWS CLI or SDK in Gate 2.

Transport: AWS API through CLI or SDK. Direction: request/reply.
This is an AWS-owned interface, not an endpoint in our worker server.
Full AWS contract: [StartExecution](https://docs.aws.amazon.com/step-functions/latest/apireference/API_StartExecution.html).

### Request

| Field | Type | Value / meaning |
| --- | --- | --- |
| `stateMachineArn` | string | ARN of the deployed Step Functions Standard state machine. |
| `name` | string | Operator-chosen execution name; also used as the VM launch client token. |
| `input` | string | JSON serialization of `openapi.yaml#/components/schemas/WorkflowSubmission`: an array containing exactly one Job, not an embedded object. |

### Response

Successful AWS execution acknowledgement:

| Field | Type | Meaning |
| --- | --- | --- |
| `executionArn` | string | ARN identifying the execution. |
| `startDate` | timestamp | Execution start time; representation follows the AWS API/client. |

This acknowledges execution startup, **not task success**. Retry an uncertain
`StartExecution` with the same name and input, not a new job. AWS owns failure
responses; no custom submission-error body is defined here.

Direct starts are independent Step Functions tests: they retain the explicitly
supplied pinned `baseSha`, have no queue reference, and perform no terminal queue
writes. Unlike queued Jobs, their baseline is not refreshed. The operator starts
them only when the queue is empty and no Job is active, and submits no webhook
Jobs until the direct execution's MicroVM termination is confirmed.

## Dispatcher → Step Functions: start a queued Job

The Dispatcher calls the same AWS `StartExecution` operation with
`input = JSON.stringify({jobs: [Job], queue: {PK: "JOBS", SK: enqueueTimestamp}})`.
Before startup, the Dispatcher unconditionally claims the oldest returned pending
Job as `IN_PROGRESS`, resolves only the deployment-configured target in the
authorized repository using the configured repository-scoped GitHub secret, and
persists that exact SHA in the claimed item's existing `job.baseSha`. It submits
the same SHA without changing the Job payload shape or rewriting other pending
Jobs. Resolution or persistence failure starts no workflow; an uncertain start
also retains the active blocker without retry or redispatch.

The workflow keeps `queue` separately from the worker Job payload. Only
independently verified merged completion and confirmed MicroVM termination permit
that same composite key to become `COMPLETED`. The next selected Job resolves its
execution baseline after this completion, including the preceding merge in the
latest configured target. Direct testing continues to use only `[Job]`.

## Receiver → DynamoDB: accept a queued Job

A webhook `202` means DynamoDB accepted a conditional insert, not workflow
startup or completion. The item has string `PK = JOBS`, timestamp-only string
`SK` in `YYYY-MM-DDTHH:mm:ss.ffffffZ` UTC format, `status = PENDING`, and nested
`job` with the existing Job fields. A timestamp collision rejects the new Job
without replacing the existing one; there is no collision retry. The stored
`job.baseSha` is a receipt-time baseline, not the final queued execution baseline;
it is refreshed only when that Job is claimed.

`NEW_IMAGE` Streams notify the Dispatcher for `INSERT/PENDING` and
`MODIFY/COMPLETED|FAILED`, using only the new-image status. Each invocation makes
one decision from current table contents: strongly consistent, ascending,
one-page base-table queries check active Jobs, then select the oldest returned
pending Job. No evaluated-item limit of one or pagination is used.

The operator must confirm single-dispatcher execution, a demonstration queue
fitting the first DynamoDB query page (1 MB), and non-overlapping direct tests.
These are operating assumptions, not deployed concurrency guarantees: claims
remain non-atomic and one-page reads can miss Jobs beyond that page.
Implementation, verification, exhausted review, publication, merge, baseline
preparation, startup, workflow, timeout, manual-stop, and cleanup failures retain
`IN_PROGRESS`. New enqueues, duplicate delivery, and unrelated notifications
cannot bypass that blocker under these assumptions. No failed-Job release,
automatic retry, reset, skip, or recovery is introduced.

Deployment removes the old SQS queues and Pipe. Drain accepted work before
applying the stack update; this change does not migrate existing queue messages.

## Gate 1 submission: Operator → Lambda MicroVMs

**Operation:** AWS `RunMicrovm`, called manually instead of starting Step Functions.

Transport: AWS API through CLI. Direction: request/reply.
Authoritative AWS launch parameters and responses are in the
[copied launching reference](aws-lambda-microvms/microvms-launching.md#microvms-launching-run).

### Request

Supply JSON serialization of `openapi.yaml#/components/schemas/Job` as the AWS
API's `runHookPayload`. The resulting AWS-to-worker request envelope is defined
by `openapi.yaml#/components/schemas/RunHookRequest`, not repeated here.

### Response

AWS's launch response supplies `microvmId` and `endpoint` dynamically. It
acknowledges launch, not task success or confirmed termination.

## HTTP contract source

The signed GitHub receiver's `/` POST boundary is also defined in
[openapi.yaml](../openapi.yaml), under its own Function URL server.

## Worker / Codex → authorized target repository: setup and verification

Transport: local executable scripts in the checked-out repository.
Direction: request/reply (process invocation and exit result).
Authoritative contract: this section.

### Request

| Caller → receiver | Operation | Required ordering |
| --- | --- | --- |
| Worker → repository `./scripts/setup` | Execute from the pinned repository root. | After checkout, before baseline verification and Codex. |
| Worker → repository `./scripts/verify` | Execute from the pinned repository root. | After successful setup, before Codex. |
| Codex → repository `./scripts/verify` | Execute from the working repository root. | Passing final verification is required before commit, push, and PR creation in the same agent invocation. |
| Worker → repository `./scripts/verify` | Independently execute from the working repository root. | After the agent invocation, before reporting automated completion. |

No additional Job fields or script-specific JSON request payload are defined.
Setup and test tooling belong to the target repository scripts, not hardcoded
factory test commands or calculator acceptance checks. Scripts use executable
files with an appropriate interpreter/shebang; the worker invokes each path
directly, without a shell wrapper, from the repository root. Scripts own their
dependency installation and must propagate failures as nonzero exit codes.

### Authorization configuration

The worker requires these environment settings supplied by infrastructure,
not by the Job or cloned repository:

| Setting | Meaning |
| --- | --- |
| `AUTHORIZED_REPOSITORY` | One trusted GitHub repository in `owner/name` format; Job `repository` must match exactly. Missing or invalid authorization fails closed before credential retrieval or work. |
| `PR_TARGET_BRANCH` | Nonblank target branch for PR publication and merged-state confirmation; Git validates the branch name before scripts or Codex run. The unique task branch must differ from this target. |
| `GITHUB_SECRET_ARN` | Existing deployment-controlled secret holding the GitHub token scoped to that authorized repository. Job input cannot select a secret. |

Native CDK image parameters `AuthorizedRepository` and `PrTargetBranch` supply
the first two settings. Their defaults retain the original demonstration
repository and `main`; changing configuration does not grant token permissions.
Another live repository requires explicit trust/scoping review and separately
authorized configuration/deployment. Repository scripts execute with access to
the runtime role and GitHub token; Codex's sandbox does not remove that exposure.
No allowlist, multi-repository token, or Job-supplied authorization is defined.

### Response

| Result | Meaning |
| --- | --- |
| Actual process exit code `0` | The script passed; verification alone does not establish task correctness or test adequacy. |
| Nonzero exit code | Script failure. |
| Available stdout/stderr | Command/test output retained with secret redaction; not returned in the status response. |

### Interface Constraints

| Constraint | Requirement |
| --- | --- |
| Script availability | Both paths must exist and be executable. Missing or non-executable scripts fail explicitly. |
| Worker-side failure | Setup, baseline verification, or independent post-implementation verification failure is terminal. Setup/baseline failure prevents agent work; post-implementation failure prevents review and publication. |
| Agent-side test failure | Implementation diagnoses, fixes, and reruns failing verification within its invocation; no whole-Job rerun. Validated review FAIL permits repair and fresh review within the existing three-pass Job budget; invalid evidence or reviewer process failure is terminal. Exhaustion leaves the Job blocking. |
| Stage ordering | Implement → independent verification → worker-created local commit → fresh review (bounded repair if required) → final independent verification and unchanged-candidate check → publication of that existing commit → independent worker gate → fresh authorized merge invocation → independent GitHub merged-PR confirmation. Each agent stage uses a distinct ephemeral Codex process and an explicit canonical skill path. Implementation returns without commit/push/PR/merge; publication grants no merge permission. |
| Review evidence | Review covers the complete committed change relative to pinned `baseSha`, including staged, unstaged, and untracked work staged by the worker. A fixed output schema requests PASS/FAIL and required findings (Standards/Spec, location, problem, correction) through a new last-message file outside the repository. Missing, malformed, contradictory, or structurally invalid output fails closed. PASS has no findings; FAIL requires findings. Commit, branch, and Git status checks reject persistent reviewer mutations. The managed workspace-write policy overrides read-only CLI sandbox selection; review remains read-only by instruction and Git checks. |
| Completion | Independent post-implementation verification with actual exit code `0`, a valid fresh review PASS, nonempty committed changes descended from pinned `baseSha`, clean workspace, matching reviewed/local/remote/PR task heads, and exactly one matching PR targeting the configured branch are required. The gate confirms repository/origin, clean local candidate, remote/PR head, PR identity, and merge-commit eligibility before authorization. Repository metadata is read once here and once for final scoped access. GitHub enforces branch protection and required checks on the non-admin, exact-head merge request; the worker does not duplicate its check policy or parse check-status output. No required checks is eligible; denied or unresolved merges fail closed. Publication and merge cannot edit source or create a replacement commit. Request only `gh pr merge <number> --repo <repository> --merge --match-head-commit <verified-head>`, without admin/auto/squash/rebase or target pushes. Completion independently confirms GitHub reports the expected PR as MERGED with a nonempty mergedAt, matching repository, target, task branch, and reviewed head. GitHub is trusted as the merge authority; merge metadata, parent structure, and current target ancestry are not inspected. After ambiguous publication or merge, inspect existing state without retrying or duplicating publication. A lost merge response can succeed only with independent confirmation of that matching merged PR; enabling auto-merge is insufficient. A source ref automatically deleted after merge is accepted only with the same bound merged PR head; a changed surviving ref is rejected. Final scoped GitHub repository access is checked. Failed cleanup still blocks queue completion. PR sections are Summary, Evidence, Review, and Merge Danger with actual verification/review evidence. The target may advance during the Job; its current head need not equal `baseSha`. |
| Automated limits | AI review is heuristic, not proof of task correctness or test adequacy. Git checks detect persistent candidate changes, not edits later undone or ignored files outside the candidate. Repository scripts retain their existing credential access. Success trusts GitHub reports the matching reviewed PR as merged, not a proof of code correctness or compatibility with target advancement. The worker does not independently detect an unexpected merge method or a later target-history rewrite removing the merge. GitHub branch protection remains authoritative; no factory-required checks, human-review gate, administrative bypass, automatic conflict repair, or merge-policy change is introduced. Local simulations do not establish live GitHub policy enforcement or provider behavior. |
| AWS credentials | Use the AWS SDK credential provider chain and SDK/service errors for missing, expired, or denied credentials. The worker registers discovered values for log redaction; it does not export CLI credentials, validate account/role or expiry itself, or perform a final rotation check. Deployment/operator configuration is responsible for selecting the intended credentials. |
| Repository authorization | Read `openapi.yaml#/components/schemas/Job`. |

## Sequence diagrams

| Exchange | Rendered diagram |
| --- | --- |
| Image creation: one snapshot per image version | [Image-build sequence](diagrams/key_flow_image_build.svg) |
| Job launch, polling, termination: reuse the pinned snapshot | [Job-lifecycle sequence](diagrams/key_flow_job_lifecycle.svg) |
