"""Simulated reply and hire rates on a fixed job set: python -m evals.runners.gigsim_outcomes

Each job runs through the whole graph, with approval given automatically, and the simulator's
client then reacts. On DevSim the client is a labelled stand-in, so never report these as GigSim.
"""

import argparse
import asyncio
import json
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from dotenv import load_dotenv

from agent.config import get_config
from agent.llm import LlmGateway, ModelRouter
from agent.state import ThreadStatus
from agent.tracing import flush_traces
from evals.harness import (
    EvalStack,
    append_row,
    eval_config,
    load_gold,
    read_finished_rows,
    rewrite_rows,
    select_jobs,
    start_eval_stack,
)
from evals.job_runner import JobRunner
from evals.judge import score_proposal
from evals.metrics import percentile
from evals.runners.decision_eval import mean_cost, models_used, usage_by_node
from evals.runners.rag_eval import RESULTS_DIR, safe_name, write_result
from rag.retriever import open_components


async def run_job(
    runner: JobRunner,
    gold: dict[str, Any],
    stack: EvalStack,
    seed: int,
    judge: LlmGateway | None,
) -> dict[str, Any]:
    result = await runner.complete(gold)
    row: dict[str, Any] = {
        **result.as_row(),
        "job_id": gold["job_id"],
        "persona": gold["persona"],
        "job_type": gold["job_type"],
        "submitted": result.status == ThreadStatus.SUBMITTED.value,
        "replied": False,
        "hired": False,
        "judge_overall": None,
    }
    if row["submitted"]:
        assert result.proposal_id is not None and result.cover_letter is not None
        outcome = stack.simulate_client(result.proposal_id, seed)
        row.update(replied=outcome["replied"], hired=outcome["hired"])
        if judge is not None:
            score = await score_proposal(judge, result.job, result.cover_letter)
            row["judge_overall"] = score.overall
    return row


def _mean(values: list[float]) -> float | None:
    return round(mean(values), 4) if values else None


def _ratio(rows: list[dict[str, Any]], part: str, whole: str) -> float | None:
    total = sum(row[whole] or 0 for row in rows)
    return round(sum(row[part] or 0 for row in rows) / total, 4) if total else None


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
        "unsupported_rate_before": _ratio(drafted, "unsupported_before", "claims_before"),
        "unsupported_rate_after": _ratio(drafted, "unsupported_after", "claims_after"),
        "judge_score": _mean(
            [row["judge_overall"] for row in sent if row["judge_overall"] is not None]
        ),
        "tokens_per_proposal": _mean([row["tokens"] for row in sent]),
        "cost_per_proposal_usd": mean_cost(sent),
        "models_used": models_used(done),
        "usage_by_node": usage_by_node(sent) if sent else {},
        "p50_latency_seconds": round(percentile(latencies, 50), 2) if latencies else None,
        "p95_latency_seconds": round(percentile(latencies, 95), 2) if latencies else None,
    }


def report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Overall, per persona (as B4 requires) and per job type."""
    groups: dict[str, defaultdict[str, list[dict[str, Any]]]] = {
        "persona": defaultdict(list),
        "job_type": defaultdict(list),
    }
    for row in rows:
        for field, group in groups.items():
            group[row[field]].append(row)
    return {
        "overall": summarise(rows),
        "by_persona": {key: summarise(value) for key, value in sorted(groups["persona"].items())},
        "by_job_type": {key: summarise(value) for key, value in sorted(groups["job_type"].items())},
    }


async def run(arguments: argparse.Namespace) -> dict[str, Any]:
    load_dotenv()
    base = eval_config(get_config())
    variant = arguments.variant or base.rag.variant
    # The Writer's phrasing examples follow the variant, so each run tests one complete design.
    config = base.model_copy(update={"rag": base.rag.model_copy(update={"variant": variant})})
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
        runners: dict[str, JobRunner] = {}
        rows: list[dict[str, Any]] = []
        failures_in_a_row = 0
        stopped_early = False
        for gold in jobs:
            key = f"{gold['persona']}:{gold['job_id']}"
            if key not in finished:
                persona = gold["persona"]
                if persona not in runners:
                    runners[persona] = stack.runner(persona, llm)
                row = await run_job(runners[persona], gold, stack, arguments.seed, judge)
                append_row(rows_path, row)
                print(key, row["status"], row["replied"], row["tokens"], flush=True)
                failures_in_a_row = failures_in_a_row + 1 if row["error"] else 0
            rows.append(finished.get(key) or row)
            if failures_in_a_row >= arguments.max_failures:
                print("Stopping: repeated failures, probably a provider limit.", flush=True)
                stopped_early = True
                break
        stack.stop()
    flush_traces(config)
    components.close()
    result = {
        "variant": variant,
        "data_source": arguments.source,
        "official_gigsim": arguments.source == "gigsim",
        "seed": arguments.seed,
        "stopped_early": stopped_early,
        **report(rows),
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
    options.add_argument(
        "--max-failures", type=int, default=3, help="Stop after this many in a row"
    )
    return options


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print(json.dumps(asyncio.run(run(parser().parse_args()))["overall"], indent=2))
