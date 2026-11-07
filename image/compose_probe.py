# Run the fixed integration stack inside Codex's workspace-write sandbox; always tear down project resources.
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path


# Run the fixed stack in workspace; save/return verified checks after connectivity, persistence and cleanup, propagating failures.
def run_stack(workspace):
    workspace = Path(workspace)
    for filename in ("compose.yaml", "Dockerfile", "http_dependency.py"):
        shutil.copyfile(
            Path(__file__).parent / "compose" / filename, workspace / filename
        )
    (workspace / "from-worker").write_text("worker-write")
    compose = [
        "docker",
        "compose",
        "-p",
        "compose-probe",
        "-f",
        str(workspace / "compose.yaml"),
    ]
    evidence = {}
    try:
        subprocess.run(compose + ["pull", "db"], cwd=workspace, check=True, timeout=120)
        subprocess.run(
            compose + ["up", "--build", "-d", "--wait", "--wait-timeout", "90"],
            cwd=workspace,
            check=True,
            timeout=240,
        )
        with urllib.request.urlopen(
            "http://127.0.0.1:18080/from-container", timeout=5
        ) as response:
            assert response.read() == b"dns-db-bind-ok"
        assert (workspace / "from-container").read_text() == "dns-db-bind-ok"
        subprocess.run(
            compose + ["exec", "-T", "db", "valkey-cli", "SET", "probe", "volume-ok"],
            cwd=workspace,
            check=True,
            timeout=10,
        )
        subprocess.run(
            compose + ["restart", "db"], cwd=workspace, check=True, timeout=30
        )
        result = subprocess.run(
            compose + ["exec", "-T", "db", "valkey-cli", "GET", "probe"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.stdout.strip() == "volume-ok"
        evidence["checks"] = [
            "pull",
            "build",
            "dns",
            "health",
            "database",
            "named-volume",
            "workspace-bind",
            "worker-port",
        ]
    finally:
        try:
            subprocess.run(
                compose + ["logs", "--no-color", "--tail", "50"],
                cwd=workspace,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as error:
            # Diagnostic failure must not skip cleanup or replace the original stack blocker.
            print(
                "Compose log capture failed: " + type(error).__name__, file=sys.stderr
            )
        subprocess.run(
            compose + ["down", "--volumes", "--remove-orphans"],
            cwd=workspace,
            check=True,
            timeout=60,
        )
    for kind in ("container", "network", "volume"):
        result = subprocess.run(
            [
                "docker",
                kind,
                "ls",
                "-q",
                "--filter",
                "label=com.docker.compose.project=compose-probe",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert not result.stdout.strip(), f"Leaked Compose {kind}"
    evidence["cleanup"] = "ok"
    evidence["uid"] = os.getuid()
    (workspace / "stack-result.json").write_text(json.dumps(evidence))
    print("compose-probe-ok", flush=True)
    return evidence


if __name__ == "__main__":
    run_stack(Path(sys.argv[1]))
