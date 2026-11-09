.PHONY: install dev test eval seed ingest demo lint format guards api

install:
	uv sync --locked

test:
	uv run --locked pytest

lint:
	uv run --locked ruff check .
	uv run --locked ruff format --check .
	uv run --locked mypy
	uv run --locked python scripts/ci/guards.py all

format:
	uv run --locked ruff format .
	uv run --locked ruff check --fix .

guards:
	uv run --locked python scripts/ci/guards.py all

api:
	uv run --locked uvicorn apps.api.main:app --reload --port 8000

# These fail on purpose until they are built, so nothing can pass without doing the work.
dev:
	@echo make dev is not built yet. It arrives with the Docker stack in Phase 16.
	@exit 1

seed:
	uv run --locked python -m devsim.datagen --out data/processed/devsim

ingest:
	uv run --locked python -m rag.ingest.pipeline --personas data/processed/devsim/personas

eval:
	@echo make eval is not built yet. It arrives with the evaluation pipelines in Phase 11.
	@exit 1

demo:
	@echo make demo is not built yet. It needs the bid graph and web interface.
	@exit 1
