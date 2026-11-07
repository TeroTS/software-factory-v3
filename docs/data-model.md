# Job records and completion evidence

## Scope and conventions

This describes logical records at factory boundaries, not a database schema.
Exact payload definitions remain in [OpenAPI](../openapi.yaml); AWS and queue
mappings remain in [contracts](contracts.md). [Model diagram](diagrams/models/job_models.d2).

A Job contains exactly `repository`, `baseSha`, and `task`. The repository must
exactly match deployment authorization, the baseline is a full 40-character hex
commit SHA, and the task is nonblank. Serialized worker payloads are limited to
16 KiB of UTF-8. Jobs contain no credentials or target-branch configuration.

## Authoritative records

| Record | Required information | Owner and semantics |
| --- | --- | --- |
| Job | Repository, baseline SHA, task | Receiver captures issue title/body; direct operator tests supply their own pinned Job. The dispatcher refreshes only a claimed queued Job's baseline. |
| Queue item | Composite key, status, nested Job | Receiver inserts; dispatcher claims and persists the execution baseline; workflow records completion. The partition is `JOBS`, and the ordering key is a UTC enqueue timestamp with six fractional digits. |
| Workflow execution | AWS execution identity, singleton Job, optional queue reference | Step Functions owns execution state/history and the launched VM identity. Queue reference is separate from the worker Job. |
| Review result | Candidate head, PASS/FAIL verdict, required findings | Worker validates fresh structured output from a separate review process. PASS has no findings; FAIL requires Standards/Spec findings. See [review schema](../src/worker/review.py). |
| GitHub PR | PR number, repository, target/task branches, reviewed head, merged state/time | GitHub owns publication and merge state. Worker requires exactly one matching PR, first OPEN and finally MERGED. |

The queue timestamp is receipt metadata, not GitHub issue creation time or a
unique delivery identity. Timestamp collisions reject the new insert and preserve
the existing item. Duplicate webhook delivery can create another billable Job.
No issue ID, author record, delivery-deduplication ledger, or extra audit fields
are stored by the factory.

## Relationships and constraints

- One queue item contains one Job. A dispatched workflow retains that item's exact
  composite key; direct executions have no queue item or terminal queue writes.
- A successful execution uses one VM and one matching reviewed PR. VM launch
  idempotency uses the execution name. Queued starts let AWS choose the name;
  direct tests use the retained operator-selected name.
- Queued `baseSha` initially records receipt-time target state. After claim, its
  persisted value is the execution baseline; later completion does not rewrite
  other pending Jobs. The next claim resolves its own latest target SHA.
- The reviewed head must match the clean local candidate, surviving remote task
  ref, and PR head, with nonempty changes descended from the pinned baseline.
  GitHub may delete the source ref after merge; the same bound merged PR suffices.
- One active Job is an operating assumption. One-page reads and non-atomic claims
  do not guarantee it under concurrent dispatch or a larger queue.

## Lifecycle and update/delete posture

The implemented path is `PENDING → IN_PROGRESS → COMPLETED`. Completion requires
independent GitHub merged-PR confirmation and service-confirmed VM termination.
Preparation, startup, implementation, verification, review, publication, merge,
workflow, timeout, manual-stop, and cleanup failures retain `IN_PROGRESS`.

Stream filters also accept `MODIFY/FAILED`, but the factory does not write FAILED
or provide failed-Job release, retry, skip, reset, or recovery. There is no
application deletion/expiry path for queue items. Do not equate workflow terminal
state or an accepted termination request with queue completion.

## Derived views and evidence

`GET /status` projects in-memory worker-process state as `pending`, `ok`, or
`error`, without task details, diagnostics, or artifacts. It is pending before
startup and while work runs; process exit zero means verified merged completion.
The view is not a persistent receipt or a restartable Job checkpoint.

Workflow output exposes VM ID, status, and confirmed termination on the normal
terminal path. Restricted execution history and redacted CloudWatch logs support
operator inspection, not a public evidence API. Code, task branches, and PRs live
in GitHub. Review results are temporary files outside the repository; logged
verdicts/findings are evidence, not durable workflow-resume state.

Any alternative storage mapping must preserve exact queue references, enqueue
ordering, persisted execution baselines, and completion-after-cleanup semantics;
it must not silently introduce delivery deduplication or failure release.
