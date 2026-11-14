# GigBid in one container, for a free host that runs a single image on one port (Hugging Face
# Spaces). The same code as the compose stack: Postgres, Redis and Qdrant run beside DevSim, the
# three MCP servers, the API, the worker and the web app, started by deploy/space/start.sh.
#
# The free tier has no persistent disk, so every boot seeds the demo again: DevSim data, the
# portfolio index and the reviewer login. Build context: the repository root.

# ---- web app -----------------------------------------------------------------------------
FROM node:24.11.0-bookworm-slim AS web
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web ./
ENV API_URL=http://127.0.0.1:8000 NEXT_TELEMETRY_DISABLED=1
RUN npm run build && npm prune --omit=dev

# ---- everything else ---------------------------------------------------------------------
FROM python:3.13-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql redis-server supervisor \
       libunwind8 libstdc++6 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=qdrant/qdrant:v1.20.0 /qdrant /opt/qdrant
COPY --from=node:24.11.0-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY devsim/pyproject.toml devsim/README.md devsim/
COPY devsim/src devsim/src
RUN uv sync --locked --no-install-project && uv sync --locked

COPY agent agent
COPY apps/api apps/api
COPY worker worker
COPY mcp_servers mcp_servers
COPY rag rag
COPY evals evals
COPY scripts scripts
COPY --from=web /web/.next /web/.next
COPY --from=web /web/node_modules /web/node_modules
COPY --from=web /web/package.json /web/package.json
COPY deploy/space/supervisord.conf deploy/space/start.sh /app/deploy/

# Hugging Face runs the container as user 1000, so everything it writes is owned by that user.
RUN useradd --create-home --uid 1000 gigbid \
    && mkdir -p /data /run/postgresql \
    && chown -R gigbid /data /app /web /run/postgresql /opt/qdrant \
    && chmod +x /app/deploy/start.sh
USER gigbid

# Addresses inside the container. Secrets come from the host's secret settings at run time.
ENV DATA=/data \
    DATABASE_URL=postgresql://gigbid@127.0.0.1:5432/gigbid \
    REDIS_URL=redis://127.0.0.1:6379/0 \
    QDRANT_URL=http://127.0.0.1:6333 \
    RAG_DATA_DIR=/data/rag DEVSIM_DATA_DIR=/data/devsim UPLOAD_DIR=/data/uploads \
    HF_HOME=/data/models FASTEMBED_CACHE_PATH=/data/models \
    GIGSIM_BASE_URL=http://127.0.0.1:8081 DEVSIM_SEED_DIR=/data/devsim/marketplace \
    DEVSIM_API_TOKENS=space-devsim-ai=ai_ml_engineer,space-devsim-fs=fullstack_developer,space-devsim-da=data_analyst \
    DEVSIM_ADMIN_TOKEN=space-devsim-admin \
    MCP_MARKETPLACE_URL=http://127.0.0.1:8090/mcp \
    MCP_PORTFOLIO_URL=http://127.0.0.1:8091/mcp \
    MCP_WORKSPACE_URL=http://127.0.0.1:8092/mcp \
    MCP_MARKETPLACE_USERS=space-mkt-ai:ai_ml_engineer:space-devsim-ai,space-mkt-fs:fullstack_developer:space-devsim-fs,space-mkt-da:data_analyst:space-devsim-da \
    MCP_PORTFOLIO_USERS=space-portfolio:ai_ml_engineer \
    MCP_WORKSPACE_USERS=space-workspace:ai_ml_engineer \
    MCP_MARKETPLACE_DB=/data/marketplace_mcp.sqlite3 MCP_WORKSPACE_DB=/data/workspace_mcp.sqlite3 \
    MARKETPLACE_MODE=gigsim MODEL_PROFILE=open APP_ENV=production \
    API_HOST=127.0.0.1 WEB_APP_URL=http://localhost:7860

EXPOSE 7860
CMD ["/app/deploy/start.sh"]
