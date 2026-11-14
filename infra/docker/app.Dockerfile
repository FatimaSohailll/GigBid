# GigBid backend: one image for the API, worker, DevSim, the three MCP servers and the seed job.
# Each compose service runs a different command. Build from the repository root:
#   docker build -f infra/docker/app.Dockerfile -t gigbid-app .
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1

WORKDIR /app
# Dependencies first, so code changes do not reinstall them.
COPY pyproject.toml uv.lock ./
COPY devsim/pyproject.toml devsim/README.md devsim/
COPY devsim/src devsim/src
RUN uv sync --locked --no-install-project
# DevSim (devsim/) is a workspace member in the dev group, and it runs in this stack, so the
# dev group is installed; --no-install-project skipped it above, so install it now.
RUN uv sync --locked

COPY agent agent
COPY apps/api apps/api
COPY worker worker
COPY mcp_servers mcp_servers
COPY rag rag
COPY evals evals
COPY scripts scripts

# Generated data, the portfolio index, uploads and the embedding model live in one volume.
ENV RAG_DATA_DIR=/data/rag DEVSIM_DATA_DIR=/data/devsim UPLOAD_DIR=/data/uploads \
    HF_HOME=/data/models FASTEMBED_CACHE_PATH=/data/models
VOLUME ["/data"]

# Never root at runtime.
RUN useradd --create-home --uid 10001 gigbid && mkdir -p /data && chown -R gigbid /data /app
USER gigbid

CMD ["python", "-m", "scripts.run_api"]
