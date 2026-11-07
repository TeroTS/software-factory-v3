# Disposable Docker Compose experiment

Status: feasibility verified on two fresh AWS MicroVMs on 2026-11-07. This is a
rootful integration-test probe, not a production worker feature. The originating investigation is
`docs/plans/docker-compose-feasibility.md` (operator-owned, currently untracked).

## Boundary and lifecycle

The [runner](../src/tools/compose_experiment.py) stages the existing worker image
and appends [experimental tools/startup](../image/compose/Dockerfile.append).
It never updates `FactoryImage`, the production image, workflow, or existing IAM
roles. It creates a separate image, source object, build/runtime roles, and log
group, using the existing artifact bucket. Runtime permissions allow only logs
and the existing Bedrock model; there are no GitHub/Secrets Manager permissions.

[Experiment flow](diagrams/key_flow_compose_experiment.d2) shows the isolated
root controller and factory-user execution path. `/ready` and `/validate` do not
start Docker. `/run` starts one asynchronous root supervisor after snapshot
resume; duplicate starts return 409. The supervisor starts rootful Docker and
runs the fixed probe first through `codex sandbox`, then through a fresh
model-backed `codex exec`, both as `factory` with the existing managed policy.
Only the fixed probe is exposed; the HTTP interface accepts no arbitrary commands.
AWS owns ingress authentication; evidence tokens are port-8080 scoped and never
written to disk. Termination stops the entire supervisor process group.

The [stack probe](../image/compose_probe.py) checks image pulling, a small build,
service-name DNS, health checks, database persistence across restart, named
volumes, workspace reads/writes, and worker connectivity to loopback port 18080.
It always attempts `down --volumes --remove-orphans`, then verifies no project
containers, networks, or volumes remain. The supervisor makes a final cleanup
attempt before stopping Docker.

Docker Engine/CLI 29.9.3 and Compose 5.7.0 are ARM64 binaries pinned by SHA-256;
required OS helper packages are explicitly version-pinned in the experimental
Dockerfile suffix. Base MicroVM major version 3, ARM64, 2 GiB minimum memory,
internet egress and `additionalOsCapabilities=["ALL"]` match the experiment.

## Run

