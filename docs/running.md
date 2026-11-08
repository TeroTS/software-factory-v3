# Running and verification

## Local development

From the repository root, use Python 3.14, `uv`, Make, and Node.js/npm (jsii
recommends Node 22/24 LTS). Lockfiles pin development dependencies and the local
CDK CLI. Runtime images and ingress Lambdas use Python 3.13.

```sh
make setup
make verify
```

`make verify` runs Ruff format/lint checks, unittest through branch coverage, and
the logging smoke check. `make synth` produces local templates/assets without
AWS lookups or deployment. `make dev` is only a logging check, not a server.

Before synthesis, set `AWS_ACCOUNT_ID` to the target's 12-digit account ID and
`AWS_REGION` to the deployment Region; neither has a fixed default:

```sh
export AWS_ACCOUNT_ID="<target-account-id>"
export AWS_REGION="<deployment-region>"
make synth
```

For offline review, synthetic account/Region values are sufficient; credentials
are not required. MicroVMs and the configured Bedrock inference profile must be
available in the selected Region before deployment.

| Command | Purpose |
| --- | --- |
| `make format-check`, `make lint` | Non-mutating Python checks. |
| `make test`, `make coverage` | Standard-library tests; Docker-only cases skip unless explicitly enabled. |
| `make preflight` | Build ARM64 image; run offline tool/sandbox, discovery/policy, and simulated-service worker/server checks. Requires Docker with ARM64 support. |
| `make floci-test` | Start pinned Docker fixtures; test signed HTTP receipt, DynamoDB/Streams dispatcher handoff, and real Standard entry states. |
| `make floci-down` | Stop local emulator/fixture containers. |

`make test` targets under 30 seconds locally. Failure matrices exercise worker
validation gates directly; representative integration tests retain real Git,
script execution, agent subprocesses, and lifecycle HTTP checks. The default
suite omits repeated cross-process whole-Job outcome matrices.

Focused worker/queue checks:

```sh
uv run --locked python -m unittest src.tests.test_receiver src.tests.test_dispatcher src.tests.test_worker src.tests.test_review src.tests.test_workflow -v
```

Floci exposes only loopback `http://127.0.0.1:14566`. It is not a full deployed
factory: tests use a local GitHub fixture, real workflow entry states, and
simulated MicroVM/HTTP lifecycle tasks. No model inference or live GitHub merges
are performed. `make verify` needs no AWS/GitHub credentials.

For first-run errors, run `make setup` to sync lockfiles, check the selected
Python/Node versions, and start Docker before preflight/Floci. If nested sandbox
validation fails, use the Makefile's existing Docker seccomp setting; do not
disable Codex's own sandbox to make the check pass.

## Operator-only live tools

These commands are documented for separately authorized use, not part of local
verification. They authenticate the configured AWS account, inspect current
source/deployment, and prompt before deployment or a new billable Job. They use
the same `AWS_ACCOUNT_ID` / `AWS_REGION` as synthesis and verify the authenticated
account matches the configured target before live actions. Set `AWS_PROFILE` to
your operator profile, or pass `--profile` explicitly to each command:

```sh
export AWS_PROFILE="<operator-profile>"
uv run --locked python -m src.tools.validate_deploy_image
# Set IMAGE_VERSION to the verified SUCCESSFUL/ACTIVE version printed above.
uv run --locked python -m src.tools.deploy_infra --version "$IMAGE_VERSION"
```

The first command runs local verification/preflight/synthesis before deploying
storage/image; the second verifies the explicit pin and deploys workflow
infrastructure without starting a Job. The image stack creates an empty,
retained GitHub-token secret that the operator must populate before accepting
work. Workflow deployment requires `WebhookSigningSecret`; `deploy_infra` does
not collect or supply it on a fresh deployment. Provision it privately through
an approved deployment path, then configure GitHub's webhook with the same secret
and the `WebhookUrl` stack output. Do not put secrets in source or shell history.
See [operations](operations.md) before enabling ingress.

### One direct test

Ensure the queue is empty and no Job/VM is active; prevent webhook submissions
until this test's VM termination is confirmed. The live idle check inspects
running executions and VMs, not queued items or dispatcher concurrency.

For a **new Job only**, retain one name and an unchanged singleton-array input:

```sh
EXECUTION_NAME="happy-path-$(uv run --locked python -c 'import uuid; print(uuid.uuid4().hex)')"
JOB_FILE="$(mktemp "${TMPDIR:-/tmp}/factory-happy-path.XXXXXX")"
cp src/tools/happy-path-job.json "$JOB_FILE"
printf 'Retain EXECUTION_NAME=%s\nRetain JOB_FILE=%s\n' "$EXECUTION_NAME" "$JOB_FILE"
```

Edit the copied file's `baseSha` to the full SHA of the reviewed/tested target
baseline (`git rev-parse HEAD` in that repository), then run:

```sh
uv run --locked python -m src.tools.run_happy_path \
  --name "$EXECUTION_NAME" --job-file "$JOB_FILE"
```

A new run explicitly authorizes model work, reviewed PR publication, **and a
merge commit into the configured target**. The script itself observes GitHub;
the worker performs the gated merge. It checks reviewed merged-PR evidence and
service-confirmed VM termination, and prints the PR diff.

After interruption, rerun only the final command with the **same name and
unchanged file**, not new preparation commands. Existing executions are observed
rather than replaced. No automatic retry, workflow stop, or VM termination is
requested by this observer. Direct tests retain their supplied baseline; queued
Jobs refresh theirs at claim time.
