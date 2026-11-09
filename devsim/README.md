# DevSim: a development stand-in for GigSim

**This is not the instructor-provided GigSim.** It was written for this project because the
GigSim bundle was not available. It lets GigBid be built and tested, but anything measured against
it is a DevSim result and must be labelled that way. It cannot stand in for the hidden test run.

Every response carries `X-Simulator: gigbid-devsim`, and `/health` reports `official_gigsim: false`.

The API is modelled on the assignment's description of GigSim and the kit's sample data. The real
GigSim API is unknown, so only the GigBid adapter should depend on these routes.

## Run

From the repository root, in PowerShell:

```powershell
make install
make test
$env:DEVSIM_API_TOKENS = "dev-token-ai=ai_ml_engineer"
$env:DEVSIM_ADMIN_TOKEN = "dev-admin"
uv run --locked python -m devsim
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

## Generated data

`make seed` writes everything to `data/processed/devsim/` (git-ignored). The same seed always
produces byte-identical files, and `MANIFEST.json` records their hashes. To serve this data, also
set `DEVSIM_SEED_DIR=data/processed/devsim/marketplace` before starting the simulator.

| Output | Contents |
|---|---|
| `marketplace/` | 750 jobs, 450 clients, 1,500 closed jobs for price statistics, 3 freelancers |
| `personas/<id>/` | PDF CV, 16 case studies, 16 repositories with code, reviews, 150 labelled proposals, `index.json` metadata |
| `dev_set/<id>.jsonl` | 200 gold-labelled jobs per persona: decision, scam, injection, evidence, fair price band |
| `stream_gold.jsonl` | Labels for the 150 live jobs, including which are duplicates |
| `policy_pack/` | Stand-in marketplace rules with stable ids |

Gold labels are stored apart from the marketplace files, so the API cannot expose them. A job's
gold decision follows one rule: bid only if it is not a scam, at least 60% of its requirements have
portfolio evidence, and its budget meets the persona's minimum.

Limitation: job posts and corpus text come from templates and a hand-written catalog. They are
more regular than real marketplace text, so scores measured here are likely to be optimistic.

## Not built yet

LLM-driven simulated clients and the rehearsal change request are later steps.
