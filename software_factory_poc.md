# AWS Lambda MicroVM Software Factory PoC

## Objective

Evaluate AWS Lambda MicroVMs as isolated execution environments for an AI-powered software factory and create a practical plan for a minimal proof of concept (PoC).

Focus on validating technical feasibility and implementing a working example. Avoid unnecessary architectural decisions and production-grade complexity.

## 1. Review AWS Documentation

Review the [AWS Lambda MicroVM Developer Guide](docs/aws-lambda-microvms/lambda-microvms-guide.md) and relevant documentation:

- [Core concepts](docs/aws-lambda-microvms/microvms-how-it-works.md)
- [Images](docs/aws-lambda-microvms/microvms-images.md) and [snapshots](docs/aws-lambda-microvms/microvms-images-snapshots.md)
- [Launching MicroVMs](docs/aws-lambda-microvms/microvms-launching.md)
- [Networking](docs/aws-lambda-microvms/microvms-networking.md)
- [Security](docs/aws-lambda-microvms/microvms-security.md)
- [Monitoring](docs/aws-lambda-microvms/microvms-monitoring.md)
- [Best practices](docs/aws-lambda-microvms/microvms-best-practices.md)
- [OpenAI Agents API integration](docs/aws-lambda-microvms/microvms-integrations-openai-agents-api.md)

These are local copies of the [official AWS documentation](https://docs.aws.amazon.com/lambda/latest/dg/lambda-microvms-guide.md).

Focus on capabilities directly relevant to running AI coding agents: provisioning, lifecycle, command execution, networking, filesystem, isolation, and resource limits.

Verify feature availability and avoid assuming undocumented capabilities.

## 2. Evaluate Agent Integration Options

Investigate the documented OpenAI Agents API integration and assess whether it is suitable for the PoC.

Also investigate a **custom agent execution solution** that uses AWS Lambda MicroVMs without depending on the documented OpenAI Agents API integration.

Consider:
- Running an existing coding agent or custom agent process inside a MicroVM.
- Using an LLM API directly for reasoning and tool calls.
- Executing shell commands, Git operations, builds, and tests.
- Managing agent execution, state, and results.
- How a custom solution would provision, connect to, and control MicroVMs.

Compare the approaches in terms of feasibility, implementation complexity, flexibility, and limitations.

**Recommend the simplest viable option.** If the documented integration is unsuitable, propose a custom architecture and explain how to implement it.

## 3. Define the Minimal PoC

Build a proof of concept demonstrating this workflow:

1. Submit a software development task.
2. Launch an isolated AWS Lambda MicroVM.
3. Start an AI coding agent with access to a GitHub repository.
4. Let the agent modify source code and execute shell commands.
5. Run the repository's automated tests.
6. Commit the changes and create a GitHub pull request.
7. Collect execution logs and results.
8. Terminate the MicroVM.

Keep the PoC limited to **one agent, one repository, and one task at a time**.

Use existing tools and managed services where practical. Avoid introducing infrastructure that is not essential for demonstrating the workflow.

## 4. Propose Architecture and Implementation Plan

Provide:

- A simple architecture diagram (Mermaid).
- Main components and their responsibilities.
- How the agent communicates with and executes commands inside the MicroVM.
- Required AWS services, APIs, and permissions.
- A step-by-step implementation plan with concrete tasks.
- Minimal code or CLI examples where useful.
- Basic cost estimates and major technical risks.
- Clear PoC success criteria.

Do not overengineer orchestration, observability, security, scalability, or error handling. Address only what is necessary for a functional and reasonably safe PoC.

## 5. Expected Output

Structure the response as:

1. **Feasibility Summary** — Can Lambda MicroVMs support this use case?
2. **Agent Integration Options** — Documented OpenAI integration vs. custom solution, with a recommendation.
3. **Minimal PoC Architecture** — Diagram and explanation.
4. **Implementation Plan** — Practical, sequential engineering steps.
5. **Risks and Limitations** — Only significant blockers and constraints.
6. **Success Criteria** — How to verify that the PoC works.

Use official documentation to support technical claims. Clearly distinguish verified capabilities from assumptions requiring testing.

**Goal:** Produce an actionable, lightweight PoC plan that demonstrates AI-driven software development inside AWS Lambda MicroVMs, without prematurely designing a production software factory.