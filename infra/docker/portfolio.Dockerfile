# GigBid Portfolio MCP server, stdio. Build from the repository root:
#   docker build -f infra/docker/portfolio.Dockerfile -t gigbid-portfolio-mcp .
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY devsim/pyproject.toml devsim/README.md devsim/
COPY devsim/src devsim/src
RUN uv sync --locked --no-dev --no-install-project

COPY agent agent
COPY rag rag
COPY mcp_servers mcp_servers

# The index and the downloaded embedding model persist in this volume.
ENV RAG_DATA_DIR=/data/index HF_HOME=/data/models FASTEMBED_CACHE_PATH=/data/models \
    PORTFOLIO_DIR=/portfolio PORTFOLIO_TENANT=me
VOLUME ["/data"]
ENTRYPOINT ["/app/.venv/bin/python", "-m", "mcp_servers.portfolio.local"]
