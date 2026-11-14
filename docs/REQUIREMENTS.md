# Requirement Tracker

The assignment PDF is the source of truth. Update this file in every step. Existing code does not
make a requirement complete.

| Status | Meaning |
|---|---|
| Missing | Nothing exists |
| Stub | Only a kit placeholder or TODO exists |
| Partial | Some of the requirement works |
| Stand-in | A self-built replacement for an instructor resource; must be labelled wherever used |
| Deferred | Explicitly postponed by the student; not complete and still required |
| In progress | Work has started and has no final result yet |
| Implemented | Code is complete but its tests are missing or incomplete |
| Tested | Automated tests pass |
| Verified | Demonstrated end to end in the conditions the assignment describes |

Roadmap: Stage 0 (shared prerequisites), then Parts A, B, C, D and E in order, each with its R&D question. Older "Phase" numbers below are superseded by that order. Steps completed so far: DevSim marketplace API, Step 0, DevSim data, contracts and Marketplace MCP, Portfolio MCP and RAG core, model routing and bid graph, conversation graph and memory, API and worker, web interface, RAG variants and evaluation runners (written, not yet measured), Stage 0 (run record, masked tracing, defence switch, job runner, per-user tenancy, Qdrant service).

Automated graph tests use scripted model replies, so that they are repeatable and free. The graph
has also been run by hand on real open models through Groq, on DevSim data. Part A's single-agent
proposal is built (`agent/single_agent.py`) and was run once on Groq for a DevSim job
(`evals/results/single_agent_proposal_devsim.json`).

## Instructor resources

| ID | Resource | Files | Status | Tests | Outstanding |
|---|---|---|---|---|---|
| RES-1 | GigSim REST marketplace | `gigsim/` absent; `devsim/` | Stand-in, Tested | `devsim/tests/` | Real bundle unavailable. Hidden test run needs the real GigSim |
| RES-2 | Simulated clients with hidden preferences | `devsim/src/devsim/client_sim.py` (seeded rule, not an LLM) | Stand-in, Tested | `devsim/tests/test_client_sim.py` | Real GigSim uses an LLM client; never report these rates as GigSim |
| RES-3 | Three persona corpora | `devsim/src/devsim/datagen/` (`corpus.py`, `catalog.yaml`) | Stand-in, Tested | `devsim/tests/test_datagen.py` | Real personas unavailable |
| RES-4 | Policy pack | `devsim/src/devsim/datagen/policy_pack.md` | Stand-in, Tested | `devsim/tests/test_datagen.py` | Real pack unavailable; kit copy is illustrative |
| RES-5 | About 1,500 historical jobs and live stream | `devsim/src/devsim/datagen/jobs.py`; `/v1/market/price-stats` | Stand-in, Tested | `devsim/tests/test_datagen.py` | Templated text is more regular than real posts |
| RES-6 | Dev evaluation set with gold labels | `devsim/src/devsim/datagen/jobs.py`, output in `data/processed/devsim/dev_set/` | Stand-in, Tested | `devsim/tests/test_datagen.py` | All results must be labelled DevSim |
| RES-7 | 2 November change request | None | Missing | None | Only a rehearsal can be self-written |

## System requirements

| ID | Requirement | Files | Status | Tests | Outstanding |
|---|---|---|---|---|---|
| R1 | One marketplace port, two adapters, switched by config | `agent/marketplace_port.py`, `mcp_servers/marketplace/adapters/gigsim.py` | Partial, GigSim adapter Tested | `mcp_servers/marketplace/tests/` | Upwork adapter deferred (A4) |
| R2 | Portfolio, Marketplace, Workspace, Upwork and one more MCP server | `mcp_servers/` | Partial, three own servers Tested | `mcp_servers/*/tests/`, `tests/integration/` | Upwork and GitHub deferred (A5) |
| R3 | Roles in a hand-written StateGraph with parallel analysis | `agent/graphs/`, `agent/nodes/` | Tested | `tests/integration/test_bid_graph.py`, `test_conversation_graph.py` | Real GigSim run |
| R4 | Human approval through LangGraph interrupts in the web UI | `agent/nodes/approval.py`, `ApprovalPanel` | Tested | `tests/integration/test_bid_graph.py`, `apps/web/tests/e2e/gigbid.spec.ts` | Real GigSim run |
| R5 | No unsupported claims; rate measured and reported | `rag/verify.py` | Partial, Tested | `tests/unit/test_claim_verification.py` | LLM judge not run on a real model yet; reporting in Phase 11 |
| R6 | Deployed RAG design chosen by evaluation | `rag/retriever.py`, `evals/runners/rag_eval.py`, `evals/results/rag_comparison_devsim.json`, `evals/results/ablation_devsim.json` | Partial, retrieval measured on DevSim | `tests/unit/test_part_b_measurement.py` | `rag.variant` set from retrieval results; compare V0 and the winner on proposals and reply rate |
| R7 | Memory updated only through an explicit promotion rule | `agent/memory/` | Tested | `tests/unit/test_memory.py` | Marketplace event feed, Phase 8; memory viewer UI, Phase 9 |
| R8 | Every run traced; p95 latency and cost reported | `agent/tracing.py`, `agent/usage.py`, `evals/runners/` | Partial, real trace checked | `tests/unit/test_stage_zero.py` | Report measured values, Parts B, C, E |
| R9 | Injection defence, PII masking, polite scheduling, no scraping | `agent/guardrails.py`, `agent/tracing.py`, `worker/` | Partial, Tested | `tests/unit/test_stage_zero.py`, `tests/unit/test_agent_units.py` | Measure attack success; mask application logs, Part E |
| R10 | `docker compose up` and a public URL with reviewer login | `infra/docker-compose.yml` empty | Stub | None | Phase 16 |

