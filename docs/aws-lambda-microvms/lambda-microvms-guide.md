

# AWS Lambda MicroVMs
<a name="lambda-microvms-guide"></a>

If you're building an application where multiple users or AI agents connect to a compute environment and run code (for example, interactive development environments, CI/CD systems, or sandboxes for AI) – you need compute environments that start fast, stay isolated between tenants, and don't require you to manage servers or networking.

AWS Lambda MicroVMs are purpose-built for these use cases. They are serverless compute environments that provide VM-level isolation with full operating system capabilities (for example, installing system packages or mounting filesystems), snapshot-based rapid startup speeds, and fine-grained control over ingress networking (port access, support for HTTP/2, gRPC, WebSockets) and egress networking (public internet access and VPC access).

For interactive use cases such as interactive development environments and AI sandboxes, environments can experience long periods of idleness when end-users context switch to other tasks or when waiting for AI. MicroVMs can be suspended when idle, preserving memory and disk state while reducing costs. When traffic arrives, they are resumed. You can enable automatic or programmatic suspend-resume to lower idle costs, while maintaining near-instant readiness when your end-users return.

AWS Lambda MicroVMs deliver these core capabilities through Firecracker virtualization, which powers Lambda Functions' 15 trillion\+ monthly invocations.

To get started, see [Create your first MicroVM](microvms-getting-started.md). For pricing information, see [AWS Lambda Pricing](https://aws.amazon.com/lambda/pricing/). For security and permissions, see [Security and permissions](microvms-security.md). For observability, see [Monitoring](microvms-monitoring.md). For operational guidance, see [Best practices for Lambda MicroVMs](microvms-best-practices.md).

## How Lambda MicroVMs work
<a name="microvms-how-it-works-overview"></a>

1. You package your application code and a `Dockerfile` into a zip archive and upload it to Amazon S3.

1. You call the Lambda API to create a MicroVM image. Lambda executes your `Dockerfile`, starts your application, and captures a snapshot of the fully initialized environment.

1. When your application needs an isolated environment – for a user session, a job, or a sandbox – you call `run-microvm`. Lambda launches a MicroVM from the snapshot with rapid startup times.

1. Clients connect to the running MicroVM through its dedicated HTTPS endpoint. No load balancers or ingress infrastructure required.

1. When idle, the MicroVM suspends, preserving memory and disk state while reducing costs. It resumes with memory and disk state intact when traffic returns. Suspend-resume behavior can be configured automatically through lifecycle policies or triggered directly through API invocations to `suspend-microvm` and `resume-microvm`.

1. When the session ends, you terminate the MicroVM to release all resources.

## When to use Lambda MicroVMs
<a name="microvms-when-to-use"></a>

Lambda MicroVMs is a good fit for use cases that execute user or AI-generated code and require execution environments that offer strong isolation, rapid launch and resume latency, and developer control over environment lifecycle and state retention. Typical use cases include:
+ **Interactive code environments** – Development environments where users write and execute code in real time.
+ **AI code execution sandboxes** – Ephemeral sandboxes for executing AI-generated code safely.
+ **Data analytics applications** – Jupyter notebooks and ephemeral data processing workloads that execute user-supplied scripts.
+ **Security scanning** – Vulnerability assessment tools that need isolated execution environments.
+ **Reinforcement learning environments** – Isolated sandboxes for AI agent evaluation and training, where fresh environments are spun up for every run.
+ **Multi-tenant CI/CD** – Task executors that require isolation between tenants.
+ **Game servers** – Hosting environments that execute user-supplied scripts with strong isolation.

## Key features
<a name="microvms-key-features"></a>
+ **Rapid startup** – MicroVMs resume from pre-initialized snapshots, skipping application initialization. See [MicroVM Images](microvms-images.md).
+ **Lifecycle control** – Suspend, resume, and terminate MicroVMs programmatically or automatically through configurable idle policies. See [Running MicroVMs](microvms-launching.md).
+ **Flexible networking** – MicroVMs support inbound HTTPS traffic on configurable ports, with service-provided JWE authentication. Outbound access is configurable. Access the public internet or your VPC. For details, see the chapter on [Networking](microvms-networking.md).
+ **Flexible resource allocation** – Provision each MicroVM for baseline or average usage, with the flexibility to vertically scale to 4x of configured baseline during peak activity. Pay the baseline rate while your MicroVM is running and only pay for active use above the baseline.