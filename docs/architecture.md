# Current implementation

The factory turns one coding task into an independently verified, AI-reviewed,
merged GitHub PR inside an AWS Lambda MicroVM, then confirms VM termination.
This describes repository code, not a live deployment inventory or proof of
end-to-end provider behavior.

## Components and boundaries

| Component | Responsibility | Source |
| --- | --- | --- |
| Issue receiver | Authenticate signed `issues/opened` deliveries, capture title/body, resolve the configured target, and insert a pending Job. | [receiver](../src/receiver/handler.py) |
| Job queue | Store Jobs in enqueue-time order and retain active blockers. | [contracts](contracts.md#receiver--dynamodb-accept-a-queued-job) |
| Dispatcher | Check for active work, claim the oldest returned pending Job, refresh/persist its baseline, and start one execution. | [dispatcher](../src/dispatcher/handler.py) |
| Standard workflow | Launch a pinned VM, authenticate status polls, request termination, confirm shutdown, and complete successful queued Jobs. | [workflow](../src/infra/workflow.py) |
| Lifecycle server | Serve snapshot-safe hooks on port 8080; start one asynchronous worker and expose `pending`, `ok`, or `error`. | [server](../src/worker/server.py) |
| Job worker | Coordinate repository preparation, separate Codex stages, independent verification, publication, and merge confirmation. | [worker](../src/worker/task.py) |
| Image and deployment | Package runtime tools and immutable factory skills; define storage, image, and workflow stacks. | [CDK app](../src/infra/app.py), [Dockerfile](../image/Dockerfile) |

[System context](diagrams/system_context.d2) shows the boundaries.
[The rendered runtime view](diagrams/service-architecture.html) focuses on direct
execution; queued ingress is shown in the system context.

HTTP interfaces and Job payloads are defined in [OpenAPI](../openapi.yaml).
AWS submission mappings and target-repository script requirements are in
[contracts](contracts.md). Queue record semantics are in [data model](data-model.md).

## Runtime flows

### Signed issue to queued execution

1. The receiver validates the original-body HMAC and exact repository authorization.
   Only issue opening requests work; comments and later edits are not fetched.
2. It stores a bounded Job with a receipt-time target SHA. `202` acknowledges the
   insert, not execution or completion; deliveries are not deduplicated.
3. Filtered DynamoDB Streams notifications invoke the dispatcher. It checks for
   `IN_PROGRESS`, then selects the oldest returned `PENDING` Job and claims it.
4. The dispatcher resolves the latest deployment-configured target and persists
   that exact SHA in `job.baseSha` before starting the workflow. Preparation or
   uncertain startup failure retains the claim, with no fallback or redispatch.

Claims are non-atomic and queries read only one page. One dispatcher at a time,
work fitting the first query page, and no overlapping direct tests remain
operator assumptions, not deployed serialization guarantees.

### Implementation to merged PR

1. The worker validates authorization, discovers AWS credentials through the SDK,
   retrieves the scoped GitHub token, and clones the pinned baseline onto a unique
   task branch. Executable `./scripts/setup` and `./scripts/verify` prepare and
   verify the baseline.
2. A fresh ephemeral Codex implementation process edits and verifies, but does not
   commit, push, publish, or merge. The worker independently verifies and commits
   all candidate changes.
3. Fresh Standards/Spec review emits candidate-bound structured PASS/FAIL evidence.
   Valid FAIL findings permit repair, independent verification, another worker
   commit, and fresh review, within at most three review passes. Invalid output,
   reviewer failure/mutation, or exhaustion fails closed.
4. Final independent verification and unchanged-candidate checks precede a separate
   publication process that pushes the reviewed commit and opens one matching PR.
5. An independent worker gate checks candidate/publication identity and merge-commit
   eligibility. A fresh process requests an exact-head, non-admin merge honoring
   GitHub protection. The worker independently confirms the matching PR is MERGED.

[Factory skills](factory-skills.md) documents the canonical bundle and capability
limits. Review is heuristic; GitHub is the merge authority. The worker does not
inspect merge parents or current target ancestry, and the target may advance.

### VM lifecycle and queue completion

The workflow accepts direct `[Job]` tests or dispatcher `{jobs: [Job], queue}`
submissions. Direct tests retain their supplied SHA and never write queue state.
It launches one pinned image version using the execution name as the VM client
token, waits five seconds between startup checks, and creates one 30-minute
port-8080 token. It polls `/status` every ten seconds while pending.

On worker success or caught post-launch failure with a known VM ID, it requests
termination and checks every five seconds until `TERMINATED`. Only worker `ok`
plus confirmed termination permits the queued item to become `COMPLETED` and
release the next Job. Failures leave it `IN_PROGRESS`.

The workflow timeout is 28 minutes; VM maximum lifespan is 30 minutes. Timeout
and manual `StopExecution` bypass normal cleanup. Unknown launch IDs and
unconfirmed termination require manual inspection; lifespan is only a fallback.
See the [lifecycle sequence source](diagrams/key_flow_job_lifecycle.d2) and
[image-build sequence source](diagrams/key_flow_image_build.d2).

## Deployment and safe changes

Native Python CDK defines `FactoryArtifacts`, `FactoryImage`, then
`FactoryWorkflow` for the fixed account/Region in the app. The workflow requires
an explicit verified image version; it never silently selects an older active
build. [Deployment topology](diagrams/deployment_topology.d2) separates these
resources from runtime processes. D2 diagram sources are checked in; render them
locally with D2 when a visual export is needed.

Use [running](running.md) for local commands and operator tools, and
[operations](operations.md) for trust, configuration, logs, and failure handling.
No full local application server is provided: Floci exercises ingress and entry
states, with simulated MicroVM/HTTP lifecycle tasks.

When changing a boundary, update OpenAPI or the non-HTTP contracts, corresponding
receiver/dispatcher/worker/workflow tests in `src/tests/`, and these flow diagrams.
Run focused tests, `make verify`, and `make synth` for infrastructure changes;
image/sandbox changes additionally need `make preflight`. Local results do not
establish live model behavior, GitHub policy enforcement, or concurrency safety.
