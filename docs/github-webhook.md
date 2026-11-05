# Issue-triggered Jobs: local wiring and verification

**Status: partially implemented; full Floci Job demonstration is blocked.**
No AWS deployment, live webhook configuration, real PR, or model call is authorized
by this stage.

## Flow and configuration

GitHub POST → public Lambda Function URL → prepared Job in SQS → EventBridge Pipes
→ the existing Step Functions Standard workflow.

[openapi.yaml](../openapi.yaml) owns the new `/` webhook HTTP boundary and existing
Job/worker schemas. The receiver verifies GitHub's HMAC-SHA256 over the original
bytes with constant-time comparison. Only `issues`/`opened` for the exact
configured repository requests work; PRs and other events/actions enqueue nothing.
The title and received body become the Agent task; comments and later edits are not
fetched. The target branch is resolved at **receipt**, not event creation, to a
full SHA. Oversized tasks are rejected before lookup under the existing 16 KiB
worker payload limit.

Native CDK keeps the existing `FactoryWorkflow` state machine and image version
pin. Its additional resources are the receiver/Function URL, queue/DLQ, Pipe,
scoped roles, and receiver logs. No dispatcher or new production secret resource
is introduced. Receiver assets use the existing artifact bucket; image/worker
sources, lifecycle, verification, and manual Job submission/observation are unchanged.

| Deployment setting | Source / purpose |
| --- | --- |
| `AUTHORIZED_REPOSITORY` | Shared `FactoryImage.AuthorizedRepository` parameter; payload cannot select authorization. |
| `PR_TARGET_BRANCH` | Shared `FactoryImage.PrTargetBranch` parameter; payload cannot select the publication target. |
| `GITHUB_SECRET_ARN` | Existing repository-scoped GitHub token, reused for baseline lookup. |
| `WEBHOOK_SECRET` | Required `FactoryWorkflow.WebhookSigningSecret` NoEcho parameter, placed directly in Lambda environment. |
| `JOB_QUEUE_URL` | Queue created by CDK, not selected by the request. |

Synthesis contains a signing-secret **reference**, never a real signing secret.
A later approved deployment must supply the secret privately; do not put it in
source, shell history, command arguments, logs, or stack outputs. NoEcho does not
hide it from identities authorized to read Lambda configuration. The existing
image pin remains required. The current deployment helper does not collect the
new secret: approved live deployment preparation must address that separately.

## Local commands

Prerequisites: the normal repository toolchain plus Docker with Compose and a
working Docker socket. Nothing is installed globally; no live credentials are
needed. From the repository root:

```sh
make setup
make synth
make verify
make floci-test
make floci-down
```

`make floci-test` starts [compose.floci.yaml](../compose.floci.yaml), waits for
Floci's version health check, then runs `tests/test_floci.py`. `make floci-up`
starts the same environment without tests. Stop it with `make floci-down`.
Docker downloads emulator/fixture/Lambda runtime images as needed. The Docker
socket gives the emulator control over Docker; use a trusted development daemon.
The published service port is bound to loopback, `http://127.0.0.1:14566`.

Floci **2.4.0** and the Python GitHub fixture image are pinned by digest. Host SDK
clients use the fixed loopback endpoint, explicit `test` credentials, and no HTTP
proxy. Lambda uses only `http://floci:4566` with test credentials and the local
GitHub server at `http://github:8080`; the two local overrides must be present
together and fail closed for missing/other values. Lambda and GitHub fixture
containers use an internal Docker network without external egress. Floci also
joins a separate ingress network so the loopback port remains reachable. These overrides are absent from production CDK. The fixture uses synthetic
signing/token values, a literal baseline SHA, and no host credential mounts. It
never connects to GitHub. Function URL requests retain Floci's returned Host
routing and connect over loopback without external DNS. Tests provision resources
via AWS SDK calls; CDK synthesis separately verifies production wiring/permissions.

