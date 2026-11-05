# Serve snapshot-safe lifecycle hooks and one job's status; only run starts work.
import http.server
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

HOOK = "/aws/lambda-microvms/runtime/v1/"


# Decode AWS's envelope into a bounded, approved job; reject malformed inputs.
def parse_job(envelope):
    if (
        not isinstance(envelope, dict)
        or not isinstance(envelope.get("microvmId"), str)
        or not envelope["microvmId"].strip()
    ):
        raise ValueError("Missing MicroVM identifier")
    payload = envelope.get("runHookPayload")
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > 16384:
        raise ValueError("Invalid runHookPayload")
    try:
        job = json.loads(payload)
    except (ValueError, UnicodeError) as error:
        raise ValueError("Invalid job JSON") from error
    if not isinstance(job, dict) or set(job) != {"repository", "baseSha", "task"}:
        raise ValueError("Invalid job fields")
    authorized = os.environ.get("AUTHORIZED_REPOSITORY", "")
    target = os.environ.get("PR_TARGET_BRANCH", "")
    if (
        not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", authorized)
        or authorized.split("/")[-1] in (".", "..")
        or not target.strip()
    ):
        raise ValueError("Missing or invalid repository authorization configuration")
    if not isinstance(job["repository"], str) or job["repository"] != authorized:
        raise ValueError("Unapproved repository")
    if not isinstance(job["baseSha"], str) or not re.fullmatch(
        r"[0-9a-fA-F]{40}", job["baseSha"]
    ):
        raise ValueError("Expected full commit SHA")
    if not isinstance(job["task"], str) or not job["task"].strip():
        raise ValueError("Missing agent task")
    return job


# Own one asynchronous process group; worker exit zero requires task verification.
class Runtime:
    # Keep build-time state empty; workspace and deployment settings carry no token.
    def __init__(self, workspace):
        self.workspace = Path(workspace)
        self.process = None
        self.started = False
        self.terminal = None
        self.lock = threading.RLock()

    # Check tools and real workspace-write sandbox execution without credentials/network calls.
    def validate(self):
        for tool in (sys.executable, "git", "gh", "codex", "bwrap", "rg", "aws"):
            if shutil.which(tool) is None:
                raise OSError("Missing required executable")
        with tempfile.TemporaryDirectory(dir=self.workspace) as directory:
            (Path(directory) / ".codex").mkdir()
            result = subprocess.run(
                [
                    "codex",
                    "sandbox",
                    "-c",
                    'sandbox_mode="workspace-write"',
                    "-c",
                    "sandbox_workspace_write.network_access=true",
                    "--",
                    sys.executable,
                    "-c",
                    'from pathlib import Path; Path("write-probe").write_text("ok"); print("sandbox-validation-ok")',
                ],
                cwd=directory,
                env={
                    "PATH": os.environ["PATH"],
                    "HOME": directory,
                    "CODEX_HOME": str(Path(directory) / ".codex"),
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.stdout.strip() != "sandbox-validation-ok":
                raise OSError("Sandbox probe did not execute")
        print("Image validation: workspace-write sandbox shell passed", flush=True)

    # Spawn once with JSON on stdin, returning promptly; startup errors stay terminal.
    def start(self, job):
        with self.lock:
            if self.started:
                raise RuntimeError("Job already initialized")
            self.started = True
            try:
                self.process = subprocess.Popen(
                    [sys.executable, "-m", "src.worker.task"],
                    stdin=subprocess.PIPE,
                    start_new_session=True,
                    text=True,
                    env={**os.environ, "WORKSPACE": str(self.workspace)},
                )
                self.process.stdin.write(json.dumps(job))
                self.process.stdin.close()
            except (OSError, ValueError):
                self.terminal = "error"
                self.terminate()
                raise

    # Publish exactly pending/ok/error; terminal values never change afterward.
    def status(self):
        with self.lock:
            if self.terminal is None and self.process is not None:
                code = self.process.poll()
                if code is not None:
                    self.terminal = "ok" if code == 0 else "error"
            return self.terminal or "pending"

    # Stop the entire worker group with bounded escalation and flush server output.
    def terminate(self):
        with self.lock:
            self.status()
            if self.process is not None:
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=5)
                if self.terminal is None:
                    self.terminal = "error"
            sys.stdout.flush()
            sys.stderr.flush()


# Bind the listener; no client headers, request bodies, or diagnostic causes leak.
def make_server(runtime, address=("0.0.0.0", 8080)):
    # Route only the agreed lifecycle hooks and status endpoint.
    class Handler(http.server.BaseHTTPRequestHandler):
        # Suppress default access logs, which can include user-controlled input.
        def log_message(self, *args):
            pass

        # Return HTTP status with optional compact JSON, never an error cause.
        def respond(self, code, body=None):
            data = (
                b""
                if body is None
                else json.dumps(body, separators=(",", ":")).encode()
            )
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        # Expose only one-field job state; AWS ingress owns JWE authentication.
        def do_GET(self):
            if self.path == "/status":
                self.respond(200, {"status": runtime.status()})
            else:
                self.respond(404)

        # Validate input before launch; build hooks do no credential-dependent work.
        def do_POST(self):
            try:
                if self.path == HOOK + "ready":
                    self.respond(200)
                elif self.path == HOOK + "validate":
                    try:
                        runtime.validate()
                    except (OSError, subprocess.SubprocessError):
                        print(
                            "Image validation failed: local tools/workspace/process",
                            flush=True,
                        )
                        self.respond(503)
                    else:
                        self.respond(200)
                elif self.path == HOOK + "run":
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 100000:
                        raise ValueError("Invalid envelope length")
                    self.connection.settimeout(5)
                    job = parse_job(json.loads(self.rfile.read(length)))
                    runtime.start(job)
                    self.respond(200)
                elif self.path == HOOK + "terminate":
                    runtime.terminate()
                    self.respond(200)
                else:
                    self.respond(404)
            except (ValueError, UnicodeError):
                print("Run hook rejected invalid configuration", flush=True)
                self.respond(400)
            except RuntimeError:
                self.respond(409)
            except (OSError, subprocess.SubprocessError):
                print("Lifecycle hook failed: process or I/O failure", flush=True)
                self.respond(500)

    return http.server.ThreadingHTTPServer(address, Handler)


# Start the port-8080 server from an empty snapshot; never start a job at import.
def main():
    runtime = Runtime(os.environ.get("WORKSPACE", "/workspace"))
    make_server(runtime).serve_forever()


if __name__ == "__main__":
    main()
