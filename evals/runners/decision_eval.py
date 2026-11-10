"""Bid-or-skip, scam and pricing metrics: python -m evals.runners.decision_eval [--limit 30]

Each job runs through the real graph and stops before the Writer. Finished jobs are kept, so a run
stopped by a provider's daily limit can continue.
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

from agent.config import get_config
from agent.handoffs import BidAction
from agent.llm import ModelRouter
from agent.tracing import flush_traces
from evals.harness import (
    append_row,
    eval_config,
    load_gold,
    read_finished_rows,
    rewrite_rows,
    select_jobs,
    start_eval_stack,
)
from evals.job_runner import JobRunner
from evals.metrics import percentile, precision_recall_f1
from evals.runners.rag_eval import RESULTS_DIR, write_result
from rag.retriever import open_components


async def score_job(runner: JobRunner, gold: dict[str, Any]) -> dict[str, Any]:
    result = await runner.decide(gold)
    band = gold["fair_price_band"]
    return {
        **result.as_row(),
        "job_id": gold["job_id"],
        "persona": gold["persona"],
        "job_type": gold["job_type"],
        "gold_bid": gold["decision"] == "bid",
        "predicted": result.decision,
        "is_scam": gold["is_scam"],
        "has_injection": gold["has_injection"],
        "price_in_band": None
        if result.price is None
        else band["min"] <= result.price <= band["max"],
    }


def _rate(values: list[bool]) -> float | None:
    return round(mean(values), 4) if values else None


def mean_cost(rows: list[dict[str, Any]]) -> float | None:
    """Unknown if any row used a model without a configured price."""
    costs: list[float | None] = [row.get("cost_usd") for row in rows]
    if not costs or any(cost is None for cost in costs):
        return None
    return round(sum(cost for cost in costs if cost is not None) / len(costs), 6)


def usage_by_node(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Mean tokens and latency per job for each node, to show where budget goes."""
    totals: defaultdict[str, defaultdict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        for item in row.get("usage", []):
            node = totals[item["node"]]
            node["tokens"] += item["input_tokens"] + item["output_tokens"]
            node["latency_seconds"] += item["latency_seconds"]
    return {
        name: {key: round(value / len(rows), 2) for key, value in node.items()}
        for name, node in sorted(totals.items())
    }


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    done = [row for row in rows if not row["error"]]
    if not done:
        return {"jobs": 0, "failed": len(rows)}
    latencies = [row["latency_seconds"] for row in done]
    return {
        "jobs": len(done),
        "failed": len(rows) - len(done),
        "bid_or_skip": precision_recall_f1(
            (row["gold_bid"], row["predicted"] == BidAction.BID.value) for row in done
        ),
        "scam": precision_recall_f1((row["is_scam"], row["flagged_scam"]) for row in done),
        "price_in_fair_band": _rate(
            [row["price_in_band"] for row in done if row["price_in_band"] is not None]
        ),
        "injection_flag_rate": _rate(
            [row["flagged_injection"] for row in done if row["has_injection"]]
        ),
        "tokens_per_job": round(mean(row["tokens"] for row in done), 1),
        "cost_per_job_usd": mean_cost(done),
        "usage_by_node": usage_by_node(done),
        "latency_seconds": {
            "p50": round(percentile(latencies, 50), 2),
            "p95": round(percentile(latencies, 95), 2),
        },
    }


def report(rows: list[dict[str, Any]], source: str) -> dict[str, Any]:
    groups: dict[str, defaultdict[str, list[dict[str, Any]]]] = {
        "persona": defaultdict(list),
        "job_type": defaultdict(list),
    }
    for row in rows:
        for field, group in groups.items():
            group[row[field]].append(row)
    return {
        "data_source": source,
        "official_gigsim": source == "gigsim",
        "overall": summarise(rows),
        "by_persona": {key: summarise(value) for key, value in sorted(groups["persona"].items())},
        "by_job_type": {key: summarise(value) for key, value in sorted(groups["job_type"].items())},
    }


async def run(arguments: argparse.Namespace) -> dict[str, Any]:
    load_dotenv()
    config = eval_config(get_config())
    components = open_components(config)
    tag = f"_{arguments.tag}" if arguments.tag else ""
    rows_path = RESULTS_DIR / f"decision_rows_{arguments.source}{tag}.jsonl"
    finished = {} if arguments.fresh else read_finished_rows(rows_path)
    rewrite_rows(rows_path, list(finished.values()))
    jobs = select_jobs(load_gold(arguments.data), arguments.limit)
    llm = ModelRouter(config)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as workdir:
        stack = start_eval_stack(Path(workdir), components, config, arguments.data / "marketplace")
        runners: dict[str, JobRunner] = {}
        rows: list[dict[str, Any]] = []
        for gold in jobs:
            key = f"{gold['persona']}:{gold['job_id']}"
            if key not in finished:
                persona = gold["persona"]
                if persona not in runners:
                    runners[persona] = stack.runner(persona, llm, not arguments.no_defences)
                row = await score_job(runners[persona], gold)
                append_row(rows_path, row)
                print(key, row["predicted"], row["tokens"], row["error"] or "", flush=True)
            rows.append(finished.get(key) or row)
        stack.stop()
    flush_traces(config)
    components.close()
    result = report(rows, arguments.source)
    write_result(f"decision_{arguments.source}{tag}.json", result)
    return result


def parser() -> argparse.ArgumentParser:
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--limit", type=int, default=None)
    options.add_argument("--data", type=Path, default=Path("data/processed/devsim"))
    options.add_argument("--source", default="devsim")
    options.add_argument("--tag", default="", help="Added to result file names, e.g. smoke")
    options.add_argument("--fresh", action="store_true", help="Ignore rows from an earlier run")
    options.add_argument(
        "--no-defences", action="store_true", help="Measure attack success without defences"
    )
    return options


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print(asyncio.run(run(parser().parse_args()))["overall"])
