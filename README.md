# software-factory-v3

A proof of concept for turning one coding task into a tested GitHub PR inside an
AWS Lambda MicroVM, then confirming VM termination.

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
checks Python formatting, lint, tests through coverage.

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
| `make floci-test` | Run local signed-webhook/SQS/Pipes/Standard tests and report known MicroVM lifecycle gaps; requires Docker. |
| `make floci-down` | Stop the task-local emulator and GitHub fixture containers. |

The separate demonstration repository,
[TeroTS/factory-smoke-test](https://github.com/TeroTS/factory-smoke-test).

### Live AWS/GitHub test

```sh
uv run --locked python -m tools.validate_deploy_image
# Set IMAGE_VERSION to the verified version printed by the first stage.
uv run --locked python -m tools.deploy_infra --version "$IMAGE_VERSION"
```

You choose `EXECUTION_NAME`: it is a unique label and AWS idempotency key, not an
ID obtained from AWS. For a **new Job only**, generate one and copy the
[example Job](tools/happy-path-job.json) to a temporary absolute path:

```sh
EXECUTION_NAME="happy-path-$(uv run --locked python -c 'import uuid; print(uuid.uuid4().hex)')"
JOB_FILE="$(mktemp "${TMPDIR:-/tmp}/factory-happy-path.XXXXXX")"
cp tools/happy-path-job.json "$JOB_FILE"
printf 'Retain EXECUTION_NAME=%s\nRetain JOB_FILE=%s\n' "$EXECUTION_NAME" "$JOB_FILE"
```

Edit the copied file: replace `baseSha` with the full 40-character SHA of your
reviewed, tested baseline. Obtain it with `git rev-parse HEAD` in the target
repository.

```sh
uv run --locked python -m tools.run_happy_path \
  --name "$EXECUTION_NAME" --job-file "$JOB_FILE"
```

After interruption, reuse the **same name and unchanged Job file**; do not rerun
the preparation commands or generate a new name. Retain the printed values and
file for recovery. The script checks the PR and confirmed VM termination; it
never merges.

## Repository structure

- [CONTEXT.md](CONTEXT.md): canonical domain vocabulary.
- [AGENTS.md](AGENTS.md): guidance for coding agents.
- [Makefile](Makefile), [pyproject.toml](pyproject.toml), and [uv.lock](uv.lock):
  local commands, configuration, and pinned development dependencies.
- `tools/`: non-production development smoke check.
- `tests/`: standard-library worker/lifecycle/infrastructure tests.
- `receiver/`: signed GitHub issue receiver; queues pinned Jobs without changing worker interfaces.
- `worker/`: snapshot-safe lifecycle HTTP server, asynchronous worker, and
  command/source/publication evidence verification.
- `image/`: package-managed ARM64 Dockerfile and fixed demonstration acceptance.
- `infra/`: native CDK app/stacks and Step Functions ASL.
