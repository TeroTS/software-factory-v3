

# Best practices for Lambda MicroVMs
<a name="microvms-best-practices"></a>

Follow these best practices to build reliable, cost-effective, and secure applications with AWS Lambda MicroVMs.

## Application design
<a name="microvms-best-practices-design"></a>

Design your application to work well with MicroVM snapshots and lifecycle hooks:
+ **Use lifecycle hooks** – Implement `/run` for post-run initialization, `/suspend` for graceful cleanup, `/resume` for connection re-establishment, and `/terminate` for data flushing.
+ **Handle snapshot re-use** – Generate UUIDs, secrets, and random values in the `/run` hook, not during image build. Use CSPRNGs for all random number generation.
+ **Design for resume** – Validate all network connections and cached state in the `/resume` hook. AWS SDK connections typically recover automatically, but custom connections might not.

## Performance
<a name="microvms-best-practices-performance"></a>

Optimize MicroVM performance with the following practices:
+ **Right-size your MicroVM** – Choose the smallest MicroVM size that meets your CPU, memory, and bandwidth requirements. Bandwidth scales with configured memory.
+ **Minimize snapshot size** – Keep your application footprint small to reduce run and resume times. Remove build-time dependencies and temporary files before the image snapshot is captured.
+ **Use suspend/resume for latency-sensitive workloads** – Keep frequently-used environments in suspended state to quickly resume execution.

## Security
<a name="microvms-best-practices-security"></a>

Follow these practices to secure your MicroVM workloads:
+ **Rotate auth tokens** – Generate short-lived tokens (15–30 minutes). Implement token refresh logic in your client before expiry.
+ **Scope tokens to specific ports** – Use the `allowedPorts` parameter to limit token scope to only the ports your application uses.
+ **Use VPC egress for sensitive traffic** – Route traffic to databases and internal APIs through a VPC egress connector rather than the public internet.
+ **Apply least-privilege IAM** – Separate build roles (image creation) from execution roles (runtime). Grant minimum required permissions.

## Cost optimization
<a name="microvms-best-practices-cost"></a>

Reduce costs with the following strategies:
+ **Configure idle policies** – Set `maxIdleDurationSeconds` to suspend idle MicroVMs automatically. Suspended MicroVMs do not incur compute charges.
+ **Set maximumDurationInSeconds** – Prevent runaway costs by setting a hard termination time on every MicroVM.
+ **Terminate unused MicroVMs** – Do not leave MicroVMs running indefinitely. Use `suspendedDurationSeconds` to auto-terminate after extended idle periods.
+ **Tag resources** – Use tags on MicroVM images for cost allocation and reporting in AWS Cost Explorer.
+ **Right-size your MicroVMs** – Start with the smallest baseline that meets your performance needs. You can burst to 4x baseline during peak activity without re-provisioning.
+ **Clean up unused images** – Delete MicroVM image versions you no longer need to reduce storage costs.

## Monitoring
<a name="microvms-best-practices-monitoring"></a>

Monitor your MicroVMs effectively:
+ **Review stateReason** – When MicroVMs terminate unexpectedly, the `get-microvm` response includes a `stateReason` with the reason for the unexpected failure.