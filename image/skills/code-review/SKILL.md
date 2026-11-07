---
name: code-review
description: "Review the full candidate against a pinned baseline on Standards and Spec. Use for fresh-context factory review or review of a branch, PR, or work-in-progress change."
---

# Code Review

Review both axes in one read-only conversation:

- **Standards:** does the candidate follow the target repository's documented coding standards?
- **Spec:** does it implement the supplied Agent task or spec correctly and completely?

## Process

1. Read the supplied Agent task/spec, pinned baseline, and candidate identity. Read applicable repository instructions, glossary, ADRs, and coding/contribution standards. Missing essential context or required capabilities is a blocked review, never a pass. In a factory Job, report the blocker and return; do not bootstrap a tracker or wait for interactive clarification.
2. Confirm the baseline resolves. Review the complete candidate relative to that exact baseline, including committed, staged, unstaged, and untracked changes. Inspect `git diff <baseline>` and Git status, then read untracked files and connected callers/tests/configuration. Reviewing only HEAD or a moving target-branch name misses candidate work.
3. Inspect correctness, security, failure handling, test adequacy, task compliance, and unnecessary scope. Separate actionable required corrections from optional improvements. Every concrete correctness, security, documented-standards, or task-compliance defect is blocking; speculative refactors and optional improvements are not required findings.
4. Return findings without editing files, running mutating verification, committing, publishing, or merging. The worker checks candidate integrity and schedules any repair in an implementation stage. Do not repair during review or require parallel sub-agents.

## Findings and verdict

In a factory Job, follow the worker-supplied output schema and result destination. Use an explicit PASS or FAIL and a list of required findings. Each finding identifies its axis (`Standards` or `Spec`), location, concrete problem, and required correction. Cover both axes before deciding the verdict.

- **PASS:** both axes assessed and no required corrections.
- **FAIL:** one or more actionable required corrections.

Missing requirements, an unavailable required tool, or an incomplete review cannot count as PASS. Report a blocker rather than fabricating findings or a verdict unsupported by evidence. Keep result files outside the candidate repository as directed by the worker. A prose claim of success does not replace structured evidence.

Outside the worker, report Standards and Spec separately with concrete locations and supporting rules/requirements; explicitly disclose missing context rather than treating it as a pass.

## Design judgement

Treat these as heuristics, not mandatory refactoring rules: mysterious names, duplicated logic, feature envy, data clumps, primitive obsession, repeated conditionals, shotgun surgery, divergent change, speculative generality, message chains, middle men, and refused inheritance. Flag only concrete problems relevant to this task; repository standards override generic preferences. Optional architecture exploration is not part of ordinary review.

## Required capabilities

File reading/search, shell commands, and Git suffice for ordinary review. Read bundled references directly; no Skill tool, tracker integration, or sub-agent capability is assumed. The worker provides fresh process history and the pinned candidate; the reviewer remains read-only even when the managed sandbox permits writes.
