---
name: pr
description: "Use when writing a PR body."
metadata:
  credits:
    skill: show-me
    author: Dex Horthy
    organisation: Humanlayer
    url: "https://github.com/humanlayer/skills/blob/main/plugins/show-me/skills/show-me/SKILL.md"
---

Choose a concise title derived from the supplied Agent task. Use the Summary and Evidence format below; preserve [attribution](CREDITS.md).

## Worker phase authorization

In a factory Job, follow the worker's current phase authorization:

- **Publication:** commit only the reviewed candidate, push only the assigned task branch, and create one PR against the configured target. Preserve the reviewed source; if further edits are needed, return to implementation and fresh review instead of publishing them under an earlier pass.
- **Merge:** request merge only after the independent worker gate authorizes the exact PR head. Honor all GitHub branch protection and required checks; repositories without required checks remain eligible. Use a merge commit and an exact-head precondition, without administrative bypass, force-push, or direct target-branch push. Confirm actual merge; enabling auto-merge alone is not completion.

Inspect the existing branch and PR after ambiguous publication/merge responses before any further action. Avoid duplicate PRs and unchecked retries. Unresolved outcomes are blocked; no automatic recovery or queue release is authorized. File reading, shell, Git, the installed GitHub CLI, repository-scoped credentials, and network access are required for publication/merge. Missing required capabilities produce a blocked result, not a silent skip.

This skill is not blanket permission to commit, push, or merge in unrelated interactive sessions. Those actions require explicit authorization.

## PR body template

```markdown
## Summary

<what changed and why: 1–6 bullets, using only as many as needed>

## Evidence

<how the relevant behavior was checked and what was observed>
```

## Guidance

Skip preambles and keep prose brief. Use the user's domain language from `GLOSSARY.md`.

### Summary

What changed and why: 1–6 bullets, using only as many as needed. Leave the complete implementation and line-by-line diff to GitHub's **Files changed** tab.

### Evidence

Show how the relevant behavior was checked and what was observed.

- Connect important behavioral claims to tests, example outputs, screenshots, or measurements.
- Report checks performed and actual results; include material gaps or uncertainty.
- Use before/after comparisons only when they clarify the change.
- Keep evidence concise. Omit routine commit hashes, workflow narration, and publication bookkeeping.
- Claim only what the available evidence supports.

Example:

Tests confirm multiplication handles positive, negative, and zero operands while preserving addition. `./scripts/verify` passed all 6 tests.
