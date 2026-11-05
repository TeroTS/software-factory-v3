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
| `PR_TARGET_BRANCH` | Nonblank target branch for publication; Git validates the branch name before scripts or Codex run. The unique task branch must differ from this target. |
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
| Worker-side failure | Setup, baseline verification, or independent final verification failure is terminal; setup/baseline failure prevents Codex invocation. |
| Agent-side test failure | Codex diagnoses, fixes, and reruns failing verification within its one invocation; no agent restart or whole-Job rerun. |
| Completion | Independent final verification with actual exit code `0`, nonempty committed changes descended from pinned `baseSha`, clean workspace, matching local/remote/PR task heads, and exactly one open unmerged PR targeting the configured branch are required. The target branch may advance during the Job; its current head need not equal `baseSha`. |
| Automated limits | The worker does not check calculator behavior, preserve baseline ASTs, or validate command chronology. It instructs Codex to verify before publication, but independent verification afterward does not prove that ordering or protect against weakened tests. Human GitHub review judges correctness and test adequacy; success does not authorize merging or establish compatibility with the current target branch. Compatibility remains a merge-time check; no GitHub merge-policy change is implied. |
| Repository authorization | Read `openapi.yaml#/components/schemas/Job`. |

## Sequence diagrams

| Exchange | Rendered diagram |
| --- | --- |
| Image creation: one snapshot per image version | [Image-build sequence](diagrams/key_flow_image_build.svg) |
| Job launch, polling, termination: reuse the pinned snapshot | [Job-lifecycle sequence](diagrams/key_flow_job_lifecycle.svg) |
