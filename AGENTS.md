# Agent Instructions

## Repository Overview

This is an operator-led AWS Lambda MicroVM software factory PoC.
Never write implementation progress or verification evidence to any file during
the implementation of a story. After implementation is ready, record short 
implementation summary to [docs/slices/current-slice.md](docs/slices/current-slice.md).

Read [DECISIONS.md](DECISIONS.md) for accepted decisions,
[SPEC.md](SPEC.md) for behavior and stories, and [README.md](README.md) for onboarding.

## Repository Structure

- `worker/`: lifecycle/status server, asynchronous worker, and completion evidence.
- `infra/`: native Python CDK app/stacks and Standard workflow definition.
- `image/`: ARM64 Dockerfile and fixed demonstration acceptance check.
- `tools/logging_smoke.py`: non-production local logging check.
- `tools/verify_image.py`: read-only new-source/version pin verification.
- `tests/`: standard-library tests; `test_sandbox.py` runs explicitly in Docker.
- `Makefile`: shared setup and verification commands.
- `pyproject.toml`, `uv.lock`, `.python-version`: tooling configuration, pinned
  development dependencies, and local Python selection.
- `DECISIONS.md`, `CONTEXT.md`: accepted behavior and canonical naming.
- `docs/plans/`: proposed architecture and gate documentation.
- `docs/aws-lambda-microvms/`: copied AWS reference documentation.

## Development Workflow

Use Python 3.14, `uv`, Make, and Node.js/npm for CDK; jsii recommends Node 22/24
LTS. Ruff, coverage.py, CDK, and constructs are development-only dependencies
pinned in `uv.lock`; the local CDK CLI is pinned in `package-lock.json`. Worker
runtime code uses the standard library and image-installed CLIs.
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
```

## Domain Vocabulary

- Read `CONTEXT.md` before naming domain concepts.
- Use its canonical terms consistently in code, tests, documentation, and
  user-facing output.
- Resolve terminology conflicts before adding or renaming terms.
- Treat the accepted specification as authoritative for behavior and
  `CONTEXT.md` as authoritative for naming.
- Preserve public-interface compatibility; do not silently rename established
  CLI, JSON, or API fields.

## Commenting Convention

- Every production module and function must have a comment directly above it.
- The comment must state its purpose, key inputs/outputs, and externally visible
  effects or failure handling where applicable.
- Docstrings do not satisfy this rule.

Exemptions:

- Generated files.

## Change Guidelines

Run the relevant focused tests and `make verify` after changes, and report actual
results and gaps.