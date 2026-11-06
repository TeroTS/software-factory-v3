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

    def agent(*args):
        result = original_agent(*args)
        cli, _prompt, repository, env, _secrets = args
        stage = fixture.stages[-1]
        if stage == "implement" and scenario == "final-verification-failure":
            (repository / "greeting.txt").write_text("")
        if stage == "review":
            output = Path(cli[cli.index("--output-last-message") + 1])
            finding = {
                "axis": "Spec",
                "location": "greeting.txt:1",
                "problem": "Required greeting missing github_pat_synthetic_token",
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
            if scenario in reviews:
                output.write_text(json.dumps(reviews[scenario]))
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
        if stage == "publish":
            if scenario in ("publication-ambiguous-success", "publication-failure"):
                if scenario == "publication-failure":
                    fixture.prs = []
                raise subprocess.CalledProcessError(1, ["gh", "pr", "create"])
            if scenario == "publication-different-commit":
                fixture.external(
                    ["git", "commit", "--allow-empty", "-m", "replacement"],
                    repository,
                    env,
                )
                fixture.external(["git", "push", "origin", "HEAD"], repository, env)
                fixture.prs[0]["headRefOid"] = fixture.external(
                    ["git", "rev-parse", "HEAD"], repository, env
                ).strip()
            if scenario == "publication-edit":
                (repository / "greeting.txt").write_text("publisher edit\n")
        return result

    try:
        with (
            patch.object(fixture.task, "capture", side_effect=fixture.external),
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
