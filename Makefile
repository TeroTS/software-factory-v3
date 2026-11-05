.PHONY: setup format format-check lint test coverage dev verify synth preflight floci-up floci-test floci-down

setup:
	uv sync --locked
	npm ci --registry https://registry.npmjs.org --ignore-scripts --no-audit --no-fund

format:
	uv run --locked ruff format .

format-check:
	uv run --locked ruff format --check .

lint:
	uv run --locked ruff check .

test:
	uv run --locked python -m unittest discover -s tests -v

coverage:
	uv run --locked coverage run -m unittest discover -s tests -v
	uv run --locked coverage report

dev:
	uv run --locked python tools/logging_smoke.py

verify: format-check lint coverage dev

synth:
	npm run synth -- --quiet

# Local-only SDK tests use fixed loopback/Docker endpoints and explicit test credentials.
floci-up:
	docker compose -f compose.floci.yaml up -d --wait --wait-timeout 90

floci-test: floci-up
	FACTORY_FLOCI_TEST=1 uv run --locked python -m unittest discover -s tests -p test_floci.py -v

floci-down:
	docker compose -f compose.floci.yaml down

# Outer Docker seccomp permits nested namespaces; Codex's own sandbox stays enabled.
preflight:
	docker build --platform linux/arm64 -f image/Dockerfile -t software-factory-gate2-preflight .
	docker run --rm --platform linux/arm64 --network none --security-opt seccomp=unconfined --env AWS_EC2_METADATA_DISABLED=true software-factory-gate2-preflight python3.13 -c 'from worker.server import Runtime; Runtime("/workspace").validate()'
	docker run --rm --platform linux/arm64 --network none --security-opt seccomp=unconfined --env AWS_EC2_METADATA_DISABLED=true --env FACTORY_SANDBOX_TEST=1 --mount type=bind,source=$(CURDIR)/tests,target=/preflight-tests,readonly software-factory-gate2-preflight python3.13 -m unittest discover -s /preflight-tests -p test_sandbox.py -v
	docker run --rm --platform linux/arm64 --network none --security-opt seccomp=unconfined --env AWS_EC2_METADATA_DISABLED=true --mount type=bind,source=$(CURDIR)/tests,target=/preflight-tests,readonly software-factory-gate2-preflight python3.13 -m unittest discover -s /preflight-tests -p test_worker.py -v
	docker run --rm --platform linux/arm64 --network none --security-opt seccomp=unconfined --env AWS_EC2_METADATA_DISABLED=true --mount type=bind,source=$(CURDIR)/tests,target=/preflight-tests,readonly software-factory-gate2-preflight python3.13 -m unittest discover -s /preflight-tests -p test_server.py -v
