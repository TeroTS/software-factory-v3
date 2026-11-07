# Control a disposable post-resume Docker probe; only the fixed /run hook starts rootful Docker.
import http.server
import json
import os
import pwd
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

HOOK = "/aws/lambda-microvms/runtime/v1/"


# Own one experimental process group and its credential-free result; build hooks never launch it.
class Runtime:
    # Store empty snapshot state and the root-owned evidence directory.
    def __init__(self, evidence):
        self.evidence = Path(evidence)
        self.process = None
        self.lock = threading.Lock()

    # Launch the fixed root supervisor once after resume; the supervisor runs Codex as factory.
    def start(self):
        with self.lock:
            if self.process is not None:
                raise RuntimeError("Probe already started")
            self.evidence.mkdir(parents=True, exist_ok=True)
            self.process = subprocess.Popen(
                [sys.executable, __file__, "--probe", str(self.evidence)],
                start_new_session=True,
            )

    # Publish pending until process exit, then return the saved report or an explicit process failure.
    def status(self):
        with self.lock:
            if self.process is None or self.process.poll() is None:
                return {"status": "pending"}
            report = self.evidence / "result.json"
            if report.exists():
                return json.loads(report.read_text())
            return {
                "status": "error",
                "stage": "supervisor",
                "exitCode": self.process.returncode,
            }

    # Stop the entire supervisor/daemon/worker group with bounded escalation on VM termination.
    def terminate(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=5)


