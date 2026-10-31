# software-factory-v3

AWS Lambda MicroVM software factory PoC. The accepted scope is in
[DECISIONS.md](DECISIONS.md); naming is defined in [CONTEXT.md](CONTEXT.md).

## Development setup

Install Python 3.14 and `uv`, then run:

```sh
make setup
make verify
```

`uv.lock` pins the development-only Ruff and coverage.py dependencies. The
factory has no runtime dependencies yet; the separate demonstration repository
remains dependency-free. Local Python selection does not verify MicroVM runtime
compatibility.

## Developer commands

| Command | Purpose |
| --- | --- |
| `make setup` | Create/sync `.venv` from the lockfile. |
| `make format` | Format Python files. |
| `make format-check` | Check Python formatting without changes. |
| `make lint` | Run Ruff lint checks. |
| `make test` | Run standard-library `unittest` discovery. |
| `make coverage` | Run tests with branch coverage and report missing lines. |
| `make dev` | Run the local logging smoke check; no AWS calls. |
| `make verify` | Check formatting, lint, tests via coverage, and local logging. |

## Bootstrap scope

- `tools/logging_smoke.py` verifies standard-library console logging at `INFO`
  level to stderr. It emits a fixed message, not credentials or job data. Runtime
  log delivery to CloudWatch is not implemented by this bootstrap.
- `tests/` contains the logging bootstrap test.
- Coverage currently measures only the non-production `tools/` code. Add worker
  sources to the coverage configuration when implementation begins; this report
  is not evidence of agent, worker, or workflow coverage.

No worker, routes, demonstration fixture, containers, CI, or AWS deployment
resources are included. Gate 1 and Gate 2 remain unverified.
