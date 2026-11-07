# AWS Lambda MicroVM Software Factory PoC

**Status:** Implemented in repository code. This document does not establish
current live deployment state or end-to-end AWS/model/GitHub verification.

## What it does

The factory turns a small coding task into a tested, AI-reviewed, merged GitHub
pull request inside a temporary AWS Lambda MicroVM, then confirms shutdown.
The worker authorizes merge only after independent checks; this is no longer a
PR-only workflow waiting for a human merge.

## Main workflow

1. **Submit a task.** A signed issue-opening delivery from the authorized GitHub
   repository captures title/body and enters the DynamoDB queue. HTTP 202 means
   queued, not completed. Non-overlapping direct operator tests remain available.
2. **Claim the next Job.** DynamoDB Streams invokes the dispatcher. If no Job is
   active, it claims the oldest returned pending Job, resolves the latest configured
   target branch, and persists that SHA as the execution baseline before startup.
3. **Start the workspace.** Step Functions launches a pinned ARM64 image. The
   worker retrieves the scoped GitHub token, clones the baseline onto a unique
   branch, and runs the repository's setup and verification scripts.
4. **Implement and review.** A fresh Codex process edits and tests. The worker
   independently verifies and commits, then runs a separate Standards/Spec review.
   Valid FAIL findings allow repair and fresh review within three review passes.
5. **Publish and merge.** After passing review and final independent verification,
   a separate process publishes the reviewed commit and opens a PR. An independent
   worker gate authorizes a fresh exact-head merge honoring GitHub protection.
   The worker independently confirms that matching PR is MERGED.
6. **Clean up before releasing work.** Step Functions polls status, terminates
   the VM, and confirms TERMINATED. Only merged success plus confirmed shutdown
   marks the queued Job COMPLETED, allowing the next claim to use the latest target.

```text
Signed issue → Receiver → DynamoDB → Dispatcher → Step Functions → MicroVM
                            ↑                         │
                            └── COMPLETED only after ─┘
                                merged PR + TERMINATED
```

## Where things live

- **GitHub:** authorized source repository, task branch, and merged PR.
- **DynamoDB:** enqueue-ordered Jobs and active blockers.
- **CloudWatch:** redacted worker/stage output and ingress logs.
- **Secrets Manager:** deployment-controlled repository-scoped GitHub token.
- **Amazon Bedrock:** pinned model accessed with AWS SDK/runtime credentials.
- **Native Python CDK:** artifact storage, image, and workflow/ingress resources.

## Limits and verification

Keep one dispatcher at a time, a queue fitting the first query page, and no
concurrent direct tests. Selection/claims do not enforce serialization. Failures,
exhausted review, timeout, stop, and unconfirmed cleanup retain IN_PROGRESS; there
is no automatic retry, reset, skip, or failed-Job release. Timeout/stop bypasses
normal cleanup; a 30-minute VM lifespan is the fallback.

AI review is heuristic and GitHub is trusted as merge authority. Local tests and
Floci simulations do not prove live model behavior, GitHub policy enforcement,
or deployed concurrency safety.

See [current architecture](../../architecture.md), [running](../../running.md),
[operations](../../operations.md), and [contracts](../../contracts.md).
