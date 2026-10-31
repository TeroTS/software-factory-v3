# AWS Lambda MicroVM Software Factory PoC

## What it does

This proposed service turns a coding task into a GitHub pull request. It runs Codex in a temporary, isolated MicroVM, where the agent edits code, runs tests, and publishes its work for human review.

For this PoC, the operator runs one task at a time. The service does not merge pull requests.

## Main workflow

1. **Submit a task.** Provide a GitHub repository, a specific starting commit, and a small coding task with clear test expectations.
2. **Start the workspace.** Step Functions launches a MicroVM from a reusable image containing Codex and the repository's development tools.
3. **Prepare the repository.** The worker clones the repository, checks out the starting commit, creates a task branch, and installs dependencies.
4. **Do the work.** One Codex session uses Amazon Bedrock to edit code, run tests, and fix failures until the tests pass.
5. **Publish the result.** Codex commits the changes, pushes the branch, and opens a GitHub pull request. Success requires passing tests and confirmed PR creation—not just a successful agent exit.
6. **Finish and clean up.** Step Functions polls the worker's status, terminates the MicroVM when the work finishes, confirms termination, and reports the outcome.

Logs stay in CloudWatch; code changes stay in GitHub. No local controller or artifact download is needed.

## Who does what

- **Codex** owns the edit–test–fix loop and publishes the PR from inside the MicroVM.
- **The worker** prepares the workspace, starts Codex, and reports status.
- **Step Functions** manages the MicroVM lifecycle and checks progress. It does not run a second agent loop or restart the task.
- **A developer** reviews the PR before merging.

## Prove it in two stages

1. **Manual proof:** Launch a MicroVM and complete a real coding task. Verify passing tests, PR creation, logs, and termination.
2. **Managed workflow:** Only after the manual proof succeeds, use Step Functions to run the same image and worker end to end.

The image contains tools, not credentials. The worker retrieves a restricted GitHub token at runtime and uses its AWS role for Bedrock access.

## Current status

This is a plan, not a working service. Some AWS and API checks have passed, but actual Codex execution, MicroVM compatibility, GitHub publication, and the full workflow remain untested. Demonstration inputs and remaining resource choices still need approval.

For implementation details, permissions, limits, and verification steps, see the [detailed plan](../aws-lambda-microvm-software-factory-poc.md).
