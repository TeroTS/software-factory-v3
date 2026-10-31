# Gate 2 — Native Python CDK and a clean implementation

## Approved direction

Gate 1 is a disposable feasibility experiment, not the implementation to carry
forward. Gate 2 uses **native Python CDK definitions for all infrastructure**.
Do not use `CfnInclude`, import the Gate 1 CloudFormation template, or migrate
ownership of the old stack. The old template may be consulted for configuration
and scoped permissions, but is not a deployment input.

These operator-approved reset decisions supersede the earlier plan to reuse
the deployed Gate 1 image and worker unchanged. They do not change the approved
architecture: one Codex invocation owns editing, testing, fixes, and publication
inside one MicroVM; Step Functions manages the VM lifecycle, not the agent loop.
Implementation status and verification evidence are recorded only in
[the current slice](../slices/current-slice.md).

## Remove, recreate, and preserve

The chosen transition is **remove and recreate**, not resource adoption.
Deletion is a separate operator-controlled action, not something to perform
merely because this plan exists.

| Item | Gate 2 treatment |
| --- | --- |
| Gate 1 stack `software-factory-poc-gate1` | Delete; replace with native Python CDK infrastructure. |
| Old artifact bucket and its contents | Delete; create a new private artifact bucket. |
| Old build/runtime roles | Delete; recreate scoped roles through CDK. |
| Old CloudWatch log group and its streams | Delete; recreate logging through CDK. Old runtime logs will be lost. |
| Old MicroVM image `software-factory-poc`, including its versions | Delete separately from the stack; build and pin a new image under the CDK deployment design. |
| Existing GitHub-token secret | **Preserve** without retrieving, rotating, deleting, or recreating its value. Reference its ARN for runtime access. |
| Published Gate 1 PR and its task branch/commit | **Preserve**, leave unmerged; no GitHub cleanup. |

Preserved secret:
`arn:aws:secretsmanager:us-east-1:172318459814:secret:software-factory-poc/github-token-sXayGQ`.

