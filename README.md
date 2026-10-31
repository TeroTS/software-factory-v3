# software-factory-v3

A proof of concept for turning one coding task into a tested GitHub PR inside an
AWS Lambda MicroVM, then confirming VM termination.

Implementation progress and live gate evidence belong only in
[the current slice](docs/slices/current-slice.md).

Accepted requirements are in [DECISIONS.md](DECISIONS.md); domain naming is in
[CONTEXT.md](CONTEXT.md).

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
checks Python formatting, lint, tests through coverage, and the logging smoke
check. Successful output includes a passing test and:

```text
INFO Local logging check passed.
```

The local Python version does not establish the MicroVM's runtime compatibility.

## Development

| Command | Purpose |
| --- | --- |
| `make setup` | Sync `.venv` and the repository-local CDK CLI from lockfiles. |
| `make format` | Format Python files; modifies files. |
| `make format-check` | Check Python formatting without changes. |
| `make lint` | Run Ruff lint checks. |
| `make test` | Run all discovered `unittest` tests. |
| `make coverage` | Run tests with branch coverage and report missing lines. |
| `make dev` | Run the local logging smoke check; not an application server. |
| `make verify` | Run formatting checks, lint, coverage/tests, and logging. |
| `make synth` | Synthesize native CDK templates/assets locally; no deployment. |
| `make preflight` | Build the ARM64 image and check real sandbox/search/edit/tests/Git plus simulated-service worker/lifecycle tests. |

Make commands use `uv run --locked`, so checks use the project environment rather
than a globally installed Ruff or coverage.py. Ruff checks Python files only.
Ruff, coverage.py, CDK, and constructs are development-only dependencies. Worker
runtime code uses the standard library and image-installed CLIs. No type-check
or CI workflow is configured. CDK deploy/diff commands are repository-local
`npm run deploy -- ...` / `npm run diff -- ...`; these require AWS access and
separate operator authorization. Deployment ordering and the verified image pin
are recorded in the current slice, not inferred from local synthesis.

The separate demonstration repository,
[TeroTS/factory-smoke-test](https://github.com/TeroTS/factory-smoke-test),
remains dependency-free; its setup must not be confused with this factory's
`uv` environment.

## Testing and coverage

Tests live in `tests/` and use the standard-library `unittest` runner. Run the
focused logging test from the repository root with:

```sh
uv run --locked python -m unittest discover -s tests -p test_logging_smoke.py -v
```

Tests cover the worker's completion evidence, lifecycle HTTP/process behavior,
image pinning, native CDK definitions, and simulated workflow paths. External
AWS/model/GitHub services are simulated locally; those tests are not live gate
acceptance. `make preflight` additionally runs the explicit real Linux sandbox
test, which is deliberately skipped in host-only `make verify`.

Coverage is configured in [pyproject.toml](pyproject.toml):

- `source = ["tools", "worker", "infra", "image"]` includes worker, native CDK,
  local tools, and the fixed demonstration acceptance check.
- Branch coverage is enabled; missing lines appear in the report.
- No minimum coverage percentage is enforced (`fail_under` is not configured).

Local tests/synthesis do not deploy AWS resources, call the model, publish work,
or establish live runtime compatibility.

## Local logging and configuration

[tools/logging_smoke.py](tools/logging_smoke.py) uses standard-library logging at
`INFO` level on stderr. It emits a fixed message, not credentials or job data,
and makes no AWS calls. Worker deployment supplies `WORKSPACE`, `AWS_REGION`,
`GITHUB_SECRET_ARN`, and `EXPECTED_RUNTIME_ROLE`; no credential values belong in
those settings. Lambda MicroVMs forwards available worker stdout/stderr to the
scoped CloudWatch group. Never forward host profiles/credentials to preflight.

## Repository structure

- [DECISIONS.md](DECISIONS.md): accepted scope, demonstration inputs, and two gates.
- [CONTEXT.md](CONTEXT.md): canonical domain vocabulary.
- [AGENTS.md](AGENTS.md): guidance for coding agents.
- [Makefile](Makefile), [pyproject.toml](pyproject.toml), and [uv.lock](uv.lock):
  local commands, configuration, and pinned development dependencies.
- `tools/`: non-production development smoke check.
- `tests/`: standard-library worker/lifecycle/infrastructure tests.
- `worker/`: snapshot-safe lifecycle HTTP server, asynchronous worker, and
  command/source/publication evidence verification.
- `image/`: package-managed ARM64 Dockerfile and fixed demonstration acceptance.
- `infra/`: native CDK app/stacks and Step Functions ASL.
- [PoC plan](docs/plans/aws-lambda-microvm-software-factory-poc.md): architecture
  and gate details. Accepted decisions supersede its earlier response shapes
  and proposals where explicitly stated.
- `docs/aws-lambda-microvms/`: local copies of AWS reference documentation.

## Maintenance and safety

Update this README and relevant agent guidance when setup, configuration,
interfaces, or architecture change. Review dependency changes before updating
`uv.lock` with `uv lock` and the CLI lockfile through npm; normal setup/checks
must not silently update lockfiles.

Future AWS and GitHub runs require the manual access and scoped credentials
listed in [DECISIONS.md](DECISIONS.md). Keep secret values out of committed files,
job input, prompts, and logs. Local verification does not deploy resources,
publish branches, or prove either live gate.
