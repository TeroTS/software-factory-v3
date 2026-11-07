# Run the real worker entry point with credential-free service/agent fixtures in a child process.
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from src.tests.test_worker import WorkerFixture


# Reuse the parent's pinned local repository; report synthetic publication and invocation counts for assertions.
def main():
    origin, workspace, scenario, evidence = sys.argv[1:]
    fixture = WorkerFixture()
    fixture.setUp(origin=origin, workspace=workspace)
    original_agent = fixture.agent
    review_heads = []

    def agent(*args):
        cli, prompt, repository, env, _secrets = args
        if "Independent worker gate authorizes merge" in prompt and scenario in (
            "merge-denied",
            "merge-conflict",
            "merge-auto-only",
            "merge-unresolved",
            "merge-timeout",
            "merge-local-change",
            "merge-head-race",
            "merge-checks-pending",
            "merge-checks-fail",
            "merge-checks-cancel",
            "merge-checks-error",
        ):
            fixture.agent_calls += 1
            fixture.stages.append("merge")
            fixture.events.append("merge")
            if scenario == "merge-local-change":
                (repository / "greeting.txt").write_text("unreviewed\n")
            if scenario == "merge-head-race":
                fixture.prs[0]["headRefOid"] = fixture.sha
            if scenario == "merge-auto-only":
                fixture.prs[0]["autoMergeRequest"] = {"enabled": True}
            if scenario != "merge-auto-only":
                raise subprocess.CalledProcessError(1, ["gh", "pr", "merge"])
            return
        result = original_agent(*args)
        cli, _prompt, repository, env, _secrets = args
        stage = fixture.stages[-1]
        if stage == "implement":
            if review_heads:
                fixture.assertIn(review_heads[-1], _prompt)
                fixture.assertIn("Review data", _prompt)
                fixture.assertIn(f"Required detail {len(review_heads)}", _prompt)
                if len(review_heads) > 1:
                    fixture.assertNotIn("Required detail 1", _prompt)
                if scenario != "repair-unchanged":
                    (repository / "greeting.txt").write_text("hello world\n")
                    (repository / "untracked.txt").write_text(
                        f"repaired detail {len(review_heads)}\n"
                    )
                if scenario == "repair-verification-failure":
                    (repository / "greeting.txt").write_text("")
                if scenario == "repair-process-error":
                    raise subprocess.CalledProcessError(7, ["codex", "exec"])
            if scenario == "final-verification-failure":
                (repository / "greeting.txt").write_text("")
        if stage == "review":
            review_heads.append(fixture.reviewed_head)
            output = Path(cli[cli.index("--output-last-message") + 1])
            finding = {
                "axis": "Spec",
                "location": "greeting.txt:1",
                "problem": f"Required detail {len(review_heads)} missing github_pat_synthetic_token",
                "correction": "Implement the requested greeting",
            }
            reviews = {
                "review-fail": {"verdict": "FAIL", "findings": [finding]},
                "review-pass-findings": {"verdict": "PASS", "findings": [finding]},
                "review-fail-empty": {"verdict": "FAIL", "findings": []},
                "review-invalid-verdict": {"verdict": "SKIP", "findings": []},
                "review-invalid-finding": {
                    "verdict": "FAIL",
                    "findings": [{**finding, "axis": "Other"}],
                },
                "review-empty-finding": {
                    "verdict": "FAIL",
                    "findings": [{**finding, "correction": " "}],
                },
                "review-extra-field": {
                    "verdict": "PASS",
                    "findings": [],
                    "sha": fixture.sha,
                },
                "review-nonobject": [],
                "review-nonlist": {"verdict": "PASS", "findings": None},
            }
            repairs = scenario.startswith("repair-")
            if repairs and (
                len(review_heads) == 1
                or (scenario == "repair-pass-3" and len(review_heads) == 2)
            ):
                reviews[scenario] = {"verdict": "FAIL", "findings": [finding]}
            if scenario in reviews:
                review = reviews[scenario]
                if isinstance(review, dict):
                    review = {"candidateHead": fixture.reviewed_head, **review}
                output.write_text(json.dumps(review))
            elif scenario == "review-missing":
                output.unlink()
            elif scenario == "review-malformed":
                output.write_text("not JSON github_pat_synthetic_token")
            elif scenario == "review-process-error":
                raise subprocess.CalledProcessError(7, ["codex", "exec"])
            elif scenario == "review-launch-error":
                raise OSError("github_pat_synthetic_token")
            elif scenario == "review-edit":
                (repository / "greeting.txt").write_text("review edit\n")
            elif scenario == "review-untracked":
                (repository / "review.txt").write_text("review edit\n")
            elif scenario == "review-commit":
                fixture.external(
                    ["git", "commit", "--allow-empty", "-m", "review commit"],
                    repository,
                    env,
                )
            elif scenario == "review-branch":
                fixture.external(["git", "switch", "-c", "reviewer"], repository, env)
            elif scenario == "review-stale" or (
                scenario == "repair-review-stale" and len(review_heads) == 2
            ):
                output.write_text(
                    json.dumps(
                        {
                            "candidateHead": fixture.sha
                            if len(review_heads) == 1
                            else review_heads[0],
                            "verdict": "PASS",
                            "findings": [],
                        }
                    )
                )
            elif scenario == "repair-review-process-error" and len(review_heads) == 2:
                raise subprocess.CalledProcessError(7, ["codex", "exec"])
            elif scenario == "repair-review-edit" and len(review_heads) == 2:
                (repository / "greeting.txt").write_text("review edit\n")
        if stage == "publish":
            if scenario in ("publication-ambiguous-success", "publication-failure"):
                if scenario == "publication-failure":
                    fixture.prs = []
                raise subprocess.CalledProcessError(1, ["gh", "pr", "create"])
            if scenario in (
                "publication-different-commit",
                "repair-publication-different-commit",
            ):
                fixture.external(
                    ["git", "commit", "--allow-empty", "-m", "replacement"],
                    repository,
                    env,
                )
                fixture.external(["git", "push", "origin", "HEAD"], repository, env)
                fixture.prs[0]["headRefOid"] = fixture.external(
                    ["git", "rev-parse", "HEAD"], repository, env
                ).strip()
            if scenario in ("publication-edit", "repair-publication-edit"):
                (repository / "greeting.txt").write_text("publisher edit\n")
            if scenario == "gate-dirty":
                (repository / "gate.txt").write_text("unreviewed\n")
            if scenario == "gate-head-change":
                fixture.git(
                    "update-ref",
                    "refs/heads/" + fixture.prs[0]["headRefName"],
                    fixture.sha,
                )
            if scenario == "gate-cross-repository":
                fixture.prs[0]["isCrossRepository"] = True
            if scenario == "gate-wrong-target":
                fixture.prs[0]["baseRefName"] = "wrong"
            if scenario == "gate-wrong-branch":
                fixture.prs[0]["headRefName"] = "wrong"
            if scenario == "gate-duplicate-pr":
                fixture.prs.append(dict(fixture.prs[0]))
            if scenario == "gate-already-merged":
                fixture.prs[0].update(state="MERGED", mergedAt="now")
        if stage == "merge":
            if scenario == "merge-branch-deleted":
                fixture.git("branch", "-d", fixture.prs[0]["headRefName"])
            if scenario == "merge-lost-response":
                raise subprocess.CalledProcessError(1, ["gh", "pr", "merge"])
            if scenario == "merge-wrong-pr":
                fixture.prs[0]["number"] = 3
            if scenario == "merge-target-advance":
                (fixture.origin / "later.txt").write_text("legitimate advancement\n")
                fixture.git("add", ".")
                fixture.git("commit", "-m", "later target change")
            if scenario in (
                "merge-one-parent",
                "merge-wrong-parent",
                "merge-three-parents",
            ):
                head = fixture.reviewed_head
                tree = fixture.git("rev-parse", "HEAD^{tree}").strip()
                parents = {
                    "merge-one-parent": [fixture.sha],
                    "merge-wrong-parent": [head, fixture.sha],
                    "merge-three-parents": [
                        fixture.sha,
                        head,
                        fixture.git("rev-parse", "HEAD").strip(),
                    ],
                }[scenario]
                commit = fixture.git(
                    "commit-tree",
                    tree,
                    *[part for parent in parents for part in ("-p", parent)],
                    "-m",
                    "invalid merge",
                ).strip()
                fixture.git("update-ref", "refs/heads/release", commit)
                fixture.prs[0]["mergeCommit"] = {"oid": commit}
            if scenario == "merge-no-ancestry":
                fixture.git("update-ref", "refs/heads/release", fixture.sha)
            if scenario == "merge-missing-commit":
                fixture.prs[0]["mergeCommit"] = None
        return result

    def external(args, cwd, env, secrets=None):
        if (
            args[:2] == ["gh", "api"]
            and fixture.stages
            and fixture.stages[-1] == "publish"
        ):
            if scenario == "gate-repository-change":
                return json.dumps(
                    {"full_name": "wrong/repository", "allow_merge_commit": True}
                )
            if scenario == "merge-method-prohibited":
                return json.dumps(
                    {
                        "full_name": fixture.job["repository"],
                        "allow_merge_commit": False,
                    }
                )
        if (
            args == ["git", "remote", "get-url", "origin"]
            and scenario == "gate-origin-change"
        ):
            return "https://github.com/wrong/repository.git"
        if (
            args == ["./scripts/verify"]
            and len(review_heads) == 3
            and scenario == "review-fail"
        ):
            raise AssertionError("Exhausted reviews must not advance to verification")
        if args == ["./scripts/verify"] and len(review_heads) == 2:
            if scenario == "repair-post-review-verification-failure":
                (cwd / "greeting.txt").write_text("")
            elif scenario == "repair-post-review-verification-edit":
                (cwd / "untracked.txt").write_text("verification mutation\n")
        return fixture.external(args, cwd, env, secrets)

    try:
        with (
            patch.object(fixture.task, "capture", side_effect=external),
            patch.object(fixture.task, "run_agent", side_effect=agent),
        ):
            code = fixture.task.main()
        Path(evidence).write_text(
            json.dumps(
                {
                    "agent_calls": fixture.agent_calls,
                    "prs": fixture.prs,
                    "stages": fixture.stages,
                    "events": fixture.events,
                    "reviewed_head": fixture.reviewed_head,
                    "reviewed_files": fixture.reviewed_files,
                }
            )
        )
        return code
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    sys.exit(main())