# Expose fixed lifecycle hooks and evidence behind AWS's authenticated ingress; accept no commands or payloads.
def make_server(runtime, address=("0.0.0.0", 8080)):
    # Keep experimental routing independent of production Job authorization/contracts.
    class Handler(http.server.BaseHTTPRequestHandler):
        # Suppress headers and request paths from access logs.
        def log_message(self, *args):
            pass

        # Return JSON evidence without request-controlled content.
        def respond(self, code, body=None):
            data = json.dumps(body or {}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        # Serve only fixed result evidence; AWS owns token authentication.
        def do_GET(self):
            if self.path == "/status":
                self.respond(200, runtime.status())
            else:
                self.respond(404)

        # Acknowledge snapshot hooks without Docker; run is single-use and termination is bounded.
        def do_POST(self):
            try:
                if self.path in (HOOK + "ready", HOOK + "validate"):
                    self.respond(200)
                elif self.path == HOOK + "run":
                    runtime.start()
                    self.respond(200)
                elif self.path == HOOK + "terminate":
                    runtime.terminate()
                    self.respond(200)
                else:
                    self.respond(404)
            except RuntimeError:
                self.respond(409)
            except OSError:
                self.respond(500)

    return http.server.ThreadingHTTPServer(address, Handler)


# Run post-resume Docker and factory-user Codex probes; save/return credential-free evidence including failures.
def run_probe(evidence):
    evidence = Path(evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    result = {"status": "error", "stage": "diagnostics"}
    started = time.monotonic()
    daemon = None
    secrets = []
    workspace = Path(os.environ.get("WORKSPACE", "/workspace")) / "compose-probe"
    daemon_log = evidence / "dockerd.log"
    try:
        diagnostics = subprocess.run(
            [
                "sh",
                "-c",
                "set -x; uname -a; cat /proc/self/status; ls -l /proc/self/ns; cat /proc/mounts; df -T /workspace; docker --version; docker compose version; rpm -q iptables-nft iproute procps-ng util-linux tar gzip findutils",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        result["diagnostics"] = diagnostics.stdout + diagnostics.stderr
        result["freeDiskBeforeBytes"] = shutil.disk_usage(evidence).free
        result["stage"] = "daemon"
        with daemon_log.open("w") as log:
            daemon_started = time.monotonic()
            daemon = subprocess.Popen(
                ["dockerd", "--host=unix:///var/run/docker.sock", "--group=docker"],
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            deadline = time.monotonic() + 60
            while True:
                if daemon.poll() is not None:
                    raise RuntimeError("Docker daemon exited before readiness")
                try:
                    ready = subprocess.run(
                        ["docker", "info", "--format", "{{json .}}"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    ready = None
                    result["readinessTimeouts"] = result.get("readinessTimeouts", 0) + 1
                if ready is not None and ready.returncode == 0:
                    info = json.loads(ready.stdout)
                    # Keep sizing/storage facts, not credential-bearing daemon proxy settings.
                    result["dockerInfo"] = {
                        key: info[key]
                        for key in (
                            "Driver",
                            "Architecture",
                            "KernelVersion",
                            "MemTotal",
                            "CgroupVersion",
                            "NCPU",
                            "ServerVersion",
                        )
                        if key in info
                    }
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Docker daemon readiness exceeded 60 seconds")
                time.sleep(1)
            result["daemonStartupSeconds"] = round(time.monotonic() - daemon_started, 3)
            result["stage"] = "workspace"
            factory = pwd.getpwnam("factory")
            workspace.mkdir(parents=True, exist_ok=True)
            os.chown(workspace, factory.pw_uid, factory.pw_gid)
            codex_home = Path(factory.pw_dir) / ".codex"
            codex_home.mkdir(mode=0o700, exist_ok=True)
            os.chown(codex_home, factory.pw_uid, factory.pw_gid)
            env = {
                **os.environ,
                "HOME": factory.pw_dir,
                "CODEX_HOME": str(codex_home),
            }
            options = {
                "cwd": workspace,
                "env": env,
                "user": "factory",
                "group": "factory",
                "extra_groups": ["docker"],
                "capture_output": True,
                "text": True,
            }
            subprocess.run(
                ["git", "init", "-b", "main"], timeout=10, check=True, **options
            )
            script = [
                sys.executable,
                "/opt/factory/image/compose_probe.py",
                str(workspace),
            ]
            result["stage"] = "sandbox"
            sandbox = subprocess.run(
                [
                    "codex",
                    "sandbox",
                    "-c",
                    'sandbox_mode="workspace-write"',
                    "-c",
                    "sandbox_workspace_write.network_access=true",
                    "--",
                    *script,
                ],
                timeout=360,
                check=True,
                **options,
            )
            result["sandboxLog"] = (sandbox.stdout + sandbox.stderr)[-65536:]
            result["sandboxResult"] = json.loads(
                (workspace / "stack-result.json").read_text()
            )
            (workspace / "stack-result.json").unlink()
            result["stage"] = "codex-exec"
            import boto3

            from src.worker.task import credentials, redact

            credentials(boto3.Session(region_name="us-east-1"), secrets)
            prompt = (
                "Feasibility probe only. Do not edit any scripts, configuration, or results. Run exactly this command using your normal sandboxed shell tool: "
                + " ".join(script)
                + ". Report its actual exit status. Do not retry, change sandbox policy, run sudo, commit, push, or use GitHub."
            )
            agent = subprocess.run(
                [
                    "codex",
                    "exec",
                    "--ephemeral",
                    "-C",
                    str(workspace),
                    "--add-dir",
                    str(workspace / ".git"),
                    "-",
                ],
                input=prompt,
                timeout=420,
                check=True,
                **options,
            )
            result["codexLog"] = redact(agent.stdout + agent.stderr, secrets)[-65536:]
            result["codexResult"] = json.loads(
                (workspace / "stack-result.json").read_text()
            )
            if result["codexResult"]["uid"] != factory.pw_uid:
                raise RuntimeError("Probe did not execute as factory")
            result["status"] = "ok"
            result["stage"] = "complete"
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        from src.worker.task import redact

        result["error"] = type(error).__name__ + ": " + redact(str(error), secrets)
        output = "".join(
            value.decode(errors="replace") if isinstance(value, bytes) else value
            for value in (
                getattr(error, "stdout", None) or "",
                getattr(error, "stderr", None) or "",
            )
        )
        result["failureLog"] = redact(output, secrets)[-65536:]
    except Exception as error:  # noqa: BLE001 - SDK credential error bodies must not reach raw tracebacks
        result["error"] = type(error).__name__
    finally:
        result["freeDiskAfterBytes"] = shutil.disk_usage(evidence).free
        result["elapsedSeconds"] = round(time.monotonic() - started, 3)
        for key, path in (
            ("vmMemory", "/proc/meminfo"),
            ("cgroupMemoryPeakBytes", "/sys/fs/cgroup/memory.peak"),
        ):
            if Path(path).exists():
                result[key] = Path(path).read_text()
        if daemon is not None and daemon.poll() is None:
            if (workspace / "compose.yaml").exists():
                try:
                    cleanup = subprocess.run(
                        [
                            "docker",
                            "compose",
                            "-p",
                            "compose-probe",
                            "-f",
                            str(workspace / "compose.yaml"),
                            "down",
                            "--volumes",
                            "--remove-orphans",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=60,
                        check=False,
                    )
                    result["supervisorCleanupExitCode"] = cleanup.returncode
                    if cleanup.returncode:
                        result["status"] = "error"
                except (OSError, subprocess.SubprocessError) as error:
                    result["supervisorCleanupError"] = type(error).__name__
                    result["status"] = "error"
            daemon.terminate()
            try:
                daemon.wait(timeout=15)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.wait(timeout=5)
        result["daemonLog"] = (
            daemon_log.read_text()[-32768:] if daemon_log.exists() else ""
        )
        temporary = evidence / "result.tmp"
        temporary.write_text(json.dumps(result))
        temporary.replace(evidence / "result.json")
    return result


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--probe":
        raise SystemExit(0 if run_probe(Path(sys.argv[2]))["status"] == "ok" else 1)
    make_server(Runtime("/var/log/compose-experiment")).serve_forever()
