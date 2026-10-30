

# AWS Lambda MicroVMs core concepts
<a name="microvms-how-it-works"></a>

AWS Lambda MicroVMs uses several resource types that you create and manage. This page describes each resource type, how Lambda builds your MicroVM image into a snapshot, and the lifecycle states a MicroVM transitions through at runtime – the foundation for building applications with MicroVMs.

## Key concepts
<a name="microvms-key-concepts"></a>

**MicroVM**  
A MicroVM is a resource that represents an isolated compute environment for a single tenant, user session, or job. Each MicroVM runs an Amazon Linux 2023 operating system with operating system capabilities, providing near-instant launch and resume. MicroVMs receive requests through inbound HTTPS connections and can be suspended when idle, preserving memory and disk state. A suspended MicroVM resumes when traffic returns.

**MicroVM image**  
A MicroVM image is a resource that defines the application environment of a MicroVM. When you create an image, Lambda builds it into a snapshot that enables near-instant launch (see [How Lambda builds your image](#microvms-build-process) below).  
To create a MicroVM image, you provide a zip package containing a `Dockerfile` and your application artifacts, uploaded to Amazon S3. You must use a Lambda-published managed base image as the foundation – specify it with the `base-image-arn` parameter. Your `Dockerfile` defines the application layers that Lambda builds on top of the managed base.  
MicroVM images are versioned. Each version represents a single build produced from a specific code artifact and base image. A version progresses through build states (`PENDING` → `IN_PROGRESS` → `SUCCESSFUL` or `FAILED`), and successful versions can be set to `ACTIVE` or `INACTIVE`. For details on image states and management, see [MicroVM Images](microvms-images.md).

**Network connectors**  
Network connectors are resources that control how traffic reaches your MicroVM and how your MicroVM reaches external services. You associate connectors with a MicroVM at run time to configure inbound and outbound access independently.  
Build-time and run-time connectors can differ, enabling your MicroVM to reach different environments during image build versus runtime.  
Use Lambda-provided defaults for inbound port access (with JWE authentication), shell access, and public internet egress. Create your own network connector to route outbound traffic through your VPC.

## How Lambda builds your image
<a name="microvms-build-process"></a>

When you create or update a MicroVM image, Lambda performs a build process that produces a Firecracker snapshot. This snapshot captures the fully initialized state of your application, enabling near-instant launch and resume for MicroVMs that run from it.

The build process:

1. Lambda provisions a fresh MicroVM using the managed base image you specified.

1. Lambda executes your `Dockerfile` instructions to install dependencies and configure your environment.

1. Lambda starts your application using the `ENTRYPOINT` or `CMD` command.

1. If you enabled the `/ready` hook, Lambda waits for your application to signal readiness (HTTP 200).

1. Lambda captures a snapshot of the disk and memory state, including all running processes.

When you run a MicroVM, Lambda restores it from this snapshot. Your application resumes from its pre-initialized state without repeating startup.

If your application generates unique content during the build (such as unique IDs, secrets, or network connections), that content is shared across all MicroVMs run from the same image version. To avoid this, generate unique content after the MicroVM starts using the `/run` lifecycle hook. For details, see the snapshot compatibility section in [MicroVM Images](microvms-images.md).

## MicroVM lifecycle
<a name="microvms-lifecycle-overview"></a>

At runtime, a MicroVM transitions through the following stages:

1. **Run** – You call `run-microvm`. Lambda restores the MicroVM from the image snapshot, assigns a unique ID, and creates an endpoint. The MicroVM transitions from `PENDING` to `RUNNING`.

1. **Running** – Your application receives and processes requests through its endpoint URL.

1. **Suspend** – After a configurable idle period (or through the `suspend-microvm` API), the MicroVM transitions through `SUSPENDING` to `SUSPENDED`. Memory and disk state are preserved.

1. **Resume** – The MicroVM transitions from `SUSPENDED` directly back to `RUNNING` when traffic arrives (if `autoResumeEnabled=true`) or you call `resume-microvm`.

1. **Terminate** – The MicroVM transitions through `TERMINATING` to `TERMINATED` when you call `terminate-microvm` or the maximum duration is exceeded.

### States
<a name="microvms-states"></a>

The following table describes each MicroVM state. These states enable you to build reliable applications and implement proper error handling.


| State | Description | 
| --- | --- | 
| PENDING | MicroVM is being provisioned. Resources are being allocated and the snapshot is loading. | 
| RUNNING | MicroVM is active and accepting traffic through its endpoint URL. The /run hook has completed. | 
| SUSPENDING | MicroVM is being suspended. The /suspend hook is executing. Disk and memory are being checkpointed. | 
| SUSPENDED | MicroVM is suspended. State is preserved. No compute charges accrue. Can be resumed or terminated. | 
| TERMINATING | MicroVM is being terminated. The /terminate hook is executing. Resources are being released. | 
| TERMINATED | MicroVM has been terminated. This is a terminal state. The MicroVM cannot be resumed or restarted. | 

### State transitions
<a name="microvms-transitions"></a>

The following table shows the valid transitions between MicroVM states and what triggers each transition.


| Initial state | Target state | Trigger | 
| --- | --- | --- | 
| PENDING | RUNNING | Provisioning complete, /run hook succeeded. | 
| RUNNING | SUSPENDING | Idle duration exceeded, or explicit suspend-microvm API call. | 
| SUSPENDING | SUSPENDED | /suspend hook completed, memory and disk state checkpointed. | 
| SUSPENDED | RUNNING | Traffic arrives (autoResumeEnabled=true) or explicit resume-microvm API call. | 
| RUNNING | TERMINATING | Explicit terminate-microvm API call, or maximumDurationInSeconds exceeded. | 
| SUSPENDED | TERMINATING | suspendedDurationSeconds exceeded, or explicit terminate-microvm API call. | 
| TERMINATING | TERMINATED | /terminate hook completed, all resources released. | 

**Important**  
If your `/run` hook fails or times out, the MicroVM may transition directly to `TERMINATING` without ever reaching `RUNNING`. Implement timeout and error handling in your hooks to avoid silent failures.