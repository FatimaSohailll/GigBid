#!/bin/sh
# Boot GigBid in one container: the stores first, then the demo seed, then every service.
set -eu

echo "== GigBid: starting the stores"
PG_BIN=$(ls -d /usr/lib/postgresql/*/bin | sort -V | tail -1)
PGDATA="$DATA/postgres"
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  mkdir -p "$PGDATA"
  "$PG_BIN/initdb" -D "$PGDATA" -U gigbid --auth=trust >/dev/null
fi
"$PG_BIN/pg_ctl" -D "$PGDATA" -l "$DATA/postgres.log" -w \
  -o "-c listen_addresses=127.0.0.1 -k /run/postgresql" start
"$PG_BIN/psql" -h 127.0.0.1 -U gigbid -d postgres -tAc \
  "select 1 from pg_database where datname='gigbid'" | grep -q 1 \
  || "$PG_BIN/createdb" -h 127.0.0.1 -U gigbid gigbid

redis-server --bind 127.0.0.1 --port 6379 --daemonize yes --save "" --dir "$DATA"

mkdir -p "$DATA/qdrant"
(cd /opt/qdrant && QDRANT__STORAGE__STORAGE_PATH="$DATA/qdrant" QDRANT__SERVICE__HOST=127.0.0.1 \
  QDRANT__TELEMETRY_DISABLED=true ./qdrant >"$DATA/qdrant.log" 2>&1 &)
python - <<'PY'
import time, urllib.request
for _ in range(120):
    try:
        urllib.request.urlopen("http://127.0.0.1:6333/readyz", timeout=2)
        break
    except Exception:
        time.sleep(1)
else:
    raise SystemExit("Qdrant did not start; see $DATA/qdrant.log")
PY

# Signing keys only need to agree inside this container, so a missing one is generated per boot.
if [ -z "${MCP_TENANT_SIGNING_KEY:-}" ]; then
  MCP_TENANT_SIGNING_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
  export MCP_TENANT_SIGNING_KEY
fi
[ -n "${GROQ_API_KEY:-}" ] || echo "WARNING: GROQ_API_KEY is not set, so agent runs will fail."

echo "== GigBid: seeding the demo (DevSim data, portfolio index, reviewer login)"
python -m scripts.seed_demo

echo "== GigBid: starting the services; the web app listens on port 7860"
exec supervisord -c /app/deploy/supervisord.conf
