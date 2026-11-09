# Requirement Tracker

The assignment PDF is the source of truth. Update this file in every step. Existing code does not
make a requirement complete.

| Status | Meaning |
|---|---|
| Missing | Nothing exists |
| Stub | Only a kit placeholder or TODO exists |
| Partial | Some of the requirement works |
| Stand-in | A self-built replacement for an instructor resource; must be labelled wherever used |
| Implemented | Code is complete but its tests are missing or incomplete |
| Tested | Automated tests pass |
| Verified | Demonstrated end to end in the conditions the assignment describes |

Phases refer to the agreed roadmap. Steps completed so far: DevSim marketplace API, Step 0, DevSim data, contracts and Marketplace MCP.

## Instructor resources

| ID | Resource | Files | Status | Tests | Outstanding |
|---|---|---|---|---|---|
| RES-1 | GigSim REST marketplace | `gigsim/` absent; `devsim/` | Stand-in, Tested | `devsim/tests/` | Real bundle unavailable. Hidden test run needs the real GigSim |
| RES-2 | Simulated clients with hidden preferences | `devsim/` has preferences only | Missing | None | DevSim step: LLM reply, interview and hire decisions |
| RES-3 | Three persona corpora | `devsim/src/devsim/datagen/` (`corpus.py`, `catalog.yaml`) | Stand-in, Tested | `devsim/tests/test_datagen.py` | Real personas unavailable |
| RES-4 | Policy pack | `devsim/src/devsim/datagen/policy_pack.md` | Stand-in, Tested | `devsim/tests/test_datagen.py` | Real pack unavailable; kit copy is illustrative |
| RES-5 | About 1,500 historical jobs and live stream | `devsim/src/devsim/datagen/jobs.py`; `/v1/market/price-stats` | Stand-in, Tested | `devsim/tests/test_datagen.py` | Templated text is more regular than real posts |
| RES-6 | Dev evaluation set with gold labels | `devsim/src/devsim/datagen/jobs.py`, output in `data/processed/devsim/dev_set/` | Stand-in, Tested | `devsim/tests/test_datagen.py` | All results must be labelled DevSim |
| RES-7 | 2 November change request | None | Missing | None | Only a rehearsal can be self-written |

## System requirements

| ID | Requirement | Files | Status | Tests | Outstanding |
|---|---|---|---|---|---|
| R1 | One marketplace port, two adapters, switched by config | `agent/marketplace_port.py`, `mcp_servers/marketplace/adapters/gigsim.py` | Partial, GigSim adapter Tested | `mcp_servers/marketplace/tests/` | Upwork adapter, Phase 13 |
| R2 | Portfolio, Marketplace, Workspace, Upwork and one more MCP server | `mcp_servers/marketplace/` | Partial, Marketplace Tested | `mcp_servers/marketplace/tests/`, `tests/integration/` | Phases 3, 6, 13 |
| R3 | Roles in a hand-written StateGraph with parallel analysis | `agent/graphs/`, `agent/nodes/` | Stub | None | Phase 5 |
| R4 | Human approval through LangGraph interrupts in the web UI | None | Missing | None | Phases 5, 9 |
| R5 | No unsupported claims; rate measured and reported | `rag/verify.py` | Stub | None | Phases 3, 11 |
| R6 | Deployed RAG design chosen by evaluation | `rag/variants/v0_naive.py` | Stub | None | Phases 10, 11, 14 |
| R7 | Memory updated only through an explicit promotion rule | `agent/memory/promotion_rules.py` | Stub | None | Phase 6 |
| R8 | Every run traced; p95 latency and cost reported | `agent/config.yaml` has budgets | Stub | None | Phases 2, 12 |
| R9 | Injection defence, PII masking, polite scheduling, no scraping | Policy text only | Missing | None | Phases 8, 12 |
| R10 | `docker compose up` and a public URL with reviewer login | `infra/docker-compose.yml` empty | Stub | None | Phase 16 |

## Part A: MCP tool layer