## Part A: MCP tool layer

| ID | Requirement | Files | Status | Tests | Outstanding |
|---|---|---|---|---|---|
| A1 | Portfolio tools, resource templates, prompt templates | `mcp_servers/portfolio/` | Tested | `mcp_servers/portfolio/tests/`, `tests/integration/test_portfolio_http.py` | Publishing, Phase 16 |
| A2 | Marketplace read and write tools, schemas, annotations, idempotency, `dry_run`, auth, rate limit, audit log | `mcp_servers/marketplace/` | Tested | `mcp_servers/marketplace/tests/`, `tests/integration/` | Tested on DevSim, not the real GigSim |
| A3 | Workspace calendar and email drafts | `mcp_servers/workspace/server.py` (local backend) | Tested | `mcp_servers/workspace/tests/` | None |
| A4 | Upwork adapter, OAuth, encrypted per-user tokens, drafts only | `agent/marketplace_port.py` (submission mode only) | Deferred, not complete | None | Adapter and OAuth; needs an Upwork OAuth client |
| A5 | Two third-party servers with trust rationale | `agent/mcp_client.py`, `docs/TOOLS.md` | Deferred, not complete | GitHub read and repository ingestion checked live; Upwork not connected | Stays incomplete until Upwork and GitHub are both verified; finish `docs/TOOLS.md` |
| A6 | Per-agent allowlists, timeouts, retries, degraded mode | `agent/mcp_client.py`, `worker/tasks.py`, `/api/status` | Tested | `tests/integration/test_api_worker.py`, `apps/web/tests/e2e/gigbid.spec.ts` | None |
| A7 | In-memory tests, Inspector screenshots, schema snapshot | `tests/contract/`, `mcp_servers/*/tests/` | Tested | In-memory client tests for all three servers; schema snapshots in `tests/contract/` for all three; `tests/contract/test_tool_rules.py`; Inspector screenshots in `docs/TOOLS.md` | None |
| RQ-A | Tool granularity study on 40 tasks | `evals/runners/tool_granularity.py`, `evals/results/tool_granularity.json`, `docs/RND_A_TOOL_GRANULARITY.md`, `mcp_servers/tool_descriptions.py` | Tested, measured on Qwen3 8B | `tests/unit/test_tool_study.py` | Include in the R&D report |

## Part B: Advanced RAG

| ID | Requirement | Files | Status | Tests | Outstanding |
|---|---|---|---|---|---|
| B1 | Ingestion with full metadata and incremental re-indexing | `rag/ingest/` (including `github.py`), `rag/index/`, `apps/api/routers/settings.py` | Partial, Tested | `tests/unit/test_rag_core.py`, `tests/unit/test_part_b_measurement.py`, `tests/integration/test_github_mcp.py` | Re-ingest so chunks carry persona; UI for evidence list and delete, Part D |
| B2 | V0 and at least four more variants covering required groups | `rag/variants/` (V0, V1, V2, V3, V4, V8), `agent/nodes/writer.py` | Tested | `tests/unit/test_rag_variants.py`, `tests/unit/test_part_b_measurement.py` | Full comparison |
| B3 | Claim verification, rate before and after | `rag/verify.py` (`recheck_final_text`), `agent/nodes/verifier.py`, `evals/job_runner.py` | Partial, Tested | `tests/unit/test_claim_verification.py`, `tests/unit/test_part_b_measurement.py` | Measure on real runs, against the final text |
| B4 | Retrieval, decision, quality, outcome, cost metrics; calibrated judge | `evals/runners/`, `evals/judge.py`, `agent/prompts/judge.v1.md` | Partial, retrieval measured; model sample in progress on DevSim | `tests/unit/test_eval_pipeline.py`, `tests/unit/test_part_b_measurement.py`, `tests/integration/test_eval_runners.py` | Finish the sample (`evals/runners/model_campaign.py`); student labels 30 items; classmate ratings (`evals/runners/human_panel.py`) |
| B5 | Results table regenerated by a script | `evals/results_table.py` | Partial, Tested | `tests/unit/test_eval_pipeline.py` | Complete numbers for both rows |
| RQ-B | Hypothesis, ablation, breakdown by persona and job type | `evals/runners/ablation.py`, `evals/results/ablation_devsim.json` | Partial, retrieval half measured on DevSim | `docs/RND_B_RETRIEVAL.md`, `tests/unit/test_part_b_measurement.py` | Reply-rate comparison |

