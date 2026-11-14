.PHONY: install dev test eval seed ingest db graphs openapi e2e demo lint format guards api worker

db:
	docker compose -f infra/docker-compose.yml up -d --wait postgres redis qdrant

worker:
	uv run --locked arq worker.main.WorkerSettings

openapi:
	uv run --locked python -m scripts.export_openapi
	npm --prefix apps/web run gen:api

e2e:
	npm --prefix apps/web run test:e2e

graphs:
	uv run --locked python -m scripts.render_graphs

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

# The whole stack: web, API, worker, MCP servers, DevSim, Postgres, Redis, Qdrant and Ollama.
dev:
	docker compose -f infra/docker-compose.yml --env-file .env up --build

seed:
	uv run --locked python -m devsim.datagen --out data/processed/devsim

ingest:
	uv run --locked python -m rag.ingest.pipeline --personas data/processed/devsim/personas

# The last two steps call a model for every job. See docs/RUNBOOK.md for free-tier limits.
eval:
	uv run --locked python -m evals.runners.rag_eval --all
	uv run --locked python -m evals.runners.ablation
	uv run --locked python -m evals.runners.decision_eval
	uv run --locked python -m evals.runners.gigsim_outcomes --judge
	uv run --locked python -m evals.results_table

# The stack in the background, ready for a reviewer: open the web app and sign in with DEMO_EMAIL.
demo:
	docker compose -f infra/docker-compose.yml --env-file .env up --build --detach --wait web api worker
	@echo GigBid is running at http://localhost:3000. Sign in with DEMO_EMAIL and DEMO_PASSWORD from .env.