| ID | Requirement | Files | Status | Tests | Outstanding |
|---|---|---|---|---|---|
| A1 | Portfolio tools, resource templates, prompt templates | `mcp_servers/portfolio/server.py` | Stub | None | Phase 3 |
| A2 | Marketplace read and write tools, schemas, annotations, idempotency, `dry_run`, auth, rate limit, audit log | `mcp_servers/marketplace/` | Tested | `mcp_servers/marketplace/tests/`, `tests/integration/` | Tested on DevSim, not the real GigSim |
| A3 | Workspace calendar and email drafts | `mcp_servers/workspace/server.py` | Stub | None | Phase 6 |
| A4 | Upwork adapter, OAuth, encrypted per-user tokens, drafts only | None | Missing | None | Phase 13; needs an Upwork account |
| A5 | Two third-party servers with trust rationale | `docs/TOOLS.md` is TODO | Missing | None | Phase 13 |
| A6 | Per-agent allowlists, timeouts, retries, degraded mode | `agent/mcp_client.py`, `agent/config.yaml` | Partial, client side Tested | `tests/integration/test_marketplace_http.py` | Queue and UI banner, Phases 8, 9 |
| A7 | In-memory tests, Inspector screenshots, schema snapshot | `tests/contract/` | Partial, Marketplace Tested | `mcp_servers/marketplace/tests/`, `tests/contract/` | Other servers; screenshots by student |
| RQ-A | Tool granularity study on 40 tasks | None | Missing | None | Phase 14 |

## Part B: Advanced RAG

| ID | Requirement | Files | Status | Tests | Outstanding |
|---|---|---|---|---|---|
| B1 | Ingestion with full metadata and incremental re-indexing | `rag/ingest/` is TODO | Missing | None | Phase 3 |
| B2 | V0 and at least four more variants covering required groups | `rag/variants/v0_naive.py` | Stub | None | Phases 3, 10 |
| B3 | Claim verification, rate before and after | `rag/verify.py` | Stub | None | Phases 3, 11 |
| B4 | Retrieval, decision, quality, outcome, cost metrics; calibrated judge | None | Missing | None | Phase 11; student labels 30 items; classmate panel |
| B5 | Results table regenerated by a script | None | Missing | None | Phases 11, 14 |
| RQ-B | Hypothesis, ablation, breakdown by persona and job type | None | Missing | None | Phase 14 |

## Part C: Multi-agent orchestration

| ID | Requirement | Files | Status | Tests | Outstanding |
|---|---|---|---|---|---|
| C1 | Bid and conversation graphs | `agent/graphs/` | Stub | None | Phases 5, 6 |
| C2 | Typed state, reducers, trace and memory separation, two stores | `agent/state.py` | Partial, state and reducers Tested | `tests/unit/test_contracts.py` | Memory stores, Phase 6 |
| C3 | Eight Pydantic hand-offs | `agent/handoffs.py` | Tested | `tests/unit/test_contracts.py` | Used by nodes from Phase 4 |
| C4 | Critic limit, Compliance block, step and token budgets | `agent/config.yaml` has values | Stub | None | Phase 5 |
| C5 | Interrupts with full payload; approve, edit, reject | None | Missing | None | Phase 5 |
| C6 | Postgres checkpointer, crash and resume, replay | None | Missing | None | Phase 5 |
| C7 | Retries, timeouts, fallback model, dead-letter state | None | Missing | None | Phase 5 |
| RQ-C | ReAct, supervisor and graph comparison on 50 jobs | None | Missing | None | Phase 14 |

## Part D: Web interface

| ID | Requirement | Files | Status | Tests | Outstanding |
|---|---|---|---|---|---|
| D1 | Sign-in and onboarding | `apps/web/` empty | Missing | None | Phases 7, 9 |
| D2 | Job inbox | `apps/web/` empty | Missing | None | Phase 9 |
| D3 | Job detail with evidence matrix | `apps/web/` empty | Missing | None | Phase 9 |
| D4 | Proposal studio | `apps/web/` empty | Missing | None | Phase 9 |
| D5 | Pipeline board | `apps/web/` empty | Missing | None | Phase 9 |
| D6 | Messages | `apps/web/` empty | Missing | None | Phase 9 |
| D7 | Agent run viewer | `apps/web/` empty | Missing | None | Phase 9 |
| D8 | Analytics | `apps/web/` empty | Missing | None | Phase 9 |
| D9 | Settings and memory viewer | `apps/web/` empty | Missing | None | Phase 9 |
| D10 | Streaming; approvals survive refresh | None | Missing | None | Phases 7, 9 |
| D11 | TypeScript client generated from OpenAPI | None | Missing | None | Phases 7, 9 |
| D12 | Phone layout, keyboard access, empty, loading and error states | None | Missing | None | Phase 9 |
| D13 | Five or more Playwright tests | `apps/web/tests/e2e/` empty | Missing | None | Phase 9 |

