# Follow-ups

## Upgrade the image's Python runtime

- Python 3.9 is the current Amazon Linux package choice, not a MicroVM requirement.
  It is upstream end-of-life and limits maintained dependency choices.
- Evaluate Python 3.13 as a candidate; first verify versioned package availability
  on Amazon Linux 2023 ARM64. The version is proposed, not an accepted decision.
- Install and explicitly select the newer interpreter in `image/Dockerfile`,
  including worker startup, subprocesses, and demonstration test execution.
  Update compatibility settings and relevant tests/documentation consistently.
- Run `make verify` and `make preflight`; build and validate a new image before
  deployment. Deployment requires separate approval; approved worker dependency
  additions are listed below.
- Record implementation progress and verification evidence only in
  [the current slice](../slices/current-slice.md).

## Approved worker refactoring

The operator approved the following dependency additions and refactoring scope.
Plan against a Python 3.13 image; runtime installation and compatibility still
require verification. Approval does not authorize AWS deployment or live tasks.

### 1. Replace custom HTTP handling with Flask + Waitress

- In `worker/server.py`, replace `BaseHTTPRequestHandler` routing and
  `ThreadingHTTPServer` serving with Flask routes and Waitress.
- Keep `Runtime` process supervision intact and use one server process with
  threads, preserving the single in-memory Job state.
- Preserve exact compact status JSON, empty hook responses, existing HTTP error
  mappings, request limits/timeouts, asynchronous startup, stable terminal
  status, and suppression of sensitive request/error logging.
- Update `tests/test_server.py` with route tests; retain real HTTP and
  process-group termination tests. Estimated complexity: medium.

### 2. Replace selected AWS CLI calls with boto3

- In `worker/task.py`, replace STS identity and Secrets Manager calls with boto3
  clients, retaining the expected account/role guard before secret access and
  nonempty token validation.
- Initially retain CLI credential export/expiration checks and required AWS CLI
  availability. Do not rely on private botocore expiration attributes or assume
  SDK credential refresh also refreshes the separate Codex process.
- Configure bounded API request behavior and sanitized failure reporting;
  introduce no automatic Job retries or secret-bearing debug logs.
- Update `tests/test_worker.py` using botocore `Stubber`; retain identity,
  expiration, invalid-secret, and redaction regressions. Verify actual MicroVM
  credential-provider compatibility. Estimated complexity: medium.

### 3. Centralize consumed boundary validation with Pydantic

- Separate AWS envelope decoding from shared Job validation, removing the
  synthetic-envelope JSON round trip in `worker/task.py`.
- Use strict Pydantic models for Job fields and consumed command-event shapes;
  forbid extra Job fields, preserve byte/whitespace constraints and callers'
  dictionary interfaces, and tolerate unrelated valid Codex event types.
- Never log raw validation errors containing input values. Keep chronology,
  source-preservation, and publication policy in `worker/evidence.py`.
- Update malformed-input/event tests in `tests/test_server.py` and
  `tests/test_worker.py`. Estimated complexity: low–medium.

### Integration and scope

- Pin approved runtime dependencies reproducibly, include their installation
  manifest in CDK source staging (`infra/stacks.py`), and update the Dockerfile,
  relevant tooling configuration, tests, and documentation consistently.
- Deliver the refactorings separately, running focused tests and `make verify`;
  run `make synth` and `make preflight` for image/staging changes. Validate a new
  image before any separately authorized deployment/live task.
- Keep standard-library subprocess streaming, locks/process groups, Git/gh
  integration, AST verification, redaction, and factory-specific evidence rules.
  Do not add queues, a full Codex event model, or a broad framework rewrite.