Read [operator prerequisites](running.md#operator-only-live-tools) first.
Image builds, AWS calls/cost, and model inference require separate authorization.
This probe performs no repository cloning, GitHub writes, or merges.

```sh
# Local ARM64 build and offline real-sandbox validation only.
uv run --locked python -m src.tools.compose_experiment --local-only

# Authorized experiment: local gates, approval prompt, AWS image build, two VMs.
uv run --locked python -m src.tools.compose_experiment
```

The runner prints its unique `software-factory-compose-<12 hex digits>` name and
`cdk.out/<name>/` evidence directory. It refuses to overwrite an existing local
run or S3 source object.
AWS account/Region authentication and the existing idle check apply before cloud
creation. Keep the factory queue/webhook idle yourself; the idle check does not
prevent incoming queued work.

Each VM has a 900-second maximum lifespan. The runner polls `/status`, terminates
the known VM in `finally`, and requires service-confirmed `TERMINATED` before the
second launch. Failed probes still get a second fresh VM to reproduce the blocker;
an unknown launch ID or unconfirmed termination stops further work. The CLI exits
nonzero if either probe fails. Success is
both fresh-VM reports passing through the real Codex execution path, not merely a
successful build or root-shell/local stack run.

## Evidence and recovery

Files under `cdk.out/<name>/`:

- `source.zip`, `factory-source/`: exact source sent to AWS and local image build.
- `resources.json`, `image-version.json`: cloud identifiers and resolved build
  configuration/state.
- `trial-1.json`, `trial-2.json`: durable launch token/VM ID, stage, diagnostics,
  daemon/agent logs, measurements, stack checks and confirmed termination.

Reports capture kernel, capabilities, namespaces, cgroup mounts, filesystem,
free disk before/after, Docker info/startup time, elapsed time, and cgroup memory
peak where the kernel exposes it. `/proc/meminfo` is a point-in-time observation,
not a substitute for a missing peak measurement. Logs are limited in report size;
CloudWatch remains the build-log source.

After interruption, inspect retained identifiers and account VMs before any new
run. Do not rerun the CLI with a new name as recovery. If a launch response was
lost, its 900-second lifespan is only a fallback, not confirmed cleanup. Partial
provisioning may leave resources named with the experiment prefix even before
`resources.json` is written. Inspect those exact names; do not alter factory
resources.

The tool retains its experimental image, two roles, S3 source object and log group
for inspection. Log retention is seven days; retained image/source/log storage can
incur continuing costs. After saving evidence, delete only this experiment's
image, inline `experiment` policies/roles, source object and log group. Confirm all
VMs are terminated before removing resources.

## Limits

`ALL` capabilities and Docker socket access give `factory` root-equivalent control
inside this disposable VM. Codex's normal sandbox remains enabled, but Docker
access is not a security boundary. No production deployment, rootless evaluation,
filesystem-boundary hardening, full failure matrix, workload sizing, concurrent
Jobs, or suspend/resume-with-live-containers support is established here.

## Recorded run

Image `software-factory-compose-26b99b5568a1`, version `1.0`, reached
`SUCCESSFUL`/`ACTIVE` using resolved base version **3.1**, ARM64, the requested
2 GiB minimum, and `ALL` capabilities. Both fresh VMs passed **all eight stack
checks and cleanup twice**: first as `factory` through `codex sandbox`, then
through real Bedrock-backed `codex exec` with the managed workspace-write policy.
Both result files reported UID **1000** and cleanup `ok`; final supervisor cleanup
also exited zero.

| Measurement | Fresh VM 1 | Fresh VM 2 |
| --- | --- | --- |
| VM ID | `microvm-d2a0f649-451e-3a4b-b78f-b18b9294253f` | `microvm-3189afa4-8b00-361e-8668-f63dd4711255` |
| Daemon readiness | 17.971 s | 19.229 s |
| Total probe time | 99.021 s | 88.733 s |
| Application-cgroup memory peak | 1011 MiB | 983 MiB |
| Free disk before / after | 5.290 / 4.943 GiB | 5.290 / 4.945 GiB |
| Service-confirmed final state | `TERMINATED` | `TERMINATED` |

The guest kernel was `6.1.166-24.303.amzn2023.aarch64`; the root filesystem was
ext4 and cgroup v2 was mounted read/write. Docker used its containerd **overlayfs**
snapshotter. Effective root capabilities were `000001ffffffffff`, with seccomp
mode 0. Docker reported approximately **8 GiB guest RAM**, despite the requested
2 GiB image minimum. The cgroup memory limit was not captured: measured usage does
**not** establish a 2 GiB runtime ceiling or general workload sizing.

Daemon logs warned that IPv6 nftables NAT/filter tables were unsupported. Valkey
warned about disabled memory overcommit and a 1024-file-descriptor limit. These did
not prevent this IPv4 test, but arbitrary integration suites remain unverified.
A local nested-container probe hit an overlay mount `invalid argument`; that
local storage limitation did **not** reproduce on the AWS ext4 guest.

Two earlier disposable images exposed probe bugs rather than proven Docker
blockers: prematurely aborting readiness on a five-second command timeout,
losing byte-valued timeout output, and failing to initialize Codex's home. The
corrected setup creates the Codex home and a real Git repository before launch;
regression tests cover these conditions. All six AWS trial VMs terminated.

Full source, reports, and logs are archived locally under
`cdk.out/software-factory-compose-26b99b5568a1/`. The tested source ZIP SHA-256 is:

```text
19e0e6e031a677452da13f805c0bb7c1f40c10669d26fe4a1238a488661ee64a
```

After the live run, locally-tested report guards added 64 KiB transcript tails,
safe SDK error summaries, and a sizing/storage-only Docker-info projection that
omits credential-bearing proxy settings. The operator runner also gained a
unit-tested conditional S3 upload protecting existing source objects. These
post-run guards were not re-exercised against AWS; successful stack execution and
image configuration are unchanged. After saving evidence, all three
experimental images, their six roles, source objects, and log groups were deleted;
image deletion was service-confirmed. The deployed factory image remained at
version **7.0**.

## Verification

- `uv run --locked python -m unittest src.tests.test_compose_experiment -v`:
  13 focused tests passed, using mocked external process/AWS boundaries.
- `make verify`: Ruff format/lint, 133 tests (12 explicit skips), coverage and
  logging passed with AWS credentials/profile inputs disabled; test runtime was
  24.669 seconds. Branch coverage was **94%**,
  down from the prior **97%** because of new experimental controller/live failure
  paths; existing code's missed-statement count remained unchanged (22).
- `make synth`: passed; native production image configuration/staging is unchanged.
- Experimental ARM64 image: build and offline sandbox validation passed; all five
  real Linux sandbox tests passed. The host-daemon stack passed; the separate
  nested-local storage limitation is recorded above. Both final AWS probes passed.
- Python `compileall`, affected local documentation links, whitespace, and D2
  diagram compilation passed. No static typechecker is configured in this repo.
- Standards and Spec review used the pre-work HEAD as its baseline. Review was
  manual: this session did not provide independent parallel review agents.

Production `make preflight` and `make floci-test` were not rerun because worker and
ingress code/configuration are unchanged; use those commands if changing those
areas. The experimental image received the dedicated checks above. jsii warned
about the installed Node 25 version; use the documented Node 22/24 LTS toolchain.
