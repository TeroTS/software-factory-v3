# software-factory-v3

A proof of concept for turning one coding task into a tested GitHub PR inside an
AWS Lambda MicroVM, then confirming VM termination.

**Current implementation:** a local development bootstrap and logging smoke
check only. The coding worker, AWS orchestration, deployment resources, and
separate demonstration fixture are not implemented here. Both live gates remain
unverified.

Accepted requirements are in [DECISIONS.md](DECISIONS.md); domain naming is in
[CONTEXT.md](CONTEXT.md).

## Quick start

Prerequisites: Python 3.14, `uv`, and Make. Setup and verification have been tested
with Python 3.14.3 and `uv` 0.5.13. No AWS or GitHub credentials are needed for
these local checks.

Run from the repository root:

```sh
make setup
make verify
```

Setup creates `.venv` using the dependencies pinned in `uv.lock`. Verification
checks Python formatting, lint, tests through coverage, and the logging smoke
check. Successful output includes a passing test and:

```text
INFO Local logging check passed.
```

The local Python version does not establish the MicroVM's runtime compatibility.

## Development

| Command | Purpose |
| --- | --- |
| `make setup` | Sync `.venv` from the lockfile. |
| `make format` | Format Python files; modifies files. |
| `make format-check` | Check Python formatting without changes. |
| `make lint` | Run Ruff lint checks. |
| `make test` | Run all discovered `unittest` tests. |
| `make coverage` | Run tests with branch coverage and report missing lines. |
| `make dev` | Run the local logging smoke check; not an application server. |
| `make verify` | Run formatting checks, lint, coverage/tests, and logging. |

Make commands use `uv run --locked`, so checks use the project environment rather
than a globally installed Ruff or coverage.py. Ruff checks Python files only.
Ruff and coverage.py are development dependencies; no runtime dependencies are
currently declared. No type-check, build, or CI workflow is configured.

The separate demonstration repository,
[TeroTS/TeroTS-factory-smoke-test](https://github.com/TeroTS/TeroTS-factory-smoke-test),
remains dependency-free; its setup must not be confused with this factory's
`uv` environment.

## Testing and coverage

Tests live in `tests/` and use the standard-library `unittest` runner. Run the
focused logging test from the repository root with:

```sh
uv run --locked python -m unittest discover -s tests -p test_logging_smoke.py -v
```

The current test verifies the logging smoke check's `INFO` level and exact stderr
output. There are no worker, integration, or end-to-end tests yet.

Coverage is configured in [pyproject.toml](pyproject.toml):

- `source = ["tools"]` measures only the non-production bootstrap code.
- Branch coverage is enabled; missing lines appear in the report.
- No minimum coverage percentage is enforced (`fail_under` is not configured).

A 100% bootstrap result is not evidence of whole-factory coverage. Extend the
source scope when worker code is introduced; do not leave new application code
outside the report.

## Local logging and configuration

[tools/logging_smoke.py](tools/logging_smoke.py) uses standard-library logging at
`INFO` level on stderr. It emits a fixed message, not credentials or job data,
and makes no AWS calls. There is no local service or required application
environment variable. CloudWatch delivery is planned, not implemented by this
bootstrap.

## Repository structure

- [DECISIONS.md](DECISIONS.md): accepted scope, demonstration inputs, and two gates.
- [CONTEXT.md](CONTEXT.md): canonical domain vocabulary.
- [AGENTS.md](AGENTS.md): guidance for coding agents.
- [Makefile](Makefile), [pyproject.toml](pyproject.toml), and [uv.lock](uv.lock):
  local commands, configuration, and pinned development dependencies.
- `tools/`: non-production development smoke check.
- `tests/`: bootstrap tests.
- [PoC plan](docs/plans/aws-lambda-microvm-software-factory-poc.md): architecture
  and gate details. Accepted decisions supersede its earlier response shapes
  and proposals where explicitly stated.
- `docs/aws-lambda-microvms/`: local copies of AWS reference documentation.

## Maintenance and safety

Update this README and relevant agent guidance when setup, configuration,
interfaces, or architecture change. Review dependency changes before updating
`uv.lock` with `uv lock`; normal setup and checks do not silently update it.

Future AWS and GitHub runs require the manual access and scoped credentials
listed in [DECISIONS.md](DECISIONS.md). Keep secret values out of committed files,
job input, prompts, and logs. Local verification does not deploy resources,
publish branches, or prove either live gate.
