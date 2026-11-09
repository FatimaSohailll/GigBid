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

## One thing the AI got wrong that I caught

The assignment requires this to be the student's own finding. To be completed by the student.

## Errors the AI found in its own work

- DevSim's first version used plain `def` routes. FastAPI runs those in a thread pool, so two
  simultaneous submits could both spend Connects. They were changed to `async def`.
