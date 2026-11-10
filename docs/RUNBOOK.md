# Runbook

## Running locally

Start the stores with `make db`, then start the API with `make api` and the worker with
`make worker`. On Windows the API and worker need a selector event loop for Postgres, so run them
in Docker or Linux. The packaging phase adds the full stack to `docker compose up`.

`make db` also starts Qdrant. The worker and Portfolio server are separate processes, so they need
`QDRANT_URL`; embedded Qdrant allows only one process. Set `MCP_TENANT_SIGNING_KEY` to the same
value for the worker and all three MCP servers. Each GigBid user gets a private copy of their
persona's portfolio on first use, so adding or deleting evidence affects only that user.

Tracing needs `LANGSMITH_API_KEY`. Emails, phone numbers and card numbers are masked before a trace
is sent. Set `tracing.enabled` to false in `agent/config.yaml` to send nothing.

## Schedules and rate limits

| Work | Schedule | Why |
|---|---|---|
| Saved-search check | Every minute, running only searches that are due | Cheap database query |
| Marketplace sync | Every minute per user | Keeps the pipeline board and client replies current |
| GigSim search | At most every 5 minutes | A simulator built for this load |
| Upwork search (live mode) | At most every 240 minutes | Upwork treats aggressive automation as a violation. New work is not time-critical enough to justify polling more often |

The Marketplace server also limits each user to 20 requests a minute (`agent/config.yaml`).

## Recovery

| Failure | What happens |
|---|---|
| Marketplace MCP or marketplace down | The run becomes `waiting_for_service`, `/api/status` reports unavailable, and the queued job retries until the service returns |
| Worker dies mid-run | Arq re-runs the job. The task reads the checkpoint first and continues from the last finished step |
| Worker dies after submitting | Stable idempotency keys make the repeated submit a harmless replay |
| API dies during an approval | The approval is read from the Postgres checkpoint, so nothing is lost |
| A node keeps failing | The thread moves to `dead_letter` with the error recorded |
| Step or token budget reached | The thread moves to `needs_attention` |
