"""R&D question C: one ReAct agent, a prebuilt supervisor and the hand-built graph on the same jobs.

Usage: python -m evals.runners.orchestration_compare [--limit 50] [--designs graph,react,supervisor]

All designs use the same jobs, model settings, simulated-client seed, final-text claim check and
outgoing-text scanner, with fallback models switched off. Finished jobs are kept, so a run stopped
by a provider limit continues when run again. Not run yet: it needs about 150 job runs of quota.
"""

import argparse
import asyncio
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from dotenv import load_dotenv

from agent.config import AppConfig, get_config
from agent.llm import ModelRouter, create_chat_model
from agent.tracing import flush_traces
from evals.baselines import ROLES, ReactJobRunner, SupervisorJobRunner, with_baseline_tools
from evals.harness import (
    EvalStack,
    append_row,
    eval_config,
    load_gold,
    read_finished_rows,
    select_jobs,
    start_eval_stack,
)
from evals.job_runner import JobRunner
from evals.metrics import percentile, precision_recall_f1
from evals.runners.decision_eval import mean_cost
from evals.runners.rag_eval import RESULTS_DIR, write_result
from rag.retriever import open_components

DESIGNS = ("graph", "react", "supervisor")
# The baselines call models directly, so the client waits out per-minute limits (Retry-After).
BASELINE_RETRIES = 8


async def build_runner(
    design: str, persona: str, stack: EvalStack, config: AppConfig, seed_key: str
) -> JobRunner:
    llm = ModelRouter(config)
    if design == "graph":
        return stack.runner(persona, llm)
    runtime = stack.runtime(persona, llm)
    toolbox = stack.toolbox(persona)
    if design == "react":
        model = create_chat_model(config.model_for("single_agent")[0], BASELINE_RETRIES)
        tools = await toolbox.tools_for("react_baseline")
        return ReactJobRunner(runtime, tools, model, seed_key)
    models = {
        role: create_chat_model(config.model_for(role)[0], BASELINE_RETRIES) for role in ROLES
    }
    tools_by_role = {role: await toolbox.tools_for(role) for role in ROLES}
    lead = create_chat_model(config.model_for("single_agent")[0], BASELINE_RETRIES)
    return SupervisorJobRunner(runtime, tools_by_role, models, lead, seed_key)


async def score(
    runner: JobRunner, gold: dict[str, Any], stack: EvalStack, seed: int
) -> dict[str, Any]:
    result = await runner.complete(gold)
    row: dict[str, Any] = {
        **result.as_row(),
        "job_id": gold["job_id"],
        "persona": gold["persona"],
        "job_type": gold["job_type"],
        "gold_bid": gold["decision"] == "bid",
        "is_scam": gold["is_scam"],
        "submitted": result.status == "submitted",
        "replied": False,
    }
    if row["submitted"] and result.proposal_id:
        row["replied"] = stack.simulate_client(result.proposal_id, seed)["replied"]
    return row


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    done = [row for row in rows if not row["error"]]
    if not done:
        return {"jobs": 0, "failed": len(rows)}
    sent = [row for row in done if row["submitted"]]
    checked = [row for row in done if row.get("claims_after") is not None]
    latencies = [row["latency_seconds"] for row in done]
    return {
        "jobs": len(done),
        "failed": len(rows) - len(done),
        "bid_or_skip": precision_recall_f1((r["gold_bid"], r["decision"] == "bid") for r in done),
        "bid_or_skip_accuracy": round(
            mean(float(r["gold_bid"] == (r["decision"] == "bid")) for r in done), 4
        ),
        "unsupported_claims_per_proposal": (
            round(mean(r["unsupported_after"] for r in checked), 4) if checked else None
        ),
        "compliance_caught": sum(r.get("compliance_caught", 0) for r in done),
        "compliance_issues_in_output": sum(r.get("compliance_issues_in_output", 0) for r in done),
        "writes_attempted_by_agent": sum(len(r.get("writes_attempted", [])) for r in done),
        "reply_rate": round(mean(float(r["replied"]) for r in sent), 4) if sent else None,
        "tokens_per_job": round(mean(r["tokens"] for r in done), 1),
        "cost_per_job_usd": mean_cost(done),
        "latency_seconds": {
            "p50": round(percentile(latencies, 50), 2),
            "p95": round(percentile(latencies, 95), 2),
        },
    }


def report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_type[row["job_type"]].append(row)
    return {
        "overall": summarise(rows),
        "by_job_type": {key: summarise(value) for key, value in sorted(by_type.items())},
    }


async def run(arguments: argparse.Namespace) -> dict[str, Any]:
    load_dotenv()
    config = with_baseline_tools(eval_config(get_config()))
    designs = [d for d in arguments.designs.split(",") if d]
    unknown = set(designs) - set(DESIGNS)
    if unknown:
        raise SystemExit(f"Unknown designs: {sorted(unknown)}")
    components = open_components(config)
    jobs = select_jobs(load_gold(arguments.data), arguments.limit)
    results: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as workdir:
        stack = start_eval_stack(Path(workdir), components, config, arguments.data / "marketplace")
        try:
            for design in designs:
                rows_path = RESULTS_DIR / f"rqc_rows_{design}_{arguments.source}.jsonl"
                finished = read_finished_rows(rows_path)
                runners: dict[str, JobRunner] = {}
                rows: list[dict[str, Any]] = []
                failures = 0
                for gold in jobs:
                    key = f"{gold['persona']}:{gold['job_id']}"
                    if key not in finished:
                        persona = gold["persona"]
                        if persona not in runners:
                            runners[persona] = await build_runner(
                                design, persona, stack, config, str(arguments.seed)
                            )
                        finished[key] = await score(runners[persona], gold, stack, arguments.seed)
                        append_row(rows_path, finished[key])
                        failures = failures + 1 if finished[key]["error"] else 0
                        print(design, key, finished[key]["decision"], flush=True)
                    rows.append(finished[key])
                    if failures >= arguments.max_failures:
                        print("Stopping: repeated failures, probably a provider limit.", flush=True)
                        break
                results[design] = report(rows)
                write_result(f"rqc_{design}_{arguments.source}.json", results[design])
        finally:
            stack.stop()
    flush_traces(config)
    components.close()
    comparison = {
        "data_source": arguments.source,
        "official_gigsim": arguments.source == "gigsim",
        "seed": arguments.seed,
        "designs": {name: value["overall"] for name, value in results.items()},
    }
    write_result(f"rqc_comparison_{arguments.source}.json", comparison)
    return comparison


def parser() -> argparse.ArgumentParser:
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--limit", type=int, default=50)
    options.add_argument("--designs", default=",".join(DESIGNS))
    options.add_argument("--data", type=Path, default=Path("data/processed/devsim"))
    options.add_argument("--source", default="devsim")
    options.add_argument("--seed", type=int, default=42)
    options.add_argument("--max-failures", type=int, default=3)
    return options


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print(asyncio.run(run(parser().parse_args())))
