# Current Slice

Status: done

## Slice Goal
- Run a coding Job in a configuration-authorized repository using its executable setup and verification scripts.

## Slice Boundary
- Operator submits the existing structured Job for an authorized repository; one Codex invocation publishes a task PR, and independent script and Git/PR checks move pending to stable ok, or unrecoverable failures move pending to stable error.

## Stories In Scope
- `US-11 Run a job in an authorized repository using its scripts`

## Stories Completed In This Slice
- `US-11 Run a job in an authorized repository using its scripts`

## Stories Remaining In This Slice
- None.

## Contract Inputs
- [openapi.yaml](../../openapi.yaml): unchanged Job fields and lifecycle/status shapes; required deployment-controlled repository authorization and PR target, and reduced automated completion guarantees.
- [docs/contracts.md](../contracts.md): executable script ordering, failure handling, environment/CDK configuration, credential exposure, and manual-review boundary.

## Decision Inputs
- [DECISIONS.md](../../DECISIONS.md): repository authorization/PR-target configuration, repository-scoped secret selection, script trust review, and US-11/US-12 evidence coordination.
- Operator explicitly approved `AUTHORIZED_REPOSITORY` and `PR_TARGET_BRANCH` alongside the existing repository-scoped secret, failing closed when configuration is missing, with scripts' runtime credential exposure disclosed. This does not authorize another live repository, deployment, or broader credentials.

## Shared State Required Now
- Existing Job, subprocess environment, scoped runtime role and GitHub secret, repository checkout, unique branch, Git head, PR, and stable terminal status.
- One authorized repository and configured PR target per image configuration. Native CDK parameters `AuthorizedRepository` and `PrTargetBranch` supply worker environment settings; defaults retain the original demonstration repository and `main`. The existing secret ARN, runtime identity guard, and scoped IAM policies remain unchanged.
- Prior completed slices and gate evidence remain in git history. Deployed resources, active image pin, secret permissions, and unmerged PRs remain unchanged.

## Operations / Endpoints / Surfaces In This Slice
- `worker/server.py`: bounded Job validation and fail-closed authorization before worker launch or credential retrieval.
- `worker/task.py`: script-based preparation, repository-agnostic prompt and completion checks, retained initial sandbox probe, and terminal rejection of streamed agent errors even with process exit zero.
- `worker/evidence.py`: generic PR validation now accepts the configured target; legacy script-command/AST helpers remain disconnected from completion until US-12 deletes them.
- `infra/stacks.py`, `openapi.yaml`, `docs/contracts.md`, and connected worker/lifecycle/infrastructure tests. `tests/test_sandbox.py` now bounds app-server shutdown with kill-and-reap escalation after a SIGTERM timeout; configuration assertions are unchanged. Existing image source staging includes worker changes without a Dockerfile or Makefile change.

## Tests Required
- Fail-closed missing/malformed configuration and unauthorized/malformed Job input; another synthetic authorized repository and a non-main PR target.
- Real local checkout, executable setup and baseline verification before one simulated agent invocation, independent final verification, nonempty committed changes, ancestry, clean workspace, matching remote head, and one open unmerged matching PR.
- Missing/non-executable/failing setup or verification, unavailable/failing final verification, invalid target branch, failed sandbox probe, agent errors, and contradictory Git/PR publication. Retain credentials, redaction, lifecycle, stable-status, and real image sandbox/configuration regressions.
- Focused tests, `make verify`, `make preflight`, `make synth`, whitespace, affected local links, and OpenAPI YAML/internal-reference checks.

## Not Now
- US-12 physical deletion of `worker/evidence.py`, `image/acceptance.py`, obsolete AST/chronology helpers, dedicated tests, and image/build references; relocation of generic PR validation to `worker/task.py`.
- US-13 workflow simplification and US-14 suite consolidation.
- README.md, AGENTS.md, DECISIONS.md, dependency changes, new Job fields, classes/frameworks, agent retries, or changes to the factory Make workflow.
- Deployment, active image updates, secret/token permission changes, another live repository, external model/GitHub Jobs, and US-05/US-06 demonstrations require separate authorization.

## Done When
- Approved deployment configuration rejects unauthorized Jobs without broadening credential scope.
- No demonstration repository/task/file/test restrictions gate the worker. Setup and baseline verification precede one Codex invocation; independent final script and matching Git/PR checks precede success.
- Contracts disclose that independent verification and manual review do not prove verification-before-publication or protect against weakened tests after obsolete chronology/source checks are disconnected.
- Focused tests, full verification, image preflight, and synthesis pass; coverage changes and live verification gaps are recorded here.

