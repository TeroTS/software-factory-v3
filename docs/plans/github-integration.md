# GitHub integration: happy-path demo

Status: accepted design. Implementation and deployment are not authorized by this plan.

## Flow

```text
GitHub issues.opened
  → Lambda Function URL: verify signature, validate repository, prepare Job
  → Amazon SQS + DLQ
  → EventBridge Pipes: batch size one, map message body to Job input
  → Existing Step Functions Standard Job workflow
```

## Accepted scope

- Accept HTTP POST requests only for GitHub `issues` events with `action: opened`
  in the deployment-authorized repository. Other actions and PRs do not trigger Jobs.
- Verify GitHub's signature against the raw request body using a shared signing
  secret configured directly in Lambda. No new Secrets Manager resource or
  authentication system is required. Keep the secret out of source control and logs.
- Form the Agent task from the issue title and body at receipt. Resolve the
  configured PR target branch to a full commit SHA using the existing
  repository-scoped GitHub token.
- Queue the existing Job shape: `repository`, `baseSha`, and `task`. EventBridge
  Pipes supplies a single Job object, not a batch array, to the existing workflow.
- Reuse the existing pinned-image workflow and its repository setup, verification,
  PR publication, and VM-cleanup behavior. Do not change manual submission tooling.
- Configure the SQS DLQ redrive threshold as `maxReceiveCount: 1`. DLQ inspection
  and recovery are manual.
- Add no application retry logic, idempotency layer, dispatcher, DynamoDB tracking,
  concurrency lock, issue comments, or additional failure-recovery machinery.
  Signature/repository checks and valid Job construction remain required.

## Operating limits

One-at-a-time operation is operator discipline: open the next issue only after
confirming the previous Job's completion and VM termination, including any manual
Jobs. There is no enforced active-Job limit.

Pipes invokes Standard workflows asynchronously. Successful workflow startup does
not mean the Job completed; later Job failure does not send that message to the
DLQ. The DLQ threshold is a queue redrive setting, not a guarantee of exactly one
service delivery or invocation.

GitHub, SQS, and Pipes may deliver duplicates. Duplicate Jobs and concurrent
execution remain possible and are outside this demo's guarantees. Existing AWS
and workflow mechanisms are retained; no new duplicate protection is introduced.

Opening an issue triggers billable work. Repository access is controlled through
GitHub; no additional actor allowlist or approval label is introduced. Webhook
signature verification authenticates delivery, not the issue author.

## References

- [Domain vocabulary](../../GLOSSARY.md)
- [Existing interface contracts](../contracts.md)
- [Pipes target invocation behavior](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-pipes-event-target.html)