## Part C: Multi-agent orchestration

| ID | Requirement | Files | Status | Tests | Outstanding |
|---|---|---|---|---|---|
| C1 | Bid and conversation graphs | `agent/graphs/` | Tested | `tests/integration/test_bid_graph.py`, `test_conversation_graph.py` | None |
| C2 | Typed state, reducers, trace and memory separation, two stores | `agent/state.py`, `agent/memory/store.py` | Tested | `tests/unit/test_contracts.py`, `test_memory.py` | Tracing tool, Phase 12 |
| C3 | Eight Pydantic hand-offs | `agent/handoffs.py` | Tested | `tests/unit/test_contracts.py` | Used by nodes from Phase 4 |
| C4 | Critic limit, Compliance block, step and token budgets | `agent/graphs/bid_graph.py`, `agent/nodes/` | Tested | `tests/integration/test_bid_graph.py` | Show in UI, Phase 9 |
| C5 | Interrupts with full payload; approve, edit, reject | `agent/nodes/approval.py`, `agent/nodes/critic.py`, `apps/api/routers/proposals.py`, `apps/web/src/app/studio/[id]` | Tested; deleted evidence is caught by the Critic and dropped on regeneration | `tests/integration/test_bid_graph.py`, `apps/web/tests/e2e/gigbid.spec.ts` | None |
| C6 | Postgres checkpointer, crash and resume, replay | `agent/checkpoint.py`, `infra/docker-compose.yml` | Tested | `tests/integration/test_crash_recovery.py` kills real processes | Demo from the UI, Phase 9 |
| C7 | Retries, timeouts, fallback model, dead-letter state | `agent/graphs/bid_graph.py`, `agent/llm.py` | Tested | `tests/integration/test_bid_graph.py`, `tests/unit/test_agent_units.py` | Fallback is now `llama-3.1-8b-instant` on Groq and is switched off in evaluations; not yet triggered against the live provider |
| RQ-C | ReAct, supervisor and graph comparison on 50 jobs | `evals/baselines.py`, `evals/runners/orchestration_compare.py` | Implemented, not run by the student's choice | `tests/unit/test_part_c_baselines.py` | Run on 50 jobs; needs about 150 job runs of model quota |

## Part D: Web interface

| ID | Requirement | Files | Status | Tests | Outstanding |
|---|---|---|---|---|---|
| D1 | Sign-in and onboarding | `apps/web/src/app/login`, `onboarding` | Partial, Tested | `apps/web/tests/e2e/gigbid.spec.ts` | Own profile and Upwork OAuth, Phase 13 |
| D2 | Job inbox | `apps/web/src/app/inbox` | Tested | `apps/web/tests/e2e/gigbid.spec.ts` | None |
| D3 | Job detail with evidence matrix | `apps/web/src/app/jobs/[id]` | Implemented | No browser test yet | Testing phase |
| D4 | Proposal studio | `apps/web/src/app/studio/[id]`, `EvidenceHover`, `ApprovalPanel` | Tested | `apps/web/tests/e2e/gigbid.spec.ts` | None |
| D5 | Pipeline board | `apps/web/src/app/pipeline` | Tested | `apps/web/tests/e2e/gigbid.spec.ts` | None |
| D6 | Messages | `apps/web/src/app/messages` | Implemented | Backend tested; no browser test yet | Testing phase |
| D7 | Agent run viewer | `apps/web/src/app/runs`, `RunTimeline` | Partial, Tested | `apps/web/tests/e2e/gigbid.spec.ts` | Link to the full trace, Phase 12 |
| D8 | Analytics | `apps/web/src/app/analytics` | Tested | `apps/web/tests/e2e/gigbid.spec.ts` | Cost in USD, Phase 12 |
| D9 | Settings and memory viewer | `apps/web/src/app/settings` | Partial, Tested | `apps/web/tests/e2e/gigbid.spec.ts` | Deleting memory from the UI has no browser test yet |
| D10 | Streaming; approvals survive refresh | `apps/api/streaming.py`, `RunTimeline`, `apps/api/routers/approvals.py` | Tested | `apps/web/tests/e2e/gigbid.spec.ts`, `tests/integration/test_api_worker.py` | None |
| D11 | TypeScript client generated from OpenAPI | `apps/web/src/lib/api/schema.d.ts`, `make openapi` | Tested | CI compares it with the API | None |
| D12 | Phone layout, keyboard access, empty, loading and error states | `apps/web/src/app/globals.css`, `States.tsx` | Tested | `apps/web/tests/e2e/gigbid.spec.ts` | Wider accessibility check in testing phase |
| D13 | Five or more Playwright tests | `apps/web/tests/e2e/gigbid.spec.ts` (6 tests) | Tested | Passing locally | Confirm on GitHub Actions |

