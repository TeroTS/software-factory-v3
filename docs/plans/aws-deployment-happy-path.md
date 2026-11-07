# AWS deployment and one live GitHub happy-path test

**Status:** Proposed; execution requires separate operator authorization.
No live AWS or GitHub state has been inspected for this plan.

## Goal and scope

Deploy the current factory source to AWS, then prove that one Job implements a
small change, passes verification and fresh review, publishes and merges its PR,
and leaves its MicroVM service-confirmed `TERMINATED`.

Proposed test entry: the existing **direct Step Functions test**, not an issue
webhook. This isolates the live image/model/GitHub/lifecycle path and gives us a
retained execution identity for interruption recovery. It does **not** establish
live webhook receipt, queue claim, dispatcher handoff, or queue completion.
A webhook-based end-to-end test would be a separately approved follow-up.

Use existing [live tools](../../src/tools/live.py), [running instructions](../running.md),
and [operating limits](../operations.md). No new deployment framework, model
change, branch-policy change, or extra billable trial run is planned.

## 1. Confirm destination, trust, and authorization

Before any cloud mutation, confirm:

- AWS account `172318459814`, Region `us-east-1`, and operator profile
  `AWSAdministratorAccess-172318459814` (or an explicitly selected profile for
  the same account). The tools reject another account.
- Authorized repository `TeroTS/factory-smoke-test`, target `main`, and permission
  for **one billable Job and an actual merge commit into that target**.
- Lambda MicroVMs availability/quotas and Bedrock access for the
  [configured model and runtime role](../../src/infra/image_stack.py).
  Do not switch models or broaden permissions to work around an access failure
  without approval. These prerequisites are not proven by offline checks.
- Operator GitHub authentication can inspect the repository, PR, and diff.
  The separate worker token is repository-scoped and permits the required
  branch publication, PR creation, and merge; existing GitHub protection permits
  that workflow without an administrative bypass.
- Trusted target baseline has executable `scripts/setup` and `scripts/verify`,
  satisfies the [repository contract](../contracts.md), and can run under the
  Linux ARM64 worker. Review scripts because they execute with runtime credentials.
- Agreed cost/observation budget. Image builds, model inference, MicroVM runtime,
  and persistent AWS resources are not free; the observer is not a cost cutoff.

Inspect existing stacks, accepted work, executions, and VM lifecycle read-only.
Keep webhook submissions disabled throughout deployment and the direct test.
Confirm the queue is empty and no Job/VM is active; the scripts' idle guard checks
running workflows/VMs, **not** queued items or dispatcher concurrency.
Drain older accepted SQS/Pipe work before an update that removes those resources.
Stop on unapproved resource deletion/replacement, secret replacement, or trust
expansion in CDK diffs. Do not delete queue records to create an idle state.

**Gate:** Approve the destination and reviewed deployment changes; approve the
live Job separately immediately before submission.

## 2. Validate source and deploy artifacts/image

Record the source revision and any intentional uncommitted changes. Keep source
unchanged from validation through image pin verification and test submission.
Run `make setup` only if the declared local dependencies are not installed.

```sh
uv run --locked python -m src.tools.validate_deploy_image
```

This runs `make verify preflight synth`, authenticates the AWS account, checks
idle state, shows the artifacts/image diff, and prompts before deployment.
Review IAM/trust and resource changes before answering `yes`.
It deploys `FactoryArtifacts` followed by `FactoryImage`, then verifies the
exact current-source image is `SUCCESSFUL/ACTIVE`.

Retain the printed image version as `IMAGE_VERSION`, source revision, and stack
identities. Never substitute an older active image if this build fails.
A failed image deployment uses `--no-rollback`; inspect its stack/build state
before any separately authorized recovery.

**Gate:** All local checks pass and the exact-source image pin verifies.

## 3. Resolve secrets and deploy workflow/ingress

Read `FactoryImage`'s `GitHubSecretArn` output. Through an approved private
operator path, populate the empty retained secret with the scoped GitHub token
as a **plain token SecretString**, not a JSON object. Preserve an existing valid
secret unless replacement is explicitly authorized. Do not print token values.

