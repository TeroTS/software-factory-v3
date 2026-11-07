# Agent Instructions

## Repository Overview

This is an operator-led AWS Lambda MicroVM software factory PoC.
Keep README.md and AGENTS.md current for setup, navigation, and agent guidance;
put ticket-specific implementation details in the relevant ticket or docs.

## Repository Structure

- `src/receiver/`, `src/dispatcher/`, `src/utils/`: issue ingestion, queued dispatch,
  and shared authorization/baseline utilities.
- `src/worker/`: lifecycle/status server, worker, review, and completion gates.
- `src/infra/`: native Python CDK stacks and Standard workflow definition.
- `src/tools/`: local logging, read-only image-pin checks, deployment, and live-run tools.
- `src/tests/`: standard-library tests; sandbox, emulator, and live checks are explicit.
- `image/`: ARM64 Dockerfile, runtime dependencies, managed Codex policy, and skills.
- `Makefile`: shared setup and verification commands.
- `pyproject.toml`, `uv.lock`, `.python-version`: tooling configuration, pinned
  development dependencies, and local Python selection.
- `docs/architecture.md`, `docs/running.md`, `docs/operations.md`: current flows,
  commands, and operational limits.
- `openapi.yaml`, `docs/contracts.md`: HTTP schemas and non-HTTP boundaries.
- `docs/plans/`: planning material; check each document's status.
- `docs/aws-lambda-microvms/`: copied AWS reference documentation.

## Development Workflow

Use Python 3.14, `uv`, Make, and Node.js/npm for CDK; jsii recommends Node 22/24
LTS. Ruff, coverage.py, CDK, and constructs are development-only dependencies
pinned in `uv.lock`; the local CDK CLI is pinned in `package-lock.json`. Worker
runtime uses Python 3.13, the standard library, pinned boto3, and image-installed
CLIs.
Run commands from the repository root:

```sh
make setup
make verify
```

Individual commands:

```sh
make format-check
make lint
make test
make coverage
make dev
make format
make synth
make preflight
make floci-test
make floci-down
```

Deployment, live inference, and GitHub writes require separate authorization;
local verification does not require credentials. See `docs/running.md` before
using live tools.

## Agent skills

### Issue tracker

Tickets live under `.scratch/<feature>/`. Before reading or writing tickets, read `docs/agents/issue-tracker.md`.

### Triage labels

Use the five default triage roles. Before triaging, read `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md`; ADRs live in `docs/adr/` if present. Before exploring
terms or decisions, read `docs/agents/domain.md`.

## Commenting Convention

- Every production module and function must have a comment directly above it.
- The comment must state its purpose, key inputs/outputs, and externally visible
  effects or failure handling where applicable.
- Docstrings do not satisfy this rule.

Exemptions:

- Generated files.

## Change Guidelines

Keep `make test` under 30 seconds locally: use focused gate tests for failure
matrices and retain representative real Git/process integration checks.
For code changes, run relevant focused tests and `make verify`; report actual
results and gaps. For documentation-only changes, check affected links,
formatting, and diagrams without running tests.

Run `make synth` only for CDK/infrastructure, asset-staging, or deployment
configuration changes. Routine application-code or skill-content edits do not
require synthesis merely because those files are packaged as deployment assets.
