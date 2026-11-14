# AI Usage Disclosure

The student remains accountable for every line in this repository.

## Tools and files

| Tool | Model | Files or tasks | Reviewed and run by the student |
|---|---|---|---|
| Claude Code (desktop app) | Claude Opus 5.5 | Assignment assessment and roadmap | Student to confirm |
| Claude Code (desktop app) | Claude Opus 5.5 | `devsim/`: stand-in marketplace simulator, synthetic data generator, personas, policy pack and tests | Student to confirm |
| Claude Code (desktop app) | Claude Opus 5.5 | Step 0: `pyproject.toml`, `uv.lock`, `Makefile`, `.gitignore`, `.env.example`, `.github/workflows/ci.yml`, `scripts/ci/guards.py`, `tests/unit/test_ci_guards.py`, `docs/REQUIREMENTS.md` | Pending |
| Claude Code (desktop app) | Claude Opus 5.5 | `agent/` contracts, config and MCP client; `mcp_servers/marketplace/`; tests; `docs/TOOLS.md` | Student to confirm |
| Claude Code (desktop app) | Claude Opus 5.5 | `rag/`, `mcp_servers/portfolio/`, `evals/`, prompts and tests | Student to confirm |
| Claude Code (desktop app) | Claude Opus 5.5 | `agent/llm.py`, `agent/guardrails.py`, `agent/checkpoint.py`, `agent/nodes/`, `agent/graphs/bid_graph.py`, prompts and tests | Student to confirm |
| Claude Code (desktop app) | Claude Opus 5.5 | `mcp_servers/workspace/`, `agent/memory/`, `agent/graphs/conversation_graph.py`, `agent/nodes/client_liaison.py`, tests | Student to confirm |
| Claude Code (desktop app) | Claude Opus 5.5 | `apps/api/`, `worker/`, `docs/RUNBOOK.md`, tests | Student to confirm |
| Claude Code (desktop app) | Claude Opus 5.5 | `apps/web/`, paragraph regeneration in `apps/api/`, `tests/support/e2e_backend.py` | Student to confirm |
| Claude Code (desktop app) | Claude Opus 5.5 | `rag/variants/`, `rag/index/bm25.py`, `rag/index/reranker.py`, `evals/`, `devsim/src/devsim/client_sim.py`, tests | Student to confirm |
| Claude Code (desktop app) | Claude Opus 5.5 | Stage 0: `agent/usage.py`, `agent/tracing.py`, `evals/job_runner.py`, per-user tenancy, Qdrant service, tests | Student to confirm |
| Claude Code (desktop app) | Claude Opus 5.5 | Part A completion: MCP contract tests, Inspector screenshots, the live Upwork adapter (`mcp_servers/marketplace/adapters/upwork_live.py`, `agent/upwork_oauth.py`, `apps/api/routers/upwork.py`), `scripts/upwork_live_check.py` | Student to confirm |
| Claude Code (desktop app) | Claude Opus 5.5 | Part C: config-driven policy rules, replay, ReAct and supervisor baselines, failure taxonomy, model profiles; `docs/CHANGE_REQUEST.md`, `docs/RND_C_ORCHESTRATION.md` | Student to confirm |
| Claude Code (desktop app) | Claude Opus 5.5 | Part D: run viewer trace link, cost and replay, portfolio manager, own-profile onboarding, Playwright tests | Student to confirm |
| Claude Code (desktop app) | Claude Opus 5.5 | Part E: log masking, trace continuity, budgets, smoke eval workflow, Docker stack, deployment (`infra/`, `deploy/`, `docs/DEPLOYMENT.md`) | Student to confirm |
| Claude Code (desktop app) | Claude Opus 5.5 | Extension X3 (`agent/case_builder.py`, `evals/runners/case_builder_eval.py`), blind judge labelling, README, `docs/ARCHITECTURE.md`, `docs/adr/`, `docs/EXTENSION.md` | Student to confirm |

## One thing the AI got wrong that I caught

The assignment requires this to be the student's own finding. To be completed by the student.

## Errors the AI found in its own work

- DevSim's first version used plain `def` routes. FastAPI runs those in a thread pool, so two
  simultaneous submits could both spend Connects. They were changed to `async def`.
- Hybrid retrieval first gave BM25 and vector chunks different ids, so fusion never combined them.
  A review found it before any result was reported as final, and a regression test now covers it.
- Live testing showed the Upwork adapter read message rooms from the wrong level of the response
  (Upwork nests them under `data.rooms`) and treated Upwork's retryable errors as permanent. Both
  were fixed with tests built on the shapes the live server returned.
- The first judge-calibration export was not blind: its item ids named the variant. They are now
  anonymous, and scoring reuses the judge scores the run saved.
- The first `open_small` profile also moved the judge to the smaller model, which would have scored
  the two configurations with different judges. The judge is now the same in both, and a test
  keeps it so.
- The Docker image left DevSim uninstalled (it is in the dev dependency group), and `.env`
  overrode the container's data path. Both showed up when the stack first ran and were fixed.
- During the work the assistant twice stated a fact without checking it (which variant two new
  proposals came from, and a bug in the smoke gate that was not there). Both were corrected from
  the result files and the code before anything relied on them.
