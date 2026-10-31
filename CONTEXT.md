# Domain Vocabulary

Definitions reflect the accepted [PoC decisions](DECISIONS.md). This vocabulary governs naming, not behavior.

## Canonical Terms

### Software factory PoC

The demonstration that turns one coding task into a tested GitHub PR inside an isolated MicroVM, then confirms VM termination. Both gates are required; this is not a production software factory.

### Demonstration repository

The controlled synthetic repository, `TeroTS/TeroTS-factory-smoke-test`, where the baseline is prepared and both gates publish their task PRs.

### Demonstration baseline

The tested calculator fixture committed before agent execution and pinned to the same commit SHA for both gates. It is not the repository's original README-only commit.

### Agent task

The requested source/test change: add `multiply(a, b)` and positive, negative, and zero operand tests while preserving addition behavior. The task describes the change, not the whole job.

### Job

One execution supplied with the demonstration repository, pinned base SHA, and agent task. It includes repository setup, agent work, publication, status reporting, and VM cleanup; the PoC runs only one job at a time.

### Publication

The agent-side commit, task-branch push, and PR creation inside the MicroVM after passing tests. Publication does not include merging the PR.

### Gate 1

The execution-feasibility gate: manually launch a VM, complete the real agent task through publication, then manually terminate and confirm termination. It must pass before orchestration resources are created.

### Gate 2

The orchestration gate: reuse Gate 1's proven image and worker under Step Functions to demonstrate a successful job and controlled failure with confirmed cleanup, including authenticated status polling and token reuse.

### Controlled failure

The Gate 2 demonstration using a base SHA confirmed absent from the repository. Checkout fails before Codex starts, no PR is published, and orchestration reports failure after confirming VM termination; the cause remains in CloudWatch logs.

### Hard-lifespan check

The separate non-publishing, two-minute VM run that verifies automatic termination without an explicit termination request. It uses the nonexistent-base-commit job and requires no agent invocation or model call.
