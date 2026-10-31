# Interface Contracts

AWS-owned submission exchanges for the software factory PoC. Our HTTP
interfaces and shared payload schemas are defined **only in
[openapi.yaml](../openapi.yaml)**. Behavior and gate requirements remain in
[SPEC.md](../SPEC.md) and [DECISIONS.md](../DECISIONS.md).
**These interfaces are planned, not implemented or live-verified.**

## Operator → Step Functions: start a job

**Operation:** AWS `StartExecution`, called through the AWS CLI or SDK in Gate 2.
This is an AWS-owned interface, not an endpoint in our worker server.
Full AWS contract: [StartExecution](https://docs.aws.amazon.com/step-functions/latest/apireference/API_StartExecution.html).

### Request

| Field | Type | Value / meaning |
| --- | --- | --- |
| `stateMachineArn` | string | ARN of the deployed Step Functions Standard state machine. |
| `name` | string | Operator-chosen execution name; also used as the VM launch client token. |
| `input` | string | JSON serialization of `openapi.yaml#/components/schemas/Job`, not an embedded object. |

### Response

Successful AWS execution acknowledgement:

| Field | Type | Meaning |
| --- | --- | --- |
| `executionArn` | string | ARN identifying the execution. |
| `startDate` | timestamp | Execution start time; representation follows the AWS API/client. |

This acknowledges execution startup, **not task success**. Retry an uncertain
`StartExecution` with the same name and input, not a new job. AWS owns failure
responses; no custom submission-error body is defined here.

## Gate 1 submission: Operator → Lambda MicroVMs

**Operation:** AWS `RunMicrovm`, called manually instead of starting Step Functions.
Supply the same `Job` serialization as the AWS API's `runHookPayload`; the
launch response supplies `microvmId` and `endpoint`. Full launch parameters and
AWS response examples are in the
[copied launching reference](aws-lambda-microvms/microvms-launching.md#microvms-launching-run).

The resulting AWS-to-worker request envelope is defined by
`openapi.yaml#/components/schemas/RunHookRequest`, not repeated here.

## HTTP contract source

Read [openapi.yaml](../openapi.yaml) for lifecycle hooks, status polling,
authentication, headers, request/response bodies, and the shared `Job` and
`RunHookRequest` schemas. This document does not redefine them.

## Sequence diagrams

| Exchange | Rendered diagram | Editable source |
| --- | --- | --- |
| Image creation: one snapshot per image version | [Image-build sequence](diagrams/key_flow_image_build.svg) | [D2](diagrams/key_flow_image_build.d2) |
| Job launch, polling, termination: reuse the pinned snapshot | [Job-lifecycle sequence](diagrams/key_flow_job_lifecycle.svg) | [D2](diagrams/key_flow_job_lifecycle.d2) |

## Slice Handoff

`$lean-story-delivery` must read [openapi.yaml](../openapi.yaml) and this file,
then list relevant artifacts under `## Contract Inputs` in
`docs/slices/current-slice.md` for the selected `SPEC.md` story.
