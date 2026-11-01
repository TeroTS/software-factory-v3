# Current Slice

Status: done

## Slice Goal
- Run Codex with factory-owned static configuration that repository settings cannot override.

## Slice Boundary
- Operator runs the existing Job with image-owned model/provider and security policy; per-job invocation arguments remain runtime values and pending still becomes stable ok/error under unchanged completion checks.

## Stories In Scope
- `US-10 Run Codex with factory-owned static configuration`

## Stories Completed In This Slice
- `US-10 Run Codex with factory-owned static configuration`

## Stories Remaining In This Slice
- None.

## Contract Inputs
- [openapi.yaml](../../openapi.yaml): unchanged Job/lifecycle/status interfaces.
- [docs/contracts.md](../contracts.md): unchanged submission boundaries; repository scripts remain deferred to US-11.

## Decision Inputs
- [DECISIONS.md](../../DECISIONS.md): image-owned static Codex configuration, retained invocation-specific arguments, pinned-version precedence validation, unchanged sandbox and credential constraints.

## Shared State Required Now
- Existing Job, subprocess environment, redaction secrets, repository/branch, agent events, and completion evidence.
- US-01–US-04 and US-07–US-09 are complete locally/as previously recorded; prior gate evidence remains in git history. Deployed resources, secret, and unmerged PRs stay unchanged.
- Factory-owned `/etc/codex/managed_config.toml`, root-owned and unwritable by the factory user, with the same approved static settings.

## Operations / Endpoints / Surfaces In This Slice
- `image/managed_config.toml`, `image/Dockerfile`, `.dockerignore`, and `infra/stacks.py` source staging.
- `worker/task.py` invocation and missing-configuration failure handling.
- Connected worker, image, source-staging, and real pinned-Codex configuration/sandbox tests.

## Tests Required
- Test-first invocation-specific arguments and rejection of a missing factory policy before agent access.
- Parse/check static policy and packaged/staged contents without credentials.
- Real Codex 0.166.0 config/read checks in the ARM64 image: effective static settings and origins with trusted repository/user/CLI override attempts; no model or external calls.
- Real sandbox enforcement and existing simulated-service complete Job, credentials, publication, and lifecycle regressions.
- Focused tests, make verify, make preflight, make synth, whitespace and local-link checks.

## Not Now
- US-11 repository scripts/authorization, US-12 obsolete evidence removal, US-13 workflow simplification, US-14 suite consolidation.
- README.md, AGENTS.md, DECISIONS.md, dependency upgrades, new Job fields, classes/frameworks, or agent retries.
- Deployment, active-image pin updates, real AWS/model/GitHub jobs, and US-05/US-06 demonstrations require separate authorization.

## Done When
- Static settings load from factory-owned image configuration and survive repository override attempts on pinned Codex; runtime invocation retains exec/JSON/ephemeral/repository/.git/stdin arguments.
- Missing configuration fails closed; focused checks, full verification, image preflight, and synthesis pass without changing completion guarantees.

## Completion Summary
- US-10 is complete locally. `image/managed_config.toml` retains the approved model/provider, workspace-write sandbox with network access, disabled web search, never approval, and all/included shell environment policy. Docker copies it to root-owned `/etc/codex/managed_config.toml`; the factory user cannot write the file or its directory. `.dockerignore` includes the source and CDK stages the exact bytes into the image asset. No secrets or per-job state are baked into the policy.
- `invoke_agent()` retains pinned-version enforcement and rejects a missing factory configuration before agent access. Its arguments now contain only `exec`, `--json`, `--ephemeral`, repository `-C`, `.git` `--add-dir`, and stdin `-`. Stage ordering, prompts, one invocation, completion evidence, credentials, and lifecycle/status contracts are unchanged. Lifecycle/sandbox validation retains its explicit probe flags; those are not Job invocation arguments.
- Configuration location was selected only after inspecting the [0.166.0 loader layering documentation](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/config/src/loader/README.md) and [Unix managed-file lookup](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/config/src/loader/layer_io.rs), and exercising the actual pinned ARM64 executable. Normal system/user configuration loses to project config; this managed layer wins over project, user, and session/CLI settings. It is a legacy layer being phased out upstream: revalidate loading/precedence before any separately approved Codex upgrade. No dependency change, custom parser, workflow framework, or runtime policy wrapper was introduced.
- Test-first worker invocation/missing-policy, image packaging, source staging, and ARM64 policy checks failed for their intended missing behavior, then passed. The complete simulated-service Job checks exact per-job arguments, real checkout/tests/Git, one agent invocation, matching publication, both credential checks, and sanitized logs. The real Codex `config/read` regression checks effective settings and the winning managed layer both normally and with conflicting user, explicitly trusted project, and CLI settings; the conflicting project layer is confirmed enabled rather than merely ignored. Config checks use bounded local subprocesses, no model calls, and disposable runtime homes.
- An additional offline `codex exec --ephemeral -C /workspace/repo --add-dir /workspace/repo/.git -` diagnostic in the credential-free, network-disabled image displayed the approved model/provider, approval never, and workspace-write including the repository/.git roots with network access enabled. Inference then failed as expected because no AWS credential provider was available; this is configuration evidence, not successful model/runtime authorization. An attempted removal of sandbox-probe-specific flags made its Git metadata test fail, so those existing probe flags were retained; the actual exec diagnostic confirms Job loading separately.
- Focused `uv run --locked python -m unittest discover -s tests -p test_worker.py -v` (22 tests), `-p test_image_runtime.py -v` (three), and `-p test_infra.py -v` (six) passed. `make verify` passed format-check, lint, coverage/tests, and logging: 50 discovered, 47 passed, three Docker-only host skips. Overall and worker/task.py branch-aware coverage remain 94%; measurement scope, branch reporting, and gates are unchanged. The existing Node 25.4.0/jsii warning remains; Node 22/24 is recommended, with no host installation or replacement performed.
- `make preflight` passed image validation and all 31 ARM64 tests (three runtime/config/sandbox, 22 worker/evidence, six lifecycle) using Python 3.13, no host credentials, disabled metadata, and no external networking during container tests. Only Docker's outer seccomp filter was disabled; Codex's own sandbox stayed enforced. Local image `sha256:b72a63d31fbefce03727e52504fcafb3f97b7ced38f1831ae0906e173a0305c0` is not a deployed/active AWS image pin. An initial preflight fixture path failure was corrected so host tests use source policy and image tests use the installed policy; rerun passed.
- `make synth` passed and staged the updated policy without AWS lookups or deployment. `git diff --check` and current-slice local-link/fence checks passed. Only changed test files were formatted with locked Ruff; no unrelated formatting or guidance/interface edits. No type-check or CI command exists.
- No deployment, active-image pin update, live AWS/secret/GitHub access, successful external model call, PR publication, or live Job occurred. Local checks do not establish the rebuilt image's real MicroVM credential-provider/refresh or model compatibility. Future live use requires separate authorization, reviewed newly validated image pin/permissions, retained VM ID, and confirmed cleanup; previous gates and deployed resources remain unchanged.

## Next Slice Recommendation
- `US-11 Run a job in an authorized repository using its scripts`: reconcile repository authorization/PR-target configuration and scripts' credential-access trade-off, then implement executable setup/verification without calculator-specific worker assumptions.
- Keep US-12 obsolete evidence removal and US-13 workflow simplification as deferred adjacent work. Finish US-11–US-14 before separately authorized US-05/US-06 demonstrations; no deployment or live Job is implicit.
