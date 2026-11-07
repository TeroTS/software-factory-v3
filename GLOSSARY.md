# Software factory PoC

A demonstration that turns a requested coding change into a tested, reviewed, merged GitHub PR and confirms VM termination.

## Language

**Job**:
One run of a requested coding change against an authorized repository and a pinned baseline, including verification, review, PR publication and merge, and VM cleanup.
_Avoid_: Issue, Agent task

**Agent task**:
The requested coding change supplied to a Job. For an issue-triggered Job, it consists of the issue title and body captured when the request is received.
_Avoid_: Job
