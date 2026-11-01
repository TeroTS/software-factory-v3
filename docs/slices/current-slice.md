# Current Slice

Status: done

## Slice Goal
- Make a mechanically verified task PR available for human review without obsolete calculator-specific acceptance gates.

## Slice Boundary
- Human reviewer inspects the published diff and tests; automated completion relies on independent final repository verification and matching Git/PR publication, while correctness and test adequacy remain human judgments rather than automatic merge approval.

## Stories In Scope
- `US-12 Review a task PR without calculator-specific acceptance gates`

## Stories Completed In This Slice
- `US-12 Review a task PR without calculator-specific acceptance gates`

## Stories Remaining In This Slice
- None.

## Contract Inputs
- [openapi.yaml](../../openapi.yaml): unchanged Job/lifecycle/status shapes and reduced automated completion guarantees.
- [docs/contracts.md](../contracts.md): script execution, generic Git/PR consistency, and manual-review boundary.

## Decision Inputs
- [DECISIONS.md](../../DECISIONS.md): approved removal of AST/chronology/calculator checks and preservation of generic PR checks. US-11 already disconnected obsolete checks and adopted `./scripts/verify`; this slice removes their modules and packaging, not new product policy.

## Shared State Required Now
- Existing authorized Job, scoped credentials, checkout, unique task branch, committed/remote head, configured PR target, and stable terminal status.
- US-11 is complete locally; prior implementation and gate evidence remain in git history. Deployed resources, secret permissions, active image pin, and unmerged PRs are unchanged.

## Operations / Endpoints / Surfaces In This Slice
- `worker/task.py`: own generic `verify_pr()` without changing its checks.
- Remove `worker/evidence.py`, `image/acceptance.py`, their dedicated tests, and image/source-staging references in `image/Dockerfile`, `.dockerignore`, and `infra/stacks.py`.
- Retain generic worker/security/script/Git/PR coverage in `tests/test_worker.py`; update image and staging regression tests.
- Contract descriptions already disclose reduced guarantees; review them for any remaining obsolete requirements.

## Tests Required
- Focused PR checks for configured target, branch/head/base, open/unmerged state, positive PR number, and exactly one result.
- Real local checkout and script verification with one simulated agent changing existing content, committing/pushing through local Git, and providing a matching simulated PR without source-preservation or publication chronology gates.
- Retain credential/redaction, authorization, script failure, clean commits, remote/PR mismatch, lifecycle/status, and real sandbox/configuration coverage.
- Focused tests, `make verify`, `make preflight`, `make synth`, whitespace and affected local links/formatting; investigate coverage changes without narrowing measurement scope.

## Not Now
- US-13 workflow simplification and US-14 broader suite consolidation.
- README.md, AGENTS.md, DECISIONS.md, dependency changes, new Job fields, frameworks, agent retries, deployment, live Jobs, image-version activation, or broader repository/credential scope.
- US-05/US-06 live demonstrations require separate authorization. Existing README/agent guidance references to removed acceptance code remain untouched under the explicit US-08–US-14 documentation boundary.

## Done When
- Obsolete evidence/calculator modules, dedicated tests, and runtime/build references are gone without relocation.
- Independent final `./scripts/verify`, nonempty clean commits, baseline ancestry, matching remote head and exactly one open unmerged configured-target PR still gate success.
- Contracts and this note disclose that automated success does not prove task correctness, adequate/unweakened tests, or verification-before-publication.
- Required local checks pass and actual results, coverage changes, and live-verification gaps are recorded here.