The test first captures a prepared queue message with Pipes stopped and deletes
that inspected delivery. A second signed HTTP delivery exercises SQS → Pipes →
Standard startup without consuming its queue message manually. Pipes passes the
Job object via `<$.body>`, batch size one, `FIRE_AND_FORGET` invocation. Tests also
cover basic rejections and actual one-receive DLQ redrive. Resources created by
each test are cleaned up; Compose remains available until `make floci-down`.

## Actual Floci compatibility results

Verified against the pinned 2.4.0 image:

| Required operation / behavior | Local result |
| --- | --- |
| Signed HTTP Function URL invocation with UTF-8 and base64 transport | Passed against a real Docker Lambda runtime. |
| Existing token lookup, local GitHub branch lookup, prepared Job in SQS | Passed; exact repository/SHA/task asserted. |
| Pipes batch/input mapping and asynchronous Standard startup | Passed; workflow input is one unchanged Job object. |
| Signature/repository/event/action/PR/malformed-task/baseline rejection | Passed; no queued Job or execution. |
| Queue `maxReceiveCount: 1` redrive | Passed with an unacknowledged receive. |
| Later workflow failure after acknowledged startup | Failed Job stays out of DLQ, as expected. |
| Standard `aws-sdk:lambdamicrovms:runMicrovm` integration | **Unsupported**; task history reports `Unsupported resource: arn:aws:states:::aws-sdk:lambdamicrovms:runMicrovm`. |
| Direct MicroVM control plane | Returns synthetic state and an AWS-shaped endpoint, not a running worker. Tests never connect to that hostname. |
| `CreateMicrovmAuthToken` | **Unsupported**, HTTP 404 in a direct SDK compatibility probe. |
| Worker endpoint, local publication evidence, confirmed real VM termination | **Not verified through Floci; blocked by the above limitations.** |

The existing ASL is used unchanged apart from resolving deployment references
(partition, image/version, roles, connection, log group). No task resource or AWS
hop is mocked or replaced. The integration test asserts the known launch failure
rather than treating any failure as acceptable. A green `make floci-test` therefore
means **supported ingress/handoffs and known compatibility gaps**, not a successful
end-to-end Job. Full ticket acceptance remains blocked.

[Floci's versioned MicroVM documentation](https://github.com/floci-io/floci/blob/2.4.0/docs/services/lambda-microvms.md#emulation-behavior)
explicitly excludes auth tokens and runnable per-VM endpoints. Existing
`tests/test_job_outcomes.py` independently exercises real local worker/repository
components with external service/agent/publication fixtures; it is **not Floci
integration evidence**. No duplicate replacement lifecycle fixture was added.

`make verify` skips the three Docker-only Floci tests and existing sandbox tests;
it cannot prove emulator integration. Host coverage includes the receiver but
cannot measure code executed inside Floci's Lambda containers: receiver coverage
is reported as 0%, and aggregate coverage falls from 96% on a fresh run of the starting commit to 86%
when including it. Existing measured module coverage is preserved/improved; no coverage
gate or threshold was lowered. There is no configured typechecker; Ruff checks
the receiver against Python 3.13 syntax, and CDK synthesis checks construct types.
Run `make preflight` separately for the ARM64 sandbox check (not needed for this
unchanged worker/image source).

## Operation once separately approved for deployment

Opening an issue in the authorized repository requests **billable work**. Anyone
able to open an issue can request it. Signature verification authenticates GitHub
delivery, **not issue-author authority**.

Operate one Job at a time across issue-triggered **and manual** submissions. Wait
for Job completion and **confirmed VM termination** before requesting more work.
This is operator discipline, not a queue/workflow concurrency lock. Managed
services may deliver duplicates or start concurrent Jobs; no exactly-once claim,
idempotency layer, or application retries are provided.

The DLQ redrive threshold is one receive, not a guarantee of one attempt. Inspect
and recover DLQ messages manually. Workflow startup acknowledgement is neither
completion nor termination evidence: failures **after** acknowledged startup do
not return the consumed message to the queue or DLQ. Existing AWS/workflow
mechanisms and manual recovery remain in place.
