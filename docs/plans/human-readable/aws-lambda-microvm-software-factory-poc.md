# AWS Lambda MicroVM Software Factory PoC

**Status:** Proposed, not implemented. Live end-to-end execution remains unverified.

## What it does

The service turns a small coding task into a tested GitHub pull request. It runs Codex in an isolated AWS Lambda MicroVM—a temporary virtual machine—and shuts the machine down when the job finishes. A human reviews and merges the PR.

## Main workflow

1. **Submit a task.** The operator manually starts an AWS Step Functions workflow with a GitHub repository, a pinned starting commit, and a task.
2. **Start the workspace.** Step Functions launches one MicroVM from a reusable image containing Codex and the repository’s development tools.
3. **Prepare the repository.** The worker inside the VM retrieves the GitHub token, clones the repository, checks out the starting commit, creates a task branch, and installs dependencies.
4. **Implement and test.** One Codex invocation edits the code, runs the approved tests, and fixes failures until the tests pass. Codex uses Amazon Bedrock for model inference; all edits, shell commands, and tests run inside the VM.
5. **Publish the result.** After passing tests with an actual code change, Codex commits, pushes the branch, and creates a GitHub PR. It does not merge it.
6. **Finish and clean up.** Step Functions polls the worker’s HTTPS status endpoint while the task runs. Once tests pass and PR creation is confirmed, the worker reports success. Step Functions terminates the VM, confirms shutdown, and completes successfully.

```text
Task → Step Functions → MicroVM: clone → code → test → create PR
                  ↓
          Confirm result → shut down VM
```

## Where things live

- **GitHub:** source repository, task branch, and PR.
- **CloudWatch:** execution logs.
- **Secrets Manager:** GitHub token, retrieved only at runtime.
- **Amazon Bedrock:** the selected GPT-6.1 Sol model, accessed using the VM’s AWS role rather than a model API key.

Step Functions manages the VM, not the coding loop. No local controller needs to stay running. The worker reports only success or an error; code and logs remain in GitHub and CloudWatch.

## Proving the PoC

1. **Prove execution first:** manually launch the VM, complete a real coding task through PR creation, and confirm shutdown.
2. **Add orchestration:** build a clean implementation with native Python CDK, then demonstrate the full Step Functions workflow and failure cleanup. Replace the old infrastructure rather than importing its CloudFormation template; preserve the GitHub-token secret and published PR. The Dockerfile and useful server code are starting points, not a requirement to retain the throwaway worker/tests.

Use one controlled repository and run one task at a time. Credentials must be restricted to the required resources. A maximum VM lifespan limits resource use if orchestration stops unexpectedly.

See the [original plan](../aws-lambda-microvm-software-factory-poc.md) for implementation details, remaining proposals, and verification requirements.