Preserved publication:
[TeroTS/factory-smoke-test PR #1](https://github.com/TeroTS/factory-smoke-test/pull/1).

Referencing the preserved secret ARN is allowed; importing the old
CloudFormation template or adopting the old stack is not. The EventBridge
connection's separately managed secret remains part of the Gate 2 design.

## Implementation to keep or discard

- Keep [image/Dockerfile](../../image/Dockerfile) as the starting point for the
  new image, including package-manager CLI installation and sandbox helpers.
- Do not deploy or include the old Gate 1 CloudFormation template in the Gate 2
  app; use native CDK constructs.
- Consider reusing lifecycle/HTTP portions of
  [worker/server.py](../../worker/server.py). Its run hook currently launches
  `worker.task`; replace that dependency if the server is retained.
- Treat the Gate 1 test suite, local tools, and `worker/task.py` as disposable
  implementation, not Gate 2 dependencies. Do not carry its calculator-specific
  acceptance code into the new execution layer.
- Add Gate 2 tests for the new infrastructure, worker, and workflow. Passing
  Gate 1 is feasibility evidence, not a substitute for testing the replacement.

## Transition sequence

1. Inspect the account/Region and old resource inventory; confirm no VM/job is
   active. Retain the existing local Gate 1 outcome and preserved identifiers.
2. Confirm the native CDK resource/image deployment design before deleting
   anything. Resolve service-resource support and any necessary CDK bootstrap
   requirements; do not silently substitute template imports or manual-only
   provisioning for the all-CDK requirement.
3. Delete the old MicroVM image separately, empty the old artifact bucket, and
   delete the old stack and its owned resources. Confirm deletion before
   recreating resources with conflicting names. Do not touch the preserved
   GitHub secret or PR.
4. Deploy the newly defined CDK infrastructure: private artifact storage,
   build/runtime roles, logs, image provisioning, Step Functions Standard and
   its role, and the EventBridge connection. Preserve scoped permissions and
   keep credentials out of synthesis output, image snapshots, and job input.
5. Build and pin the new successful, active image with the Gate 2 worker.
   Revalidate its actual runtime-role/model/sandbox/tool compatibility; the
   old image's successful task does not establish compatibility of a rebuild.
6. Demonstrate orchestration success, controlled failure with cleanup, and the
   separate hard-lifespan check. Record outcomes in the current slice.

## US-04 worker execution and acceptance

Keep the existing `Job`, lifecycle hooks, and exact status responses in
[openapi.yaml](../../openapi.yaml); the reset does not introduce a new job API.
The worker validates structured input, initializes fresh per-job state, and
starts asynchronously. Cloning, runtime credential retrieval, and model calls
happen after launch, never during image validation or snapshot creation.

Use the same approved baseline/task and native provider/model. One Codex
invocation owns editing, tests, fixes, commit, push, and PR creation. Ordinary
command/test recovery stays inside that invocation; no agent restart or
whole-task rerun is allowed. Preserve the workspace-write sandbox.

Before reporting stable `{"status":"ok"}`, verify the required evidence:

- The approved final test command passes with actual exit code `0`, including
  positive, negative, and zero multiplication and preserved addition.
- Source/test changes are meaningful and do not weaken existing tests or modify
  demonstration deployment configuration.
- The published commit descends from the pinned baseline, and the agent has
  pushed the unique task branch and created one PR targeting `main`, unmerged.

A successful process exit or HTTP 200 does not prove these facts. Unrecoverable
setup, execution, or verification failures report stable `{"status":"error"}`;
causes and available output remain in CloudWatch, never the status response.
Exclude credentials from job input, prompts, and application logs. Test missing
or contradictory completion evidence and lifecycle/process failure handling.
These are acceptance requirements, not a requirement to restore the old
calculator-specific verifier or introduce a generic task-selection policy.

## US-04 image-provisioning design boundary

Before implementation or deletion, establish a supported CDK-owned mechanism
for image creation, update, deletion, and asynchronous build completion. Native
CDK definitions must own the artifact storage, build role, full image settings,
and workflow's explicitly pinned image version. Do not deploy or import
the old Gate 1 template, adopt the old stack, or substitute manual-only image setup.
If service-resource support requires a custom-resource provider, new dependency,
or architecture change, present that proposal for approval before implementing it.

Use the [image lifecycle reference](../aws-lambda-microvms/microvms-images.md)
to distinguish resource creation from usable version readiness: the image must
be `CREATED` or `UPDATED`, and the pinned version `SUCCESSFUL` and `ACTIVE`.
Updates must supply the complete hooks/environment/configuration, not assume
omitted parameters preserve prior settings. Include build failure reporting,
scoped permissions, artifact packaging, and any required CDK bootstrap resources
in the design. Bootstrap or package installation is not authorized by this
preparation document.

## Gate 2 workflow requirements

Keep launch → wait for `RUNNING` → create one port-8080 token → authenticated
status polling → terminate → confirm `TERMINATED` → `Succeed` or `Fail`.
Reuse the token across polls; invalid responses, worker failures, and cleanup
failures cannot become success. Preserve manual recovery access and single-job
operation, the 28-minute workflow timeout, and the 30-minute VM lifespan.

Use the existing native Bedrock provider/model and workspace-write sandbox.
No provider/model substitution, sandbox removal, agent restart, or automatic
whole-task rerun is introduced by the reset. Apply the worker acceptance and
image-provisioning requirements above before implementation; do not infer the
replacement design from throwaway calculator-specific code.

The [original orchestration plan](aws-lambda-microvm-software-factory-poc.md#gate-2--add-orchestration)
provides the lifecycle details. The exact status contract remains in
[openapi.yaml](../../openapi.yaml), not the original plan's older example
response shapes. Reconcile any affected specification/decision/contract
carry-forward assumptions before implementing the replacement; this reset
plan does not silently define a new job API or task-selection policy.
