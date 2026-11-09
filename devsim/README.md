# DevSim: a development stand-in for GigSim

**This is not the instructor-provided GigSim.** It was written for this project because the
GigSim bundle was not available. It lets GigBid be built and tested, but anything measured against
it is a DevSim result and must be labelled that way. It cannot stand in for the hidden test run.

Every response carries `X-Simulator: gigbid-devsim`, and `/health` reports `official_gigsim: false`.

The API is modelled on the assignment's description of GigSim and the kit's sample data. The real
GigSim API is unknown, so only the GigBid adapter should depend on these routes.

## Run

From this folder, in PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
$env:DEVSIM_API_TOKENS = "dev-token-ai=ai_ml_engineer"
$env:DEVSIM_ADMIN_TOKEN = "dev-admin"
.venv\Scripts\python -m devsim
```

The API listens on `http://127.0.0.1:8081`, with interactive docs at `/docs`. The example tokens
are for local use only.

## Behaviour

| Feature | How it works |
|---|---|
| Authentication | Bearer token per freelancer, read from `DEVSIM_API_TOKENS` |
| Rate limits | Rolling 60-second window per freelancer; `429` with `Retry-After` |
| Failures | Seeded `503`s, either before a request applies or after a write has committed |
| Idempotency | Writes need `Idempotency-Key`; a retry replays the first successful response |
| Connects | Charged only by submit, and never twice for one proposal or job |
| Job stream | Jobs stay hidden until simulated time reaches `posted_at` |
| Events | `/v1/events` is a per-freelancer feed for the pipeline board |
| Admin routes | Reset, advance time, change fault rates, move proposals, add client replies |

Client preferences stay server-side and are never returned by the API. Gold labels do not belong
in the seed, and unknown seed fields are rejected. State is in memory and resets on restart.

Settings are in `src/devsim/config.default.yaml`; set `DEVSIM_CONFIG` to use another file.

## Not built yet

The larger job stream, persona corpora, dev evaluation set, LLM-driven simulated clients, policy
pack and rehearsal change request are later steps.
