"""Simulated reply and hire rates on a fixed job set: python -m evals.runners.gigsim_outcomes

Each job runs through the whole graph, with approval given automatically, and the simulator's
client then reacts. On DevSim the client is a labelled stand-in, so never report these as GigSim.
"""

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from statistics import mean
from typing import Any

from dotenv import load_dotenv
from langgraph.types import Command

from agent.config import get_config
from agent.llm import LlmGateway, ModelRouter
from agent.state import ThreadStatus
from evals.harness import (
    EvalStack,
    append_row,
    eval_config,
    initial_state,
    load_gold,
    read_finished_rows,
    rewrite_rows,
    select_jobs,
    start_eval_stack,
    thread_config,
)
from evals.judge import score_proposal
from evals.metrics import percentile
from evals.runners.rag_eval import RESULTS_DIR, safe_name, write_result
from rag.retriever import open_components

_FAILED = {ThreadStatus.NEEDS_ATTENTION.value, ThreadStatus.DEAD_LETTER.value}


async def run_job(
    graph: Any,
    gold: dict[str, Any],
    stack: EvalStack,
    seed: int,
    judge: LlmGateway | None,
) -> dict[str, Any]:
    thread = thread_config(gold)
    started = time.perf_counter()
    state = await graph.ainvoke(initial_state(gold), thread)
    if "__interrupt__" in state:
        state = await graph.ainvoke(Command(resume={"action": "approve"}), thread)
    latency = round(time.perf_counter() - started, 2)

    status = ThreadStatus(state["status"]).value
    reports = state.get("verification_reports", [])
    drafts = state.get("drafts", [])
    row: dict[str, Any] = {
        "job_id": gold["job_id"],
        "persona": gold["persona"],
        "job_type": gold["job_type"],
        "status": status,
        "submitted": status == ThreadStatus.SUBMITTED.value,
        "replied": False,
        "hired": False,
        "unsupported_before": reports[-1]["unsupported_before"] if reports else None,
        "unsupported_after": reports[-1]["unsupported_after"] if reports else None,
        "cover_letter": drafts[-1].cover_letter if drafts else None,
        "judge_overall": None,
        "tokens": state.get("tokens_used", 0),
        "latency_seconds": latency,
        "error": (state.get("attention_reason") or status) if status in _FAILED else None,
    }
    if row["submitted"]:
        outcome = stack.simulate_client(state["proposal_id"], seed)
        row.update(replied=outcome["replied"], hired=outcome["hired"])
        if judge is not None:
            score = await score_proposal(judge, state["job"], row["cover_letter"])
            row["judge_overall"] = score.overall
    return row


def _mean(values: list[float]) -> float | None:
    return round(mean(values), 4) if values else None


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    done = [row for row in rows if not row["error"]]
    sent = [row for row in done if row["submitted"]]
    drafted = [row for row in done if row["unsupported_after"] is not None]
    latencies = [row["latency_seconds"] for row in sent]
    stopped: dict[str, int] = {}
    for row in done:
        if not row["submitted"]:
            stopped[row["status"]] = stopped.get(row["status"], 0) + 1
    return {
        "jobs": len(done),
        "failed": len(rows) - len(done),
        "submitted": len(sent),
        "stopped_before_submission": stopped,
        "reply_rate": _mean([float(row["replied"]) for row in sent]),
        "hire_rate": _mean([float(row["hired"]) for row in sent]),
        "reply_rate_over_all_jobs": _mean([float(row["replied"]) for row in done]),
        "unsupported_claims_before": _mean([row["unsupported_before"] for row in drafted]),
        "unsupported_claims_after": _mean([row["unsupported_after"] for row in drafted]),
        "judge_score": _mean(
            [row["judge_overall"] for row in sent if row["judge_overall"] is not None]
        ),
        "tokens_per_proposal": _mean([row["tokens"] for row in sent]),
        "p50_latency_seconds": round(percentile(latencies, 50), 2) if latencies else None,
        "p95_latency_seconds": round(percentile(latencies, 95), 2) if latencies else None,
    }


async def run(arguments: argparse.Namespace) -> dict[str, Any]:
    load_dotenv()
    config = eval_config(get_config())
    variant = arguments.variant or config.rag.variant
    components = open_components(config)
    name = f"{safe_name(variant)}_{arguments.source}"
    tag = f"_{arguments.tag}" if arguments.tag else ""
    rows_path = RESULTS_DIR / f"outcomes_rows_{name}{tag}.jsonl"
    finished = {} if arguments.fresh else read_finished_rows(rows_path)
    rewrite_rows(rows_path, list(finished.values()))
    bids = [row for row in load_gold(arguments.data) if row["decision"] == "bid"]
    jobs = select_jobs(bids, arguments.limit)
    llm = ModelRouter(config)
    judge = llm if arguments.judge else None
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as workdir:
        stack = start_eval_stack(
            Path(workdir), components, config, arguments.data / "marketplace", variant
        )
        graphs: dict[str, Any] = {}
        rows: list[dict[str, Any]] = []
        for gold in jobs:
            key = f"{gold['persona']}:{gold['job_id']}"
            if key not in finished:
                graph = graphs.setdefault(gold["persona"], stack.graph(gold["persona"], llm))
                row = await run_job(graph, gold, stack, arguments.seed, judge)
                append_row(rows_path, row)
                print(key, row["status"], row["replied"], row["tokens"], flush=True)
            rows.append(finished.get(key) or row)
        stack.stop()
    components.close()
    result = {
        "variant": variant,
        "data_source": arguments.source,
        "official_gigsim": arguments.source == "gigsim",
        "seed": arguments.seed,
        "overall": summarise(rows),
    }
    write_result(f"outcomes_{name}{tag}.json", result)
    return result


def parser() -> argparse.ArgumentParser:
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--limit", type=int, default=30)
    options.add_argument("--variant", default=None)
    options.add_argument("--data", type=Path, default=Path("data/processed/devsim"))
    options.add_argument("--source", default="devsim")
    options.add_argument("--seed", type=int, default=42)
    options.add_argument("--tag", default="")
    options.add_argument("--judge", action="store_true", help="Also score with the rubric judge")
    options.add_argument("--fresh", action="store_true", help="Ignore rows from an earlier run")
    return options


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print(json.dumps(asyncio.run(run(parser().parse_args()))["overall"], indent=2))
