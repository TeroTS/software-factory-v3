# Operations and limits

## Startup and configuration

Deploy artifact storage before the image, then deploy the Standard workflow with
an explicit current-source `SUCCESSFUL/ACTIVE` image-version pin. The build
snapshot starts only the lifecycle listener; it captures no Job or GitHub token.
Ready returns 200; validate tests local tools and workspace-write sandbox
execution without credentials/inference. Runtime `run` alone starts work.

| Setting | Scope and source |
| --- | --- |
| `AuthorizedRepository`, `PrTargetBranch` | Image CDK parameters; defaults are `TeroTS/factory-smoke-test` and `main`. Supply runtime `AUTHORIZED_REPOSITORY` / `PR_TARGET_BRANCH` to worker, receiver, and dispatcher. |
| `GITHUB_SECRET_ARN` | Deployment-owned repository-scoped token secret; manually populated, never selected by Job input. |
| `ImageVersion` | Required workflow CDK parameter, explicit `major.minor` verified pin. |
| `WebhookSigningSecret` | NoEcho workflow parameter supplied privately; receiver receives `WEBHOOK_SECRET`. |
| `JOB_TABLE_NAME`, `STATE_MACHINE_ARN` | Infrastructure supplies queue/workflow identities to ingress components. |
| `AWS_ACCOUNT_ID`, `AWS_REGION`, `AWS_PROFILE` | Operator environment selects deployment account/Region and profile; live tools also accept `--profile`. Account/Region have no fixed defaults. CDK supplies the image's runtime Region; ingress Lambdas use AWS's runtime Region. |
| `WORKSPACE` | Image/runtime setting; defaults to `/workspace`. |
| Model, sandbox, approval settings | Root-owned [managed Codex policy](../image/managed_config.toml): pinned Bedrock model, workspace-write/network access, never interactive approval, web search disabled. |

[Image](../src/infra/image_stack.py), [workflow stack](../src/infra/workflow_stack.py),
and [ingress](../src/infra/ingress.py) are the configuration authority.
IAM model ARNs are derived from the packaged managed policy, keeping permissions
aligned with the worker's selected model. The selected US inference profile retains
an explicit model-routing IAM allowlist (`us-east-1`, `us-east-2`, `us-west-2`),
independent of deployment Region.
Review that list when changing the model/profile; configurable deployment does
not imply the services or profile are available in every Region.
`LOCAL_AWS_ENDPOINT` / `LOCAL_GITHUB_ENDPOINT` are paired fixed test-fixture
settings accepted by shared ingress utilities, not production endpoints. The
local dispatcher fixture supplies its AWS SDK endpoint separately.

## Trust and operating assumptions

- Run one dispatcher at a time with a demonstration queue fitting the first
  DynamoDB query page (1 MB). Neither atomic claims nor deployment concurrency
  controls/pagination enforce this. Do not overlap direct tests and webhook work.
- A valid webhook signature authenticates delivery, not issue-author authority.
  Issue text requests executable, potentially billable work; duplicate delivery
  is not deduplicated. Enable only for the trusted repository/use case.
- Repository scripts run with the runtime role and scoped GitHub token. Codex
  sandboxing does not remove that access. Changing repository authorization needs
  explicit trust/token-scoping review, not just a different Job payload.
- A worker gate authorizes only a fresh exact-reviewed-head merge-commit request.
  GitHub protection/required checks remain authoritative; no admin or auto-merge
  bypass, human-review requirement, or factory-specific check policy is added.
- Review is heuristic. Git checks detect persistent candidate mutation, not edits
  later undone or ignored files. Completion trusts matching GitHub merged PR state,
  not proof of correctness, merge parent structure, or current target ancestry.

## Logs and status

Worker output goes to `/aws/lambda-microvms/software-factory-poc`; image-build and
runtime roles write to that retained CloudWatch group. Receiver/dispatcher have
separate retained groups defined by CDK. Worker logs include stage markers,
actual command exit codes, review verdicts, and merge-gate/confirmation evidence
with credential redaction. Agent output is logged after each process exits.

`GET /status` exposes only `pending`, `ok`, or `error`. AWS-managed ingress owns
JWE authentication scoped to port 8080; the server does not authenticate headers.
The EventBridge Connection marker is not authentication. Execution history can
contain tokens and task data: restrict access and do not dump it into public logs.
GitHub retains source changes and PRs; status is not an artifacts API.

## Failure inspection and recovery boundaries

| Observation | What to check |
| --- | --- |
| Webhook rejected or no queued Job | Signature/original bytes, exact repository, issue action/content, deployment configuration, token/target resolution, then receiver logs. `202` means insertion only. |
| Pending work does not start | Active queue blocker, dispatcher logs/configuration, stream mapping, queue page size, and actual concurrent invocations. Baseline preparation/uncertain startup retains the claim. |
| Worker `error` | Last stage and actual exit code in CloudWatch: scripts, structured review, candidate/publication identity, GitHub merge eligibility/protection, or final scoped access. |
| Workflow failure | Retained VM ID and execution history; caught post-launch failures attempt termination but still fail the Job. |
| `CleanupUnconfirmed`, timeout, or manual stop | Ask the service for the retained VM's lifecycle and inspect execution history. Normal cleanup is bypassed on timeout/stop. The 30-minute VM lifespan is the fallback, not immediate confirmed termination. |
| Ambiguous publication/merge response | Inspect existing remote branch and bound PR state without retrying publication or creating a replacement commit/Job. Matching merged-PR confirmation may establish success despite a lost response. |

Only verified merged completion plus `TERMINATED` releases a queued Job as
`COMPLETED`. Failure, exhausted review, timeout, manual stop, and cleanup errors
leave `IN_PROGRESS`; new enqueues/notifications do not intentionally bypass it.
The factory provides no automatic failed-Job release, retry, reset, skip, or
recovery command. Investigate before any separately authorized manual action;
never assume a terminal workflow makes it safe to dispatch another Job.

Migrating an older SQS/Pipe deployment removes those resources rather than
migrating accepted messages. Drain accepted work before that stack update.
Artifact bucket, GitHub secret, and logs are retained; the Job table does not
have an explicit retain policy in current CDK. Review deletion/replacement
behavior before changing stacks.

## Verification limits

See [running](running.md) and [factory skills](factory-skills.md). Offline tests
and synthesis check source behavior, packaging, and workflow definitions; Floci
checks local ingress/handoff with simulated lifecycle operations. They do not
prove live model compliance, branch-protection enforcement, service availability,
cleanup under cancellation, or serialized deployed dispatch. Live deployment,
inference, and GitHub writes require separate authorization.
