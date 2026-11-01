# Current Slice

Status: done

## Slice Goal
- Run the worker and repository tests on a maintained Linux ARM64 image Python runtime.

## Slice Boundary
- Operator builds and locally validates a newly selected image interpreter; startup, worker subprocesses, and repository tests use that interpreter without changing Job/status or security behavior.

## Stories In Scope
- `US-07 Run the worker on a maintained image Python runtime`

## Stories Completed In This Slice
- `US-07 Run the worker on a maintained image Python runtime`

## Stories Remaining In This Slice
- None.

## Contract Inputs
- [openapi.yaml](../../openapi.yaml): unchanged Job/lifecycle/status and demonstration `python3 -m unittest discover -s tests -v` command.
- [docs/contracts.md](../contracts.md): unchanged AWS submission boundaries.

## Decision Inputs
- [DECISIONS.md](../../DECISIONS.md): US-07 interpreter evaluation, image validation, sandbox/snapshot safety, separate deployment authorization.

## Shared State Required Now
- One explicitly selected image interpreter; existing `sys.executable` propagation for worker and acceptance subprocesses.
- Preserve the demonstration test command by making image `python3` resolve to the selected interpreter, without replacing the OS interpreter used by packaged tools.
- US-01–US-04 are complete. Gate 1 passed; US-04 passed on native-CDK image `2.0`, execution `gate2-us04-787d7a7ff18d40ab9f264f22734e29c7`, VM `microvm-d79268a3-d531-3bdb-825b-15a20d2566ba`, workflow-confirmed `TERMINATED`. All six retained VMs were TERMINATED at the last live inspection; no new live inspection is implied here.
- Preserve secret `arn:aws:secretsmanager:us-east-1:172318459814:secret:software-factory-poc/github-token-sXayGQ` and open/unmerged demonstration PRs [#1](https://github.com/TeroTS/factory-smoke-test/pull/1), [#2](https://github.com/TeroTS/factory-smoke-test/pull/2), [#3](https://github.com/TeroTS/factory-smoke-test/pull/3). Baseline `74daa6e2bca790d0a62d4d9ab47f07bca48fbc12`, target `main`; successful US-04 head `14bd7bd93934281aa43407c6b2bd0c091eb8d025`. Earlier detailed gate evidence remains in git history.

## Operations / Endpoints / Surfaces In This Slice
- `image/Dockerfile`, lifecycle executable validation, Make preflight, runtime tests, Ruff compatibility configuration, and implementation documentation in this slice note only.
- Availability evaluation: ARM64 base-container `dnf repoquery python3.13 python3.13-libs` succeeded and listed both versioned aarch64 packages through `3.13.16-1.amzn2023.0.1`. Initial `dnf list --available` was unsupported by microdnf; used its supported repoquery instead.
- Selected Python 3.13 using the versioned AL2023 package after successful ARM64 preflight. Factory development Python 3.14 and dependency lockfiles remain unchanged.

## Tests Required
- Regression for versioned image startup and `python3` interpreter mapping; confirm existing worker subprocesses retain the running interpreter.
- Real Linux ARM64 sandbox check confirms Python 3.13 and repository test subprocess selection, plus edit/test/Git compatibility.
- Focused worker/lifecycle tests, `make verify`, `make preflight`, local synthesis for image staging, and whitespace/affected documentation link checks.

## Not Now
- US-08 boto3, US-09 stage extraction, US-10 static Codex configuration, US-11–US-14 generic worker/workflow/tests.
- AWS deployment/image pin update, secret retrieval, model/GitHub calls, live jobs, and US-05/US-06 demonstrations; all require separate authorization.
- No host runtime installation, OS interpreter replacement, sandbox relaxation, or new dependency.

## Done When
- Python 3.13 package availability is verified on the actual ARM64 base, and the built image runs startup/worker/subprocess/repository tests on that interpreter.
- Focused tests, `make verify` and `make preflight` pass; runtime settings and documentation agree while contracts, snapshot cleanliness, and security/failure handling remain intact.
- Local image validation is distinguished from separately authorized AWS image/model/credential validation and deployment.

## Completion Summary
- US-07 is complete locally: the ARM64 image installs `python3.13`, starts the server with `python3.13`, and maps repository `python3` commands/shebangs to it via `/usr/local/bin/python3`. `/usr/bin/python3` is not replaced. Existing `sys.executable` worker/acceptance propagation is retained, and validation now checks the running interpreter rather than an unrelated PATH default.
- Updated Make preflight to versioned interpreter invocations and Ruff worker/image/test targets to `py313`. Interpreter selection and implementation details are recorded here only; US-07-specific edits to README.md, AGENTS.md, and DECISIONS.md were reverted at the operator's direction. Job/lifecycle/status interfaces and the demonstration test command are unchanged; no new dependency or lockfile change. Python 3.13 natively parses credential-expiration `Z` suffixes; removed the obsolete replacement and used `datetime.UTC` to satisfy newly applicable Ruff rules. Credential expiry/identity/redaction regressions remain intact.
- Test-first checks failed against old sources for package/startup selection and executable validation; real sandbox/runtime checks failed against the old Python 3.9 image. All passed after the runtime changes. Added checks for actual ARM64/version, both interpreter names, executable repository shebangs, nested sandbox test interpreter, and existing worker-spawn interpreter propagation.
- Focused host image/runtime, lifecycle (six), and worker (18) tests passed. `make verify` passed format/lint/logging and 42 discovered tests (40 passed; two Docker-only tests host-skipped); branch-aware coverage remains 93% with unchanged `tools/worker/infra/image` scope and missing lines. No decrease or lowered gate.
- `make preflight` passed the rebuilt ARM64 image's actual sandbox validation and 26 tests: two real sandbox/runtime checks, 18 worker/evidence tests, and six lifecycle tests. The built image reports Python `3.13.16`, package/libraries `3.13.16-1.amzn2023.0.1.aarch64`, local Docker image `sha256:04861743204fec8f155f7611de7bdf50807f5569003c8dc752b3de5af3180a83`. Containers used no host credentials or external networking; only the outer Docker seccomp filter was disabled for nested namespaces, not Codex isolation.
- A separate credential-free, network-disabled container using the unmodified default CMD proved the actual Python 3.13 server process, HTTP ready/validate 200, exact pending status, and empty workspace after validation; container cleanup succeeded. This is the focused local image end-to-end check, not a live coding job.
- `make synth` passed native image source staging/all three stacks. The existing Node 25.4.0/jsii supported-version warning remains (Node 22/24 recommended); no host toolchain installation or replacement. Initial verification correctly rejected newly applicable datetime lint issues, which were fixed before the successful full rerun.
- `git diff --check` and affected Markdown local-link/fence-balance checks passed. External links were not fetched.
- At the operator's direction, removed broad documentation/decision-update mandates from US-08–US-14 in SPEC.md. Those stories now explicitly keep implementation details and live-run guidance in this slice note, without authorizing README.md, AGENTS.md, or DECISIONS.md edits. Required interface-contract changes, tests, and live-run safety requirements remain intact.
- No AWS deployment, active-image pin change, secret/credential retrieval, model access, GitHub write, or live job occurred. The deployed image/workflow remain untouched. Local validation does not establish real MicroVM credential-provider/model compatibility for the rebuilt image; AWS image/runtime validation and deployment need separate authorization.

## Next Slice Recommendation
- `US-08 Access runtime identity and the GitHub secret through boto3`: choose/pin compatible approved dependencies, bound STS/Secrets Manager requests, retain CLI credential export/expiration guards, and use Stubber regression tests. Keep US-09 stage extraction and US-10 static Codex policy as adjacent deferred work.
- Do not deploy or run jobs implicitly. Revalidate/pin each new AWS image under separate authorization; preserve the scoped secret and open/unmerged PRs. Deliver US-08–US-14 before the separately authorized US-05/US-06 live demonstrations.