## Completion Summary
- US-11 is complete locally. Repository authorization and a nonblank configured PR target are required; repository strings must match configuration exactly. Git validates the configured target before scripts/Codex run, and a task branch cannot equal that target. Job fields and bounded input, asynchronous lifecycle hooks, and one-field stable terminal status are unchanged.
- The worker executes repository-owned `./scripts/setup` and baseline `./scripts/verify` directly from the pinned checkout. Missing/non-executable scripts emit a fixed, explicit diagnostic; subprocess failures retain redacted output and actual exit codes. The same agent is instructed to repair failing verification and run passing final `./scripts/verify` before publication, without restarting or rerunning the Job. The worker independently executes verification afterward and preserves nonempty commits, ancestry, clean workspace, matching remote head, and exactly one open unmerged matching PR.
- The local `example/authorized-project` fixture uses executable shell scripts and a greeting text file, targeting `release`. Its simulated agent changes existing content, runs verification, commits and pushes through real local Git, and supplies matching simulated PR data. It supplies only the initial shell probe, deliberately no publication chronology; successful completion therefore demonstrates removal of calculator, AST-preservation, and chronology gates. STS/Secrets Manager use botocore Stubber; Codex/model/GitHub services remain simulated. Credentials, failure propagation, pinned managed Codex policy, real sandbox enforcement, and final identity rediscovery are retained.
- US-12 coordination is explicit: obsolete evidence and acceptance modules/tests remain packaged but are never called by the generic completion path. `TEST_COMMAND` in the legacy chronology helper now uses the script contract; `verify_pr()` accepts the configured target. No automated check establishes correctness, adequate/unweakened tests, or verification-before-publication. Human GitHub review judges correctness and test adequacy but cannot establish command chronology; success does not permit merging.
- Test-first authorization, generic fixture/script workflow, configured target, streamed error handling, and missing-script diagnostics failed for their intended missing behavior, then passed. A fixture verification script initially failed to propagate its test exit status because its final printf returned zero; adding `set -e` corrected the fixture, and independent final-verification failure then exercised the intended path.
- Focused `uv run --locked python -m unittest discover -s tests -p test_worker.py -v`, `-p test_server.py -v`, and `-p test_infra.py -v` passed (worker cases also rerun in full coverage and ARM64 preflight). `make verify` passed format-check, lint, branch coverage/tests, and logging: 59 discovered, 56 passed, three Docker-only host skips. Overall and `worker/task.py` coverage remain 94%; measurement scope and branch reporting are unchanged. An initial lint check identified three nested test contexts; only affected tests were corrected and the rerun passed.
- `make preflight` passed image validation and all 39 ARM64 tests: three real runtime/config/sandbox checks, 29 worker/evidence checks, and seven lifecycle checks on Python 3.13. Container tests had no host credentials or external networking and disabled metadata access; only Docker's outer seccomp filter was disabled, not Codex's sandbox. The first preflight passed; final repeat runs exposed an existing app-server shutdown timeout after configuration had been returned. The test helper previously canceled its kill timer before attempting SIGTERM cleanup and left a running subprocess/open stream when its five-second communicate timeout expired. It now kills and reaps that test subprocess on timeout, retaining bounded cleanup and all config/sandbox assertions. The corrected rerun passed all 39 tests. Local image `sha256:e4f338e34956b6afbe8f500530739e3528e8beaf186ee5eb557eb88fa4cbec6d` is not an active AWS image pin.
- `make synth` passed without AWS lookups/deployment and includes the approved environment parameter references, unchanged scoped secret permission, and updated staged worker sources. Existing Node 25.4.0/jsii warnings remain; Node 22/24 is recommended, with no installation or dependency upgrade performed. `git diff --check`, affected Markdown local-link/fence checks, and Ruby standard-library Psych OpenAPI YAML/internal-reference checks passed. A Node YAML-parser availability probe found none; no package was installed. No dedicated OpenAPI semantic validator, type-check, or CI command is configured.
- No live Job, deployment, model call, external publication, runtime credential-provider/refresh proof, or another repository authorization occurred. Future live use requires separate authorization, a newly validated pinned image, reviewed repository/token scope and script credential exposure, an approved baseline containing executable scripts, retained VM ID, and confirmed cleanup. The original demonstration baseline must not be silently changed or assumed to already contain these scripts.

## Next Slice Recommendation
- `US-12 Review a task PR without calculator-specific acceptance gates`: physically delete disconnected obsolete evidence/acceptance modules and dedicated tests, relocate generic PR validation, and retain generic security/script/Git/PR coverage.
- Keep US-13 workflow simplification and US-14 suite consolidation deferred; finish them before separately authorized US-05/US-06 demonstrations.