## Completion Summary
- US-12 is complete locally. Deleted `worker/evidence.py` and `image/acceptance.py`, all six dedicated chronology/AST/calculator tests, and unused calculator fixtures/imports/constants. The disconnected `TEST_COMMAND` disappeared with its module; the active worker already uses `./scripts/verify`. No obsolete checks were relocated or restored.
- `verify_pr()` now lives in `worker/task.py` with its existing validation and failure messages unchanged. Generic PR regressions now target `release` and cover branch/head/base mismatches, closed/merged PRs, invalid/missing numbers, and missing/duplicate results. All existing generic worker, credential, script, authorization, lifecycle, and workflow tests remain.
- Removed acceptance packaging from `image/Dockerfile` and `.dockerignore`, and both obsolete modules from native CDK source staging. Staging rebuilds only its generated `factory-source` directory so previous synthesis cannot silently repackage removed files. The staging test plants stale content and requires exactly the current Dockerfile, runtime requirements, managed configuration, server, and task files.
- The existing generic `example/authorized-project` job regression passed with real local checkout/scripts/Git, a simulated agent replacing existing greeting content, and matching simulated PR publication to `release`. Only the initial sandbox probe is supplied as command evidence; no source-preservation or verification-before-publication chronology gates apply. One agent invocation and independent final verification remain mandatory. STS/Secrets Manager use Stubber; model and GitHub services are simulated.
- Reviewed `openapi.yaml` and `docs/contracts.md`; their existing US-11 descriptions already specify independent script/Git/PR completion and explicitly exclude automated correctness, test adequacy, AST preservation, and publication chronology, so no contract edit was necessary. Passing scripts do not protect against weakened tests. Human GitHub review remains required and does not establish command chronology or authorize merging. README.md, AGENTS.md, and DECISIONS.md remain unchanged per the story documentation boundary.
- Test-first checks failed for the intended missing changes: PR validation still belonged to `worker.evidence`, obsolete acceptance still existed, and repeated staging retained obsolete/stale files. After implementation, focused `uv run --locked python -m unittest discover -s tests -p test_worker.py -v` (24), `-p test_image_runtime.py -v` (four), and `-p test_infra.py -v` (seven) passed. An initial formatting check identified two affected test files; only those were formatted with `uv run --locked ruff format tests/test_worker.py tests/test_infra.py`.
- `make verify` passed formatting, lint, coverage/tests, and logging: 55 discovered, 52 passed, three Docker-only host skips. Six obsolete tests were removed and two packaging/ownership regressions added, explaining the net decrease from 59 tests. Overall coverage increased from the US-11 recorded 94% to 95%; `worker/task.py` increased from 94% to 95% with PR validation moved into its measurement. `infra/stacks.py` remains 100%, including both fresh and repeat staging. The configured source scope remains `tools`, `worker`, `infra`, and `image`, with branch and missing-line reporting unchanged; `image` now has no Python production module to report. No coverage gate was lowered or measurement scope narrowed.
- `make preflight` passed image validation plus all 34 Linux ARM64 tests on Python 3.13: three real runtime/config/sandbox checks, 24 worker/publication checks, and seven lifecycle checks. Containers had no host credentials or external networking and metadata access was disabled; only Docker's outer seccomp filter was disabled for nested namespaces, not Codex's sandbox. An additional credential-free Docker assertion confirmed both deleted modules are absent from the built image. Local image `sha256:b278cbeb73121e44db3df5b47bee2a218d0fa3907e03cffbe805235d650d86f5` is not an active AWS image pin.
- `make synth` passed without AWS lookups/deployment, regenerating the current source asset without obsolete files. Existing Node 25.4.0/jsii compatibility and CDK feature-flag notices remain; Node 22/24 is recommended. No dependencies, runtime credentials, image activation, permissions, or deployed resources were changed.
- `git diff --check` and current-slice local-link/heading/fence checks passed. Runtime/build searches found no obsolete helper/module references except explicit regression assertions of absence. No dedicated OpenAPI semantic validator, type-check, or CI command is configured; unchanged contracts did not require new schema validation.
- No live Job, AWS deployment, model call, external GitHub publication/review, or actual MicroVM credential-provider/refresh proof was attempted. Live use requires separate authorization, a newly validated pinned image, reviewed repository/token scope and script credential exposure, approved executable scripts/baseline, retained VM ID, and confirmed cleanup. Local simulated success does not establish the real gates.

## Next Slice Recommendation
- `US-13 Run a job through a simplified CDK lifecycle workflow`: replace per-execution image inspection and raw orchestration with the approved high-level/fresh-polling/shared-termination state plan, retaining scoped permissions and token-safe results.
- Keep US-14 test consolidation in view, but defer it and separately authorized US-05/US-06 demonstrations.