**Fresh-deployment blocker:** `FactoryWorkflow` requires the NoEcho parameter
`WebhookSigningSecret`; the existing `deploy_infra` tool does not collect or
supply it. Before invoking the tool on a new stack, agree and validate a private
parameter-provisioning path. If that needs a small secure-input tool change,
approve it separately, test it, and rerun applicable validation before proceeding.
No secret literals belong in source, command arguments/history, or captured
terminal output. For an existing stack, confirm the approved parameter is retained.
A direct test does not require enabling a GitHub webhook, but stack creation
still requires this parameter.

With the secret prerequisite resolved and the verified version set:

```sh
uv run --locked python -m src.tools.deploy_infra --version "$IMAGE_VERSION"
```

Review the workflow/ingress/IAM diff and approve only the intended deployment.
The tool validates the prospective definition, deploys `FactoryWorkflow`, and
checks the actual `ACTIVE` Standard definition, explicit source pin, and
`AUTHORIZED` EventBridge Connection. It starts no Job.
Retain stack outputs, including state machine, queue, and webhook URL; do not
configure or enable webhook delivery for this test.

**Gate:** Workflow verification passes; token and target configuration are ready;
queue/executions/VMs remain idle.

## 4. Prepare and submit exactly one Job

Review and test the current target baseline in the authorized repository; record
its full SHA. Inspect whether the fixture's multiply task is still a meaningful
nonempty change. If already implemented, agree another equally small task before
submission rather than knowingly launching a no-op. Freeze target writes during
preparation/test to keep the evidence simple; do not change branch protection.

Create the execution identity and copied input **once**, using the preparation
commands in [One direct test](../running.md#one-direct-test). Replace the copied
file's `baseSha` with the reviewed full SHA and review its task text. Retain the
execution name, file path, and exact file contents securely; do not modify the
repository's [template](../../src/tools/happy-path-job.json).

```sh
uv run --locked python -m src.tools.run_happy_path \
  --name "$EXECUTION_NAME" --job-file "$JOB_FILE"
```

Confirm the repository/target and explicitly authorize the model work,
reviewed PR publication, and merge when prompted. The tool validates the input,
baseline, deployed workflow/image, and idle state before submission.

Observe the same execution. After interruption or uncertain acknowledgement,
rerun **only this command with the same name and unchanged file**. Do not repeat
preparation, choose another name, or submit a replacement Job.
The observer makes no automatic workflow-stop or VM-termination request.

## 5. Accept only complete evidence

The existing observer must report `PASS` with:

- Workflow `SUCCEEDED` and exact final `ok`/`TERMINATED` output for its VM.
- Exactly one launch, auth-token creation, and termination task; authenticated
  HTTP 200 status polls using the same nonempty token and ending in `ok`.
- Independent repository verification with exit code zero, fresh review PASS,
  unchanged reviewed candidate, worker merge authorization, and merged confirmation.
- A nonempty published change descended from the pinned baseline, with the
  matching reviewed head and PR targeting the configured repository/branch.
- GitHub reporting that bound PR `MERGED`, and AWS reporting the same VM
  `TERMINATED` (not merely a successful termination request).

Inspect the printed PR diff for the agreed change and tests. Record source SHA,
image version, baseline SHA, execution ARN, VM ID, PR URL/reviewed head, final
workflow/lifecycle states, and check results in a dated private run record.
Keep raw execution history private: it can contain authentication tokens.

## Stop and recovery

On any failed gate, do not proceed or launch another Job. Inspect the retained
execution/VM and redacted CloudWatch stage evidence using
[recovery guidance](../operations.md#failure-inspection-and-recovery-boundaries).
A terminal workflow is not proof of cleanup; timeout/manual stop bypasses normal
cleanup, with a 30-minute VM lifespan only as fallback. Unconfirmed cleanup,
ambiguous merge/publication, manual termination, or resource rollback requires
inspection and separately authorized action, not automatic retries or deletion.

Leave webhook delivery disabled and report retained resources/cost exposure after
the test. Teardown, credential rotation, enabling ingress, and another live test
are separate decisions; this plan does not authorize them.
