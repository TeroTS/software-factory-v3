# Follow-ups

## 1. Upgrade the image's Python runtime

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

## 3. Split `worker/task.py` execution into focused stages

- Extract `prepare_environment()`, `prepare_repository()`, `invoke_agent()`, and
  `verify_completion()` so `execute()` coordinates the workflow and final
  credential check rather than implementing every stage inline.
- Use explicit inputs and return values; reuse existing helpers without adding
  a class, generic workflow framework, or dependencies.
- Preserve execution order, validation, secret redaction, failure propagation,
  and the single Codex invocation with independent completion verification.
- Update relevant worker tests and run focused tests plus `make verify`.

## 4. Move static Codex settings into factory-owned configuration

- Move the model, provider, sandbox mode/network access, web search, approval,
  and shell environment policies from `worker/task.py` into a factory-owned
  Codex configuration baked into the image; retain the accepted settings.
- Keep invocation-specific arguments in Python: `exec`, `--json`,
  `--ephemeral`, repository path, `.git` access, and stdin prompt input.
- Verify configuration loading and precedence with the pinned Codex version
  before choosing the file location. Ensure cloned repository configuration
  cannot override approved constraints; retain explicit enforcement where needed.
- Add regression tests for effective settings and invocation arguments. Run
  focused tests, `make verify`, and `make preflight` to verify image integration.

## 5. Make all worker code repository-agnostic

- Remove Gate 2 smoke-test repository assumptions from all code under `worker/`,
  including repository validation, task prompts, file restrictions, test commands,
  source-preservation checks, and completion verification. Use existing
  configuration conventions for necessary repository-specific values.
- Keep repository authorization configuration-controlled; do not replace the
  fixed repository guard with unrestricted repository access. Preserve scoped
  credentials, redaction, sandbox enforcement, and explicit failure handling.
- Run `./scripts/setup` and `./scripts/verify` inside the target repository.
  Require passing baseline verification before Codex, passing final verification
  before agent publication, and independent verification afterward. Missing or
  failing scripts must fail explicitly. The factory's own Make-based development
  workflow remains unchanged.
- Remove calculator-specific acceptance checks entirely, as detailed in item 6;
  retain generic checks for nonempty committed changes, baseline ancestry, clean
  workspace, remote branch/PR consistency, and actual exit codes within one Codex
  invocation. Instruct the agent to verify before publication, but do not claim
  automated proof of that chronology after removing event-sequence validation.
  Keep the smoke-test repository as a demonstration fixture, not a worker assumption.
- Update accepted decisions/specification and affected interface documentation
  before implementation to reflect the repository script contract and replacement
  of calculator-specific validation. Review repository scripts' access to runtime
  credentials; do not silently weaken acceptance or security checks.
- Update affected callers, configuration, image integration, tests, fixtures, and
  documentation. Cover another authorized repository, unauthorized input, missing
  or failing scripts, and publication/verification failures without adding a
  generic workflow framework or unnecessary dependencies.
- Run focused tests, `make verify`, and `make preflight`; retain the two-gate order
  and separate deployment authorization.

## 6. Remove demonstration-specific evidence validation

- Remove `preserve_nodes()`, `verify_source()`, and `verify_events()` and their
  callers and tests. Remove the now-unused `command_words()` helper as well.
- Move `verify_pr()` to `worker/task.py`, preserving PR consistency validation
  and its regression tests; configure repository-specific values per item 5.
- Delete `image/acceptance.py` and remove its invocation and path constant from
  `worker/task.py`, plus associated image/build references and documentation.
  Do not relocate or retain calculator-specific acceptance checks elsewhere.
- Update `tests/test_worker.py` and any other acceptance callers/tests: remove
  calculator-acceptance-specific cases, but retain generic worker coverage for
  verification failures, credential handling, clean commits, and PR consistency.
  Replace repository-specific fixtures where needed; do not delete the worker
  test suite wholesale. Use the target repository's `./scripts/verify` instead.
- Replace `TEST_COMMAND` with `./scripts/verify` inside the target repository,
  consistent with item 5, then delete `worker/evidence.py` and unused imports.
- Use manual GitHub review to judge change correctness and test adequacy.
  Retain independent final verification and Git/PR checks; removing event
  validation means no automated proof that verification preceded publication.
- Align accepted decisions/specification and documentation before implementation
  with the explicitly reduced completion guarantees. Update affected image
  integration and tests; run focused tests, `make verify`, and `make preflight`.

## 7. Simplify tests around generic behavior and end-to-end outcomes

- Classify existing tests by the behavior they protect, not their Gate 2 origin.
  Remove smoke-task-specific cases and duplicates; replace calculator assumptions
  with repository-agnostic fixtures consistent with items 5 and 6.
- Keep focused tests for security boundaries and failure handling, including
  credential guards, job authorization, lifecycle hooks, terminal status,
  termination, and Git/PR consistency. Do not replace useful focused coverage
  wholesale with end-to-end tests or narrow coverage scope to hide removals.
- Add a small end-to-end suite covering:
  - Successful job: setup and verification pass, a matching PR is published,
    and VM termination is confirmed.
  - Setup or verification failure: the job reports error and VM termination
    is confirmed.
  - Publication failure: the job reports error and VM termination is confirmed.
- Keep local simulated-service checks in the credential-free development workflow.
  Make live AWS/GitHub end-to-end runs explicit and separately authorized;
  document credentials, costs, cleanup, and how to run them. Do not deploy or
  create live resources as part of ordinary `make verify`.
