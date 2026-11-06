# Build task-only stage prompts selecting immutable factory skills rather than same-named repository skills.
from pathlib import Path

SKILLS = Path("/opt/factory/skills")


# Supply task/repository context and canonical instructions, never another stage's history.
def prompt_for(job, branch, target, skill):
    return f"""Work only in {job["repository"]} at pinned baseline {job["baseSha"]} on branch {branch}, targeting {target}.
Agent task: {job["task"]}
Read and follow the canonical factory skill at {SKILLS / skill / "SKILL.md"} and its referenced material.
Select this absolute path even if repository skills have the same name; read applicable repository instructions/context too.
Your FIRST shell command must be exactly: printf factory-shell-ok
Stop on failure of this initial sandbox check, sandbox enforcement, model authentication or inference.
Never change provider/model, remove sandbox enforcement, display credentials, change AWS configuration, or merge.
Return on unrecoverable failure; prose or process exit alone is not completion evidence.
"""


# Implement without external publication; the worker independently verifies and commits the complete candidate.
def implementation_prompt(job, branch, target):
    return (
        prompt_for(job, branch, target, "implement")
        + """Implement the requested task and appropriate tests; do not weaken tests or modify deployment configuration.
Diagnose/fix/rerun failing ./scripts/verify in this invocation using installed equivalents where needed.
Return without commit, push, PR creation, or merge. The worker owns commit, fresh review, and publication ordering.
"""
    )


# Ask the canonical reviewer to assess the pinned commit, without implementation history or permission to edit.
def review_prompt(job, branch, target, head):
    return (
        prompt_for(job, branch, target, "code-review")
        + f"""Use the code-review skill to review commit {head} against baseline {job["baseSha"]} for the Agent task.
Review the complete git diff {job["baseSha"]} {head}, covering both Standards and Spec.
Remain read-only even if managed policy allows writes; do not run mutating verification.
Return verdict and required findings through the supplied output schema and dedicated last-message file.
"""
    )


# Authorize only publication of the reviewed commit, carrying actual verification/review evidence into the PR.
def publication_prompt(job, branch, target, head):
    return (
        prompt_for(job, branch, target, "pr")
        + f"""Publish existing reviewed commit {head}; review verdict PASS, no required findings.
Worker evidence: ./scripts/setup and baseline ./scripts/verify exited 0; independent post-implementation ./scripts/verify exited 0.
Do not edit source, rerun verification, stage, or create a replacement commit.
Push only {branch} to origin (never force-push or push the target); run gh pr create only if no PR already exists for it.
Create exactly one PR targeting {target}, left open and unmerged.
Use Summary, Evidence, Review, and Merge Danger sections, citing actual verification and the review verdict/commit.
Disclose skipped checks/material risks; AI review is not proof of correctness or compatibility with an advancing target.
Inspect existing branch/PR state before publishing and after any ambiguous response using git ls-remote and gh pr list/view.
Do not blindly create duplicates, retry publication, or merge. Return if publication cannot be confirmed.
"""
    )