## Part E: Production engineering

| ID | Requirement | Files | Status | Tests | Outstanding |
|---|---|---|---|---|---|
| E1 | Redis worker, schedules, idempotent jobs | `worker/` is TODO | Missing | None | Phase 8 |
| E2 | `make eval`; CI runs tests and a 30-job gated smoke eval | `Makefile` fails clearly; `eval.yml` absent | Missing | None | Phases 9, 11 |
| E3 | One trace id across UI, API, graph, MCP and audit log | `agent/mcp_client.py`, Marketplace server and adapter | Partial, MCP to audit log Tested | `tests/integration/test_marketplace_http.py` | UI, API, graph and tracing tool, Phases 5, 7, 12 |
| E4 | Delimited untrusted data, attack rate, masking, encrypted tokens, tenancy | None | Missing | None | Phases 7, 12, 13 |
| E5 | Declared and measured budgets; one optimisation | None | Missing | None | Phases 12, 14 |
| E6 | Full Docker stack and stable public deployment | `infra/` empty | Missing | None | Phase 16 |

## Repository rules and structure

| ID | Requirement | Files | Status | Tests | Outstanding |
|---|---|---|---|---|---|
| S1 | Required directory layout | All required folders except `gigsim/`, `data/personas/` | Partial | None | Required files arrive in their phases |
| S2 | Pinned Python dependencies | `pyproject.toml`, `uv.lock` | Tested | Clean-checkout install | None |
| S3 | `make dev`, `test`, `eval`, `seed`, `demo`, `lint` | `Makefile` | Partial | `test`, `lint`, `seed` run | `dev`, `eval`, `demo` fail on purpose until built |
| S4 | `.env.example` lists every variable, no real values | `.env.example` | Partial | `tests/unit/test_ci_guards.py` | Extend as services are added |
| S5 | CI rule: no secrets | `scripts/ci/guards.py` | Tested | `tests/unit/test_ci_guards.py` | Confirm on a GitHub Actions run |
| S6 | CI rule: `gigsim/` unchanged | `scripts/ci/guards.py` | Tested | `tests/unit/test_ci_guards.py` | Confirm on GitHub; record a manifest if the real bundle arrives |
| S7 | CI rule: every prompt in `agent/prompts/` | `scripts/ci/guards.py` | Tested | `tests/unit/test_ci_guards.py` | Confirm on GitHub. Decide how the published Portfolio server ships its prompts (Phase 3) |
| S8 | CI rule: every reported number traces to `evals/results/` | None | Missing | None | Phase 11 |
| S9 | `ci.yml`: lint, unit, contract, Playwright | `.github/workflows/ci.yml` | Partial | Runs through `make test` | Playwright, Phase 9 |
| S10 | `eval.yml`: smoke eval with thresholds | None | Missing | None | Phase 11 |
| S11 | Evaluation results are committed | `.gitignore` fixed | Implemented | None | Results arrive in Phase 11 |

## Deliverables

| ID | Requirement | Status | Outstanding |
|---|---|---|---|
| P1 | README with GIF, diagram, results tables, trace links | Stub | Phase 17 |
| P2 | Public live URL with reviewer login | Missing | Phase 16 |
| P3 | 3-minute demo video and UI screen recording | Missing | Student, after Phase 16 |
| P4 | Portfolio MCP server published with its own README | Missing | Phase 16 |
| P5 | Technical write-up, 1,200 to 1,800 words | Missing | Student |
| P6 | Two-line résumé bullet from results files | Missing | Phase 17 |
| P7 | Signature extension in `docs/EXTENSION.md` | Stub | Phase 15 |
| P8 | R&D report, 10 sections, failure taxonomy | Missing | Phase 17 |
| P9 | `docs/ARCHITECTURE.md`, `docs/adr/`, `docs/DATA_CARD.md`, `docs/TOOLS.md`, `docs/RUNBOOK.md` | Stub | In their phases |
| P10 | `docs/AI_USAGE.md` | Partial | Student adds one AI mistake they caught; update each step |
| P11 | Trace links: skipped scam, approved proposal, Critic loop, crash and resume | Missing | Phases 5, 12 |