## Part E: Production engineering

| ID | Requirement | Files | Status | Tests | Outstanding |
|---|---|---|---|---|---|
| E1 | Redis worker, schedules, idempotent jobs | `worker/`, `docs/RUNBOOK.md` | Tested | `tests/integration/test_api_worker.py` | Cron schedule not run live; real worker kill in testing phase |
| E2 | `make eval`; CI runs tests and a 30-job gated smoke eval | `Makefile`, `evals/runners/smoke.py`, `evals/thresholds.yaml` | Partial, not run | `tests/unit/test_eval_pipeline.py` | `eval.yml` needs the Groq secret and measured thresholds, Part E |
| E3 | One trace id across UI, API, graph, MCP and audit log; tokens and cost per node | `agent/tracing.py`, `agent/usage.py`, `agent/graphs/common.py`, `agent/mcp_client.py`, Marketplace server | Partial, real LangSmith trace checked | `tests/unit/test_stage_zero.py` | Trace link in the UI and API trace id across resumes, Part D |
| E4 | Delimited untrusted data, attack rate, masking, encrypted tokens, tenancy | `agent/guardrails.py`, `apps/api/auth/security.py`, `mcp_servers/tenant_auth.py`, `rag/ingest/pipeline.py` | Partial, Tested | `tests/unit/test_stage_zero.py`, `tests/integration/test_qdrant_service.py` | Attack rate and application log masking, Part E; audit log names the shared persona account |
| E5 | Declared and measured budgets; one optimisation | `evals/job_runner.py`, `agent/usage.py` record cost and latency | Partial | `tests/unit/test_stage_zero.py` | Declare budgets, measure, one optimisation, Part E |
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
| S8 | CI rule: every reported number traces to `evals/results/` | `scripts/ci/guards.py` (`numbers`) | Tested | `tests/unit/test_eval_pipeline.py` | Add the report when it exists |
| S9 | `ci.yml`: lint, unit, contract, Playwright | `.github/workflows/ci.yml` | Implemented | Playwright step not yet run on GitHub | Confirm on GitHub Actions |
| S10 | `eval.yml`: smoke eval with thresholds | Drafted locally, not committed | Partial | None | Enable in Part E, once the Groq secret and measured thresholds exist |
| S11 | Evaluation results are committed | `.gitignore` fixed | Implemented | None | Results arrive in Phase 11 |

## Deliverables

| ID | Requirement | Status | Outstanding |
|---|---|---|---|
| P1 | README with GIF, diagram, results tables, trace links | Stub | Phase 17 |
| P2 | Public live URL with reviewer login | Missing | Phase 16 |
| P3 | 3-minute demo video and UI screen recording | Missing | Student, after Phase 16 |
| P4 | Portfolio MCP server published with its own README | Tested: image built and checked over stdio (`infra/docker/portfolio.Dockerfile`, `mcp_servers/portfolio/README.md`, `local.py`) | Student publishes it (for example to GHCR) and links it |
| P5 | Technical write-up, 1,200 to 1,800 words | Missing | Student |
| P6 | Two-line résumé bullet from results files | Missing | Phase 17 |
| P7 | Signature extension in `docs/EXTENSION.md` | Stub | Phase 15 |
| P8 | R&D report, 10 sections, failure taxonomy | Missing | Phase 17 |
| P9 | `docs/ARCHITECTURE.md`, `docs/adr/`, `docs/DATA_CARD.md`, `docs/TOOLS.md`, `docs/RUNBOOK.md` | Stub | In their phases |
| P10 | `docs/AI_USAGE.md` | Partial | Student adds one AI mistake they caught; update each step |
| P11 | Trace links: skipped scam, approved proposal, Critic loop, crash and resume | Missing | Phases 5, 12 |
