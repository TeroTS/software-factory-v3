# Current Slice

Status: done

## Slice Goal
- Give the operator one tested demonstration baseline pinned for both gates.

## Slice Boundary
- Operator, triggered by the unprepared fixture, prepares and tests the calculator
  in `TeroTS/factory-smoke-test`, then records its committed GitHub SHA.
  The README-only repository becomes a tested, committed calculator baseline;
  no factory deployment or agent invocation is included.

## Stories In Scope
- `US-01 Prepare and pin the demonstration baseline`

## Stories Completed In This Slice
- `US-01 Prepare and pin the demonstration baseline`

## Stories Remaining In This Slice
- None.

## Contract Inputs
- [openapi.yaml](../../openapi.yaml): `Job` fixes the demonstration repository,
  pinned `baseSha`, and agent task; `main` and the test command are fixed.
- [docs/contracts.md](../contracts.md): both gate submissions reuse the same
  serialized `Job`. Neither submission surface is implemented in this slice.

## Decision Inputs
- None.

## Shared State Required Now
- A tested baseline commit available on GitHub, with its full SHA recorded locally.
- Baseline test command, actual exit code, outcome, and expected task acceptance
  test recorded in this note before completion.
- Both gates reuse this baseline without merging either gate's task PR.

## Operations / Endpoints / Surfaces In This Slice
- Demonstration repository: `calculator.py` exposing `add(a, b)` and
  `tests/test_calculator.py` using standard-library `unittest`.
- Local baseline evidence in this note; no factory endpoints or tooling changes.
- Operator confirmed `../factory-smoke-test` and authorized committing and
  pushing its baseline. Its clean `main` and GitHub remote both initially point
  to README-only commit `2e9474db937ff28bf468d91b8661e8f65de40070`.
- The confirmed remote is `TeroTS/factory-smoke-test`; align canonical repository
  references and the `Job.repository` constant with this operator correction.

## Tests Required
- From the demonstration repository root, run
  `python3 -m unittest discover -s tests -v`; record its actual exit code and result.
- Cover positive, negative, and zero addition without third-party packages.
- Focused end-to-end check: prepare the fixture, run its tests, commit and publish
  the approved baseline, then verify GitHub's recorded SHA and test that exact
  commit from a clean checkout. Retain command, exit code, SHA, and outcome.
- Record the expected agent acceptance check: the same test command must cover
  positive, negative, and zero multiplication while preserving addition; inspect
  the meaningful source/test diff for weakened tests in the later task story.

## Not Now
- Implementing `multiply(a, b)` or its task tests in the baseline; that is Codex's
  later agent task.
- US-02 image/resources, worker, model calls, and agent-side task publication.
- US-03 manual VM termination and Gate 1 acceptance.
- US-04 through US-06 orchestration, controlled failure, and hard-lifespan checks.
- New factory dependencies, APIs, or changes to demonstration deployment config.

## Done When
- GitHub contains the dependency-free calculator baseline and passing addition
  tests; the recorded SHA identifies that tested commit, not the original
  README-only SHA `2e9474db937ff28bf468d91b8661e8f65de40070`.
- Actual baseline command, exit code, outcome, SHA, and expected acceptance test
  are recorded locally; no SHA or verification result is invented.
- The task is fixed as adding `multiply(a, b)` with positive, negative, and zero
  tests while preserving addition, using this baseline and PR base `main` for
  both gates with separate unique task branches and unmerged PRs.

## Completion Summary
- Prepared `calculator.py:add(a, b)`, three `unittest` tests for positive,
  negative/mixed-sign, and zero addition, and test/task instructions in the
  demonstration README. No third-party dependencies or multiplication code.
- With operator authorization, committed and pushed the baseline to
  `TeroTS/factory-smoke-test` on `main`:
  `74daa6e2bca790d0a62d4d9ab47f07bca48fbc12`.
- Baseline command from the demonstration root:
  `python3 -m unittest discover -s tests -v` (Python 3.14.3, with
  `PYTHONDONTWRITEBYTECODE=1` to avoid generated files). Actual exit code: `0`;
  all three addition tests passed. Before implementation, the test-first run
  exited `1` because `calculator` did not exist.
- Focused end-to-end outcome: PASS. A fresh clone from GitHub checked out that
  exact SHA in detached state, ran the same command, and passed all three tests
  with actual exit code `0`. `git ls-remote origin refs/heads/main` matched the
  pinned SHA; both the fresh verification checkout and original checkout were
  clean. The verification clone is under this factory's ignored `.git/` area.
- Demonstration formatting/lint checks passed using the factory's existing Ruff:
  `uv run --locked ruff format --check ../factory-smoke-test/calculator.py ../factory-smoke-test/tests/test_calculator.py`
  and `uv run --locked ruff check` for the same paths. No dependencies installed
  in the demonstration repository; its `git diff --check` passed.
- Expected agent acceptance: add `multiply(a, b)` and tests demonstrating, at
  minimum, `multiply(2, 3) == 6`, `multiply(-2, 3) == -6`,
  `multiply(-2, -3) == 6`, and zero in either operand position. The same approved
  unittest command must exit `0`, all addition tests must remain intact/passing,
  and the meaningful source/test diff must not weaken tests. These task tests
  are not part of the baseline.
- Both gates must use this SHA and task, separate unique task branches/PRs
  targeting `main`, and leave PRs unmerged. Factory repository references and
  `Job.repository` now use the operator-confirmed `TeroTS/factory-smoke-test`.

## Next Slice Recommendation
- `US-02 Manually obtain a tested task PR`, using the recorded baseline SHA.
- Keep `US-03 Confirm manual termination and the Gate 1 result` in view: success
  or failure still requires manual cleanup; US-02 alone does not pass Gate 1.
- Keep `US-04 Submit a task that succeeds with orchestrated cleanup` deferred
  until Gate 1 passes; it must reuse the proven image/worker, not a new runner.
