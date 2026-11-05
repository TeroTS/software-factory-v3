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
        if scenario == "publication-failure":
            # Publication can have remote effects before an uncertain failure reaches the worker.
            original_agent(*args)
            raise subprocess.CalledProcessError(1, ["gh", "pr", "create"])
        result = original_agent(*args)
        if scenario == "final-verification-failure":
            (args[2] / "greeting.txt").write_text("")
        return result

    try:
        with (
            patch.object(fixture.task, "capture", side_effect=fixture.external),
            patch.object(fixture.task, "run_agent", side_effect=agent),
        ):
            code = fixture.task.main()
        Path(evidence).write_text(
            json.dumps({"agent_calls": fixture.agent_calls, "prs": fixture.prs})
        )
        return code
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    sys.exit(main())
