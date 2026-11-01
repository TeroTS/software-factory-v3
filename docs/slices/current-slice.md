# Current Slice

Status: done

## Slice Goal
- Execute the same Job through explicit worker stages without changing behavior.

## Slice Boundary
- Operator runs a Job through environment preparation, repository preparation, one agent invocation, and independent completion verification; pending still becomes stable ok/error under unchanged validation and publication rules.

## Stories In Scope
- `US-09 Execute a job through explicit worker stages`

## Stories Completed In This Slice
- `US-09 Execute a job through explicit worker stages`

## Stories Remaining In This Slice
- None.

## Contract Inputs
- [openapi.yaml](../../openapi.yaml): unchanged Job/lifecycle/status interfaces.
- [docs/contracts.md](../contracts.md): unchanged submission boundaries; repository scripts remain deferred to US-11.

## Decision Inputs
- [DECISIONS.md](../../DECISIONS.md): focused stage extraction with explicit inputs/returns, unchanged ordering and failure handling, final credential discovery in execute().

## Shared State Required Now
- Existing validated Job, subprocess environment, redaction secrets, initial credential identity, repository path, unique branch, baseline source/tests, and agent events passed explicitly between stages.
- US-01–US-04 and US-07–US-08 are complete; US-08 completion was local, not new live credential-provider validation. Earlier gate/runtime evidence remains in git history.
- Preserve deployed resources, GitHub-token secret, pinned baseline, and unmerged PRs; this slice does not change permissions or deployed image/version.

## Operations / Endpoints / Surfaces In This Slice
- `worker/task.py`: prepare_environment(), prepare_repository(), invoke_agent(), verify_completion(), and execute() coordination/final credential check.
- `tests/test_worker.py`: complete simulated-service Job stage ordering and failure propagation regressions; existing lifecycle callers remain unchanged.

## Tests Required
- Test-first stage ordering over real local checkout/tests/Git with simulated AWS/agent/GitHub services; explicit values passed between stages and one agent invocation.
- Stage failures stop subsequent stages and final credential checks; final credential failure prevents success with sanitized diagnostics.
- Existing credential, authorization, redaction, publication, and lifecycle regressions; focused worker tests and make verify.
- ARM64 make preflight verifies extracted code on the image interpreter; whitespace/local-link checks.

## Not Now
- US-10 static Codex configuration, US-11 repository scripts/authorization, US-12 obsolete evidence removal, US-13 workflow changes, and US-14 test consolidation.
- README.md, AGENTS.md, DECISIONS.md, dependency/configuration/interface changes, new classes/frameworks, or agent/Job retries.
- Deployment, active-image pin update, real AWS/model/GitHub calls, and US-05/US-06 demonstrations require separate authorization.

## Done When
- execute() coordinates the four focused stages and retains validation and final credential discovery; explicit inputs/returns preserve ordering and completion guarantees.
- Focused success/failure regressions and make verify pass; local simulation is distinguished from separately authorized live verification.

## Completion Summary
- US-09 is complete locally. `prepare_environment(secrets)` returns the subprocess environment and initial access-key identity after scoped credential discovery, secret validation, and Git authentication. `prepare_repository(job, env, secrets)` returns the checked-out repository, unique branch, and baseline source/test evidence after passing baseline tests. `invoke_agent(job, repository, branch, env, secrets)` enforces the existing pinned CLI/settings and returns events from exactly one Codex invocation. `verify_completion(job, repository, branch, baseline, events, env, secrets)` retains ordered event evidence, independent final tests/acceptance, source preservation, ancestry, clean workspace, remote-head matching, and PR validation.
- `execute()` still validates the structured Job before environment preparation, creates the shared redaction list, coordinates the stages, and rediscover-validates final scoped credentials before success logging/exit. Existing command arguments, prompts, SDK request bounds, subprocess behavior, status/lifecycle interfaces, and failure/redaction handling are unchanged. No class, framework, dependency, configuration, or interface change was introduced.
- Test-first stage checks failed with two assertion failures because `prepare_environment` was absent, then passed after extraction. The complete local Job wraps real stages and verifies ordering, explicit values passed between them, both credential checks, one agent invocation, one PR, and secret-free logs. It uses real local checkout/tests/Git and botocore Stubber with simulated agent/GitHub services. A new failure regression covers all four stages and the final credential check: failure stops coordination and prevents success without logging exception values. Existing credential, authorization, evidence/publication, redaction, process, and lifecycle regressions remain intact.
- `uv run --locked python -m unittest discover -s tests -p test_worker.py -v` passed all 21 focused tests. `make verify` passed format-check, lint, coverage/tests, and logging: 47 discovered, 45 passed, two Docker-only host skips. Branch-aware coverage remains 94% overall and for `worker/task.py`; measurement scope and missing-line reporting are unchanged. The pre-existing Node `25.4.0`/jsii warning remains; Node 22/24 is recommended, with no host installation/replacement performed.
- `make preflight` passed image validation and all 29 ARM64 tests (two real sandbox/runtime, 21 worker/evidence, six lifecycle), using Python 3.13 without host credentials or external networking and with metadata disabled. Only Docker's outer seccomp filter was disabled for nested namespaces; Codex's sandbox remains enforced. Rebuilt local image: `sha256:1e12819dc4532be8bd7d7d5b88b5c592393b49f6072d617d4bcbe0176feeddea`; this is not a deployed or active AWS image pin.
- `git diff --check` and current-slice local-link/fence checks passed. `make synth` was not rerun because no infrastructure, source-staging logic, or configuration changed; native stack/source-staging tests passed within `make verify`. External links were not fetched. No type-check/CI command exists.
- No deployment, active-image pin update, real AWS/secret/model/GitHub call, PR publication, or live Job occurred. Local stage tests do not resolve US-08's real MicroVM SDK credential-provider and cross-process refresh verification gaps. Deployed resources and prior gate evidence remain unchanged. Any live continuation requires separate authorization, reviewed image/permissions, retained VM ID, and confirmed cleanup; this behavior-preserving extraction alone does not change completion guarantees.

## Next Slice Recommendation
- `US-10 Run Codex with factory-owned static configuration`: verify pinned-Codex loading/precedence before selecting the image-owned location, then separate static settings from per-job invocation arguments while enforcing approved policies against repository overrides.
- Keep US-11 repository scripts/authorization and US-12 evidence removal as deferred adjacent work. Deliver remaining US-10–US-14 before separately authorized US-05/US-06 demonstrations; no deployment or live Job is implicit.
