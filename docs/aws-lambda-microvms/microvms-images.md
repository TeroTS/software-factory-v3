

# MicroVM images
<a name="microvms-images"></a>

This section describes how to build, configure, update, and manage MicroVM images.

A MicroVM image is a resource that defines the filesystem and application environment of a MicroVM. The MicroVM image includes your runtime environment, application code, and supporting programs such as background processes and observability agents. To create a MicroVM image, you provide a zip package that contains a `Dockerfile` and your application artifacts, which you upload to Amazon S3.

Your `Dockerfile` defines how your application is packaged. Lambda builds your application container image by running your `Dockerfile` on top of an operating system environment that is provided by a Lambda-managed MicroVM base image. MicroVM base images are described below, in the section titled – [MicroVM base images](#microvms-images-base-images).

You can update your MicroVM base images to update the application code or configuration for your MicroVMs. Each update you trigger creates a new MicroVM image version.

## How Lambda builds a MicroVM image
<a name="microvms-images-creating"></a>

When you create a MicroVM image, Lambda:
+ Retrieves your packaged artifacts from Amazon S3.
+ Starts a fresh MicroVM from the Lambda-managed base image.
+ Executes the instructions in your `Dockerfile`.
+ Launches your application using the `ENTRYPOINT` or `CMD` instruction.
+ Waits for initialization to complete, signalled by your lifecycle hook.
+ Captures a snapshot of the disk and memory state.

Once the snapshot process completes, your MicroVM image enters the `CREATED` state. You can now use this MicroVM image to create a MicroVM, and each MicroVM image can be used to create multiple independent MicroVMs. A MicroVM run from the MicroVM image resumes directly from the snapshotted state, providing rapid startup times. Each MicroVM image can be used to run multiple MicroVMs, up-to the limit available for your account.

For a step-by-step walkthrough of packaging your code and creating your first MicroVM image, refer to [Create your first MicroVM](microvms-getting-started.md).

## MicroVM sizing
<a name="microvms-images-sizing"></a>

Lambda MicroVMs uses a baseline-peak model that removes the need to right-size each compute environment for peak activity. You configure the baseline compute resources for your MicroVM. During peak activity, your MicroVM can automatically scale vertically up to 4x the baseline. You pay the baseline rate while your MicroVM is running and only pay for what you actively use above the baseline, billed per second.

You set the baseline by using the `memory` parameter when creating your MicroVM image. vCPU scales proportionally with memory (2 GB = 1 vCPU). The default baseline is 2 GB / 1 vCPU.

The following table lists the available sizes:


| Baseline | Peak | Max disk space | 
| --- | --- | --- | 
| 0.5 GB memory, 0.25 vCPU | 2 GB memory, 1 vCPU | 8 GB | 
| 1 GB memory, 0.5 vCPU | 4 GB memory, 2 vCPU | 8 GB | 
| 2 GB memory, 1 vCPU (default) | 8 GB memory, 4 vCPU | 8 GB | 
| 4 GB memory, 2 vCPU | 16 GB memory, 8 vCPU | 16 GB | 
| 8 GB memory, 4 vCPU | 32 GB memory, 16 vCPU | 32 GB | 

## MicroVM base images
<a name="microvms-images-base-images"></a>

A MicroVM base image serves as the foundation for your MicroVM images. Lambda publishes a MicroVM base image which provides the Amazon Linux 2023 operating system and the service components required to run MicroVMs. When you create or update a MicroVM image, Lambda starts a new MicroVM from this base image and runs your `Dockerfile` instructions within this operating system environment.

Lambda periodically releases new versions of service-managed MicroVM base images, such as when applying security patches, to update the operating system or service components. By default, the latest version of a service-managed base image applies when you are creating/updating your own MicroVM images. For troubleshooting or debugging, you can optionally override the version of the service-managed based images when creating your own MicroVM image using the `base-image-version` parameter.

Base image versions follow a deprecation lifecycle:
+ **`AVAILABLE`** – Current, recommended for use.
+ **`DEPRECATED`** (60 days) – A newer version exists. You can still build and run.
+ **`EXPIRING`** (30 days) – Cannot create new images. Existing images can still run.
+ **`EXPIRED`** – Cannot build or run. Rebuild your image on a supported version.
+ **`RECALLED`** – Immediately unavailable due to critical security issues (rare).

To stay current, monitor for deprecation notifications and rebuild your MicroVM images when a new base image version is released.

Note that the MicroVM base image is distinct from the container base image you specify in your Dockerfiles. While the former defines the operating system environment for your MicroVMs, the latter defines which base container image to use when packaging your application for use with Lambda MicroVMs. See the section on [Container base images](#microvms-images-container-base) for more details.

Use the following APIs to discover available managed MicroVM base images and their versions:

```
# List all managed MicroVM base images
aws lambda-microvms list-managed-microvm-images

# List the versions of a specific managed MicroVM base image
aws lambda-microvms list-managed-microvm-image-versions \
  --image-identifier arn:aws:lambda:{{us-east-1}}:aws:microvm-image:al2023-1
```

## MicroVM image build hooks
<a name="microvms-images-build-hooks"></a>

Lambda provides MicroVM image build hooks that let you verify application correctness and optimize performance during MicroVM image creation. Hooks run before Lambda takes the snapshot that is used to initialize each MicroVM. Each hook is an HTTP endpoint that your application exposes and that Lambda calls during the build. By responding to these requests, you control and validate the MicroVM image build process. Lambda uses HTTP Status codes to determine whether the hooks successfully completed.

**Important**  
If you configure any hooks, you must specify the port that your application listens on for hook requests.


| Hook | Path | Details | HTTP status codes | Timeout | 
| --- | --- | --- | --- | --- | 
| /ready | /aws/lambda-microvms/runtime/v1/ready | Called during the MicroVM image build, after your application starts through ENTRYPOINT or CMD. Signals that your application is ready to be snapshotted. | HTTP 503: Not yet ready; Lambda retries until timeout. HTTP 200: Initialization complete; Lambda takes the snapshot. | 1–3600 seconds (readyTimeoutInSeconds) | 
| /validate | /aws/lambda-microvms/runtime/v1/validate | Called after the build completes, on a new MicroVM started from the created image. Confirms the application works correctly when resumed. | HTTP 503: Validation needs more time to complete; Lambda retries until timeout. HTTP 200: Validation passed. | 1–3600 seconds (validateTimeoutInSeconds) | 

**Important**  
When returning HTTP 503, return it immediately rather than holding the request open while you wait. If the timeout elapses while a request is held open, Lambda ends the build.

**Note**  
You can also use the /validate hook to optimize startup time. To do so, run mock payloads during validation. This enables Lambda to track accessed regions of your snapshot and optimize their retrieval during MicroVM startup.

## Updating a MicroVM image
<a name="microvms-images-updating"></a>

You can update an existing MicroVM image by calling the `update-microvm-image` API. Each update triggers a new MicroVM image version build. You typically update a MicroVM image to:
+ **Deploy new application code** – Point to a new code artifact (a new zip uploaded to Amazon S3) to ship a new version of your application.
+ **Move to a newer MicroVM base image** – Change the MicroVM base image ARN to upgrade to newer versions of the Lambda MicroVM base image. For more information, refer to [MicroVM image patching](#microvms-images-patching) and [MicroVM base images](#microvms-images-base-images).
+ **Change the build role** – Update the build role ARN when the permissions Lambda needs during the build change, such as when your code artifact moves to a different Amazon S3 bucket or you start pulling from a private ECR repository.
+ **Adjust the runtime configuration** – Change hooks, environment variables, or capabilities to reconfigure how your MicroVM image is built and run.
+ **Update the description** – Change the MicroVM image description to record what changed in this version.

The following CLI command shows how you can update a MicroVM image. The `--base-image-arn` and `--build-role-arn` parameters are required on every `update-microvm-image` call that triggers a new build, even when you are only changing the code artifact – omitting them results in a `ValidationException`:

```
aws lambda-microvms update-microvm-image \
  --image-identifier {{arn:aws:lambda:us-east-1:123456789012:microvm-image:my-microvm-image}} \
  --code-artifact uri=s3://my-bucket/deployments/app-v2.zip \
  --base-image-arn arn:aws:lambda:{{us-east-1}}:aws:microvm-image:al2023-1 \
  --build-role-arn arn:aws:iam::123456789012:role/MicrovmBuildRole \
  --description "Updated with v2 application code"
```

## Image states and build states
<a name="microvms-images-states"></a>

Every time you create or update a MicroVM image, Lambda produces a new **version** built from your code artifact and base image. A MicroVM image can have many versions over time, and you run MicroVMs from a specific version.

Three independent states track different aspects of the lifecycle:
+ **Image state** – the overall lifecycle of the MicroVM image resource (being created, ready to use, being updated, failed, or being deleted).
+ **Version state** – the build progress of a specific version (pending, building, succeeded, or failed). Check `stateReason` or CloudWatch logs (`/aws/lambda-microvms/<image-name>`) for failure details.
+ **Version activation** – whether a successfully-built version is allowed to run MicroVMs. Lambda sets new versions to `ACTIVE` automatically; you can set a version to `INACTIVE` to disable it without deleting it.


| State | Possible values | Transition controlled by | 
| --- | --- | --- | 
| Image state | CREATING, CREATED, CREATION\_FAILED, UPDATING, UPDATED, UPDATE\_FAILED, DELETING, DELETED, DELETION\_FAILED | Lambda (automatic) | 
| Version state | PENDING, IN\_PROGRESS, SUCCESSFUL, FAILED | Lambda (automatic) | 
| Version activation | ACTIVE, INACTIVE | You (update-microvm-image-version --state) | 

To run a MicroVM from a version, the image state must be `CREATED` or `UPDATED`, the version state must be `SUCCESSFUL`, and the version must be `ACTIVE`.

**Note**  
These states are independent. An image in the `CREATED` state can contain a version whose state is `FAILED`.

```
# De-activate a version
aws lambda-microvms update-microvm-image-version \
  --image-identifier my-image \
  --image-version 1.0 \
  --state INACTIVE
```

## Environment variables
<a name="microvms-images-env-vars"></a>

Environment variables are set at MicroVM image build time through the `environmentVariables` field (maximum 50 variables). These are injected into the container during the snapshot build process. You can pass dynamically set payloads when running a new MicroVM. Refer to the section on running your MicroVM to learn more.

## MicroVM image patching
<a name="microvms-images-patching"></a>

When there is a new MicroVM base image available, you can issue an `update-microvm-image` call to trigger a MicroVM image build with the latest patches – either omitting the `base-image-version` argument (for latest) or specifying the argument with the latest version.

## Container base images
<a name="microvms-images-container-base"></a>

Lambda MicroVMs runs your application as a container within the MicroVM operating system environment. You define that container with your `Dockerfile`, and the `FROM` instruction in your `Dockerfile` sets the *container base image* for your application.

You can either start with the Lambda base container image for Amazon Linux 2023 (`public.ecr.aws/lambda/microvms:al2023-minimal`) and add your `Dockerfile` instructions on top of this, or use your own base container image. When using your own container images, validate the following requirements:

### Requirements
<a name="microvms-images-container-base-requirements"></a>
+ The container base image must be compatible with the target CPU architecture.
+ Container base images from private AWS ECR repositories require the build role to have `ecr:GetAuthorizationToken` and `ecr:BatchGetImage` permissions.
+ The container base image must be based on a Linux operating system.
+ Container base images must be accessible from the Lambda build infrastructure (public internet or an ECR repository in the same AWS account).
+ Container base images must be snapshot compatible, see instructions below.

### Snapshot-compatible base images
<a name="microvms-images-container-base-snapshot-compat"></a>

As Lambda MicroVMs starts each MicroVM from a pre-initialized snapshot, base images must be snapshot compatible. We recommend reviewing the section on [Compatibility considerations](microvms-images-snapshots.md#microvms-images-snapshots-compatibility) when using your own base images with Lambda MicroVMs.

### Using a private ECR image
<a name="microvms-images-container-base-private"></a>

Reference your private ECR container base image in the `FROM` instruction of your `Dockerfile`:

```
FROM 123456789012.dkr.ecr.us-east-1.amazonaws.com/my-base:latest
WORKDIR /app
COPY . .
CMD ["./my-app"]
```

Add the following permissions to your build role:

```
{
  "Effect": "Allow",
  "Action": [
    "ecr:GetAuthorizationToken",
    "ecr:BatchCheckLayerAvailability",
    "ecr:GetDownloadUrlForLayer",
    "ecr:BatchGetImage"
  ],
  "Resource": "*"
}
```

## Operating system capabilities
<a name="microvms-images-os-capabilities"></a>

By default, Lambda MicroVMs run with a standard set of Linux capabilities. You can grant elevated Linux capabilities using the `additionalOsCapabilities` field when creating or updating a MicroVM image. The only supported value is `["ALL"]`. Elevated capabilities enable operations such as mounting filesystems, creating network namespaces, or running eBPF programs. Capabilities are applied within the VM isolation boundary and do not affect the host or other MicroVMs.

```
aws lambda-microvms create-microvm-image \
  --name my-network-tool \
  --code-artifact uri=s3://my-bucket/app.zip \
  --base-image-arn arn:aws:lambda:{{us-east-1}}:aws:microvm-image:al2023-1 \
  --build-role-arn arn:aws:iam::123456789012:role/BuildRole \
  --additional-os-capabilities '["ALL"]'
```