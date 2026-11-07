# software-factory-v3

A proof of concept that turns a signed GitHub issue or operator-submitted task
into a tested, reviewed, merged PR inside an AWS Lambda MicroVM, then confirms
VM termination. Queued Jobs run under operator-controlled single-Job discipline;
this is not a production concurrency guarantee.

## Quick start

Prerequisites: Python 3.14, `uv`, Make, and Node.js/npm for CDK. jsii recommends
Node.js 22 or 24 LTS. Docker with Linux ARM64 support is needed for image
preflight. No AWS or GitHub credentials are needed for local verification.

Run from the repository root:

```sh
make setup
make verify
```

Setup creates `.venv` using `uv.lock` and installs the repository-local CDK CLI
using `package-lock.json`; nothing is installed globally. Verification
checks Python formatting, lint, tests through coverage, and logging.

## Development

| Command | Purpose |
| --- | --- |
| `make setup` | Sync `.venv` and the repository-local CDK CLI from lockfiles. |
| `make format` | Format Python files; modifies files. |
| `make format-check` | Check Python formatting without changes. |
| `make lint` | Run Ruff lint checks. |
| `make test` | Run all discovered `unittest` tests; local runtime target under 30 seconds. |
| `make coverage` | Run tests with branch coverage and report missing lines. |
| `make dev` | Run the local logging smoke check; not an application server. |
| `make verify` | Run formatting checks, lint, coverage/tests, and logging. |
| `make synth` | Synthesize native CDK templates/assets locally; no deployment. |
| `make preflight` | Build the ARM64 image and check real sandbox/search/edit/tests/Git plus simulated-service worker/lifecycle tests. |
| `make floci-test` | Run local signed-webhook, DynamoDB/Streams dispatch, and Standard entry-state tests with simulated VM lifecycle tasks; requires Docker. |
| `make floci-down` | Stop the task-local emulator and GitHub fixture containers. |

The default authorized demonstration repository is
[TeroTS/factory-smoke-test](https://github.com/TeroTS/factory-smoke-test).

## Live use and documentation

Live Jobs perform billable model work and a gated automatic merge into the
configured target. Deployment and live Jobs require separate authorization.
Follow [running and verification](docs/running.md) for deployment prerequisites,
private webhook configuration, direct-test preparation, and interrupted-run
handling; direct tests must not overlap queued work.

- [AWS deployment and live-test plan](docs/plans/aws-deployment-happy-path.md):
  proposed deployment gates and one direct GitHub happy-path test.
- [Architecture](docs/architecture.md): components, queueing, and runtime flows.
- [Operations](docs/operations.md): configuration, trust, logs, and failure limits.
- [Interface contracts](docs/contracts.md) and [OpenAPI](openapi.yaml): submission
  mappings, repository scripts, and HTTP payloads.
- [Job records](docs/data-model.md) and [factory skills](docs/factory-skills.md):
  record semantics and the bundled agent stages.

## Repository structure

- [GLOSSARY.md](GLOSSARY.md): canonical domain vocabulary.
- [AGENTS.md](AGENTS.md): guidance for coding agents.
- [Makefile](Makefile), [pyproject.toml](pyproject.toml), and [uv.lock](uv.lock):
  local commands, configuration, and pinned development dependencies.
- `src/receiver/`, `src/dispatcher/`, `src/utils/`: signed issue ingestion, queued
  dispatch, and shared authorization/baseline utilities.
- `src/worker/`: snapshot-safe lifecycle server and worker verification,
  review, publication, and merge gates.
- `src/infra/`: native CDK stacks and Step Functions workflow definition.
- `src/tools/`: local logging, image-pin verification, deployment, and live-run tools.
- `src/tests/`: standard-library tests and explicit Docker/live checks.
- `image/`: ARM64 Dockerfile, pinned runtime dependencies, managed Codex policy,
  and factory skill bundle.
