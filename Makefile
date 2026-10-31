.PHONY: setup format format-check lint test coverage dev verify

setup:
	uv sync --locked

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
