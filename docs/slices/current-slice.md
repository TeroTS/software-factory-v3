# Current Slice

Status: done

## Slice Goal
- Initialize a Job using bounded boto3 runtime-identity and repository-secret checks.

## Slice Boundary
- Operator runs an authorized Job; SDK identity validation precedes secret retrieval and repository/model access, retaining pending to stable ok/error and existing publication checks.

## Stories In Scope
- `US-08 Access runtime identity and the GitHub secret through boto3`

## Stories Completed In This Slice
- `US-08 Access runtime identity and the GitHub secret through boto3`

## Stories Remaining In This Slice
- None.

## Contract Inputs
- [openapi.yaml](../../openapi.yaml): unchanged Job/lifecycle/status interfaces.
- [docs/contracts.md](../contracts.md): unchanged submission boundaries; repository scripts are deferred to US-11.

## Decision Inputs
- [DECISIONS.md](../../DECISIONS.md): approved boto3/Stubber dependency, scoped identity/secret access, CLI credential export/expiration retention, separate live authorization.

## Shared State Required Now
- Existing deployment-controlled expected role and GitHub-secret ARN; temporary credentials discovered by CLI and independently by the SDK's public credential provider chain.
- Retain CLI expiration checks before setup and after completion; SDK refresh does not establish refresh in the separate Codex process.
- Prior slices US-01–US-04 and US-07 are complete. Gate 1 passed; US-04 passed on native-CDK image `2.0`, execution `gate2-us04-787d7a7ff18d40ab9f264f22734e29c7`, VM `microvm-d79268a3-d531-3bdb-825b-15a20d2566ba`, workflow-confirmed `TERMINATED`. All six retained VMs were TERMINATED at the last live inspection, not newly inspected here.
- Preserve secret `arn:aws:secretsmanager:us-east-1:172318459814:secret:software-factory-poc/github-token-sXayGQ` and open/unmerged demonstration PRs [#1](https://github.com/TeroTS/factory-smoke-test/pull/1), [#2](https://github.com/TeroTS/factory-smoke-test/pull/2), [#3](https://github.com/TeroTS/factory-smoke-test/pull/3). Baseline `74daa6e2bca790d0a62d4d9ab47f07bca48fbc12`, target `main`; earlier gate/runtime evidence remains in git history.

## Operations / Endpoints / Surfaces In This Slice
- `worker/task.py`, connected worker tests, `pyproject.toml`/`uv.lock`, image runtime dependency pins/Docker context, and native CDK image source staging.
- boto3 `1.43.123` and compatible transitive versions are locked in `uv.lock`; image runtime-only requirements include exact versions from that lock, without hashes at the operator's request. No development dependencies are installed in the image. Versioned AL2023 ARM64 `python3.13-pip` availability was checked via base-container `dnf repoquery`.

## Tests Required
- Stubber success and identity/secret failures; wrong account/role, invalid token, expired/missing credentials, sanitized diagnostics, and bounded requests.
- Complete local checkout/test/Git Job with simulated AWS/agent/GitHub services, retaining one agent invocation and independent completion checks.
- Focused worker/image/staging tests, `make verify`, `make preflight`, `make synth`, and whitespace/link checks.

## Not Now
- US-09 stage extraction, US-10 static Codex configuration, US-11–US-14 generic worker/workflow/tests.
- AWS deployment, image/version pin update, live credentials/model/GitHub calls, and US-05/US-06 demonstrations; separate authorization required.
- README.md, AGENTS.md, DECISIONS.md changes, private botocore expiry attributes, credential forwarding to Codex, or automatic Job retries.

## Done When
- Identity and secret retrieval use validated bounded SDK responses; unauthorized or failed setup cannot reach repository/model access and emits no secret-bearing errors.
- CLI credentials/expiration and availability, publication/sandbox/status guarantees remain intact; host and ARM64 checks pass.
- Local completion and remaining real MicroVM credential-provider validation are distinguished explicitly.

## Completion Summary
- US-08 is complete locally. STS `get_caller_identity()` and Secrets Manager `get_secret_value(SecretId=...)` now use boto3's public default runtime credential-provider chain with explicit `us-east-1`. Clients are closed via standard-library `closing()`. Each service request has a 5-second connect timeout, 10-second read timeout, and one total attempt (no API or Job retry); these settings do not introduce a worker deadline or change the existing CLI export behavior.
- CLI credential export/expiration validation remains before setup and after independent completion. SDK identity must match both account `172318459814` and the expected assumed-role ARN prefix before secret access; secret must be a nonempty, nonblank string. Secret/model/repository access stops on initialization failure. Stage diagnostics and exception class are retained, not secret-bearing SDK exception messages. Credentials are not copied into subprocess environment variables and no private botocore expiration attributes are used. SDK refresh is not evidence of credential refresh within Codex.
- Approved boto3 and botocore pins are both `1.43.123`; installed metadata requires Python >=3.10, and actual image execution passed on Linux ARM64 Python `3.13.16`. `uv add` updated `pyproject.toml`/`uv.lock`; boto3's required compatible `python-dateutil==2.9.0.post0` replaces the earlier development resolution `3.0.0`. CDK/constructs/Ruff/coverage pins remain unchanged. Runtime-only exact versions are stored in `image/requirements.txt`, exported with `uv export --locked --no-dev --no-emit-project --no-header --no-hashes -o image/requirements.txt`; image installation uses versioned Python pip without `--require-hashes`, at the operator's request. Version pins remain intact; pip no longer checks downloaded package files against lockfile hashes. Docker context and CDK source staging include that file. Pin-consistency tests guard against drift; no development tools or host credentials enter the image.
- Test-first worker checks rejected old CLI identity/secret access (17 failing assertions, no test errors after fixing malformed-response fixture validation); image integration and staging checks likewise failed against old sources. All pass now. Real botocore Stubber clients validate request parameters and successful responses; the complete local Job consumes two identity checks and one scoped secret response while exercising real checkout/tests/Git with simulated Codex/GitHub publication. Additional regressions cover administrator/wrong-account identities, account/ARN mismatch, missing/expired/malformed CLI credentials before SDK access, missing/binary/empty/blank secrets, sanitized SDK errors, no repository/model work on setup failure, and retained publication/redaction checks. Only the deliberately malformed empty-secret response bypasses Stubber's response-length validation during fixture registration.
- Focused commands `uv run --locked python -m unittest discover -s tests -p test_worker.py -v`, corresponding `test_image_runtime.py` and `test_infra.py` discovery passed (20, two, and six tests). `make verify` passed format-check, lint, coverage/tests, and logging: 46 discovered, 44 passed, two Docker-only host skips. Branch-aware coverage rose from the previous slice's reported 93% to 94%; `tools/worker/infra/image` measurement scope and missing-line reporting are unchanged, with no lowered gate.
- `make preflight` passed the rebuilt image's sandbox validation and 28 tests (two actual ARM64 sandbox/runtime, 20 worker/evidence, six lifecycle). Containers ran without host credentials or external networking, with metadata disabled. Only Docker's outer seccomp filter was disabled to allow nested namespaces; Codex's workspace-write sandbox remains enforced. After removing requirement hashes and rebuilding, local image is `sha256:133ced11268b5fc5c342fb09e5b62dca940a89a470f67712f20dfbc1fcd7ed93`. A separate network-disabled image command confirmed Python/boto3/botocore versions and interpreter compatibility metadata.
- `make synth` passed all three native stacks and staged runtime requirements into the content-addressed image source. The pre-existing Node `25.4.0`/jsii supported-version warning remains; Node 22/24 is recommended, with no host installation/replacement performed. `git diff --check` and current-slice local-link/fence checks passed; external links were not fetched.
- Operator-requested hash removal also updated the Docker installation comment/flags and image regression test. The focused test failed against hash-enforced installation and passed after removal. `make verify`, `make preflight`, `make synth`, and whitespace/local-link checks were rerun successfully; test counts and 94% coverage are unchanged. `uv.lock` retains its normal artifact hashes and was not changed for this follow-up.
- No AWS deployment, active-image pin change, real secret/credential retrieval, model access, GitHub write, or live Job occurred. Deployed image/workflow and scoped secret/PRs remain untouched. Real MicroVM default credential-provider compatibility, selected model access, and cross-process refresh remain live verification gaps, not inferred from Stubber or local ARM64 tests.
- Live continuation requires separate authorization, scoped provisioning/runtime permissions, image-build/VM/model costs, and retained VM ID/manual termination access. Build/deploy the reviewed image source only after authorization, then read back its new pin with `uv run --locked python tools/verify_image.py --version <new-version>` before workflow deployment. A separately approved Job must prove SDK identity/secret initialization, existing Codex credential discovery and expiry behavior, publication, and service-confirmed cleanup using the unchanged structured Job/status interfaces. Never forward host credentials into image checks or treat SDK refresh as proof of Codex refresh.

## Next Slice Recommendation
- `US-09 Execute a job through explicit worker stages`: extract environment preparation, repository preparation, agent invocation, and independent completion verification with explicit inputs/returns; retain coordination and final credentials check in `execute()`.
- Keep US-10 static configuration and US-11 repository scripts as deferred adjacent work. Deliver US-08–US-14 before separately authorized US-05/US-06 demonstrations; no deployment or live Job is implicit.
