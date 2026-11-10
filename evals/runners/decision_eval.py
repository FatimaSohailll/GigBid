"""Bid-or-skip, scam and pricing metrics: python -m evals.runners.decision_eval [--limit 30]

Each job runs through the real graph and stops before the Writer. Finished jobs are kept, so a run
stopped by a provider's daily limit can continue.
"""

import argparse
import asyncio
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from dotenv import load_dotenv

from agent.config import get_config
from agent.handoffs import BidAction, RiskFlag
from agent.llm import ModelRouter
from evals.harness import (
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
from evals.metrics import percentile, precision_recall_f1
from evals.runners.rag_eval import RESULTS_DIR, write_result
from rag.retriever import open_components


async def score_job(graph: Any, gold: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    state = await graph.ainvoke(
        initial_state(gold), thread_config(gold), interrupt_before=["writer"]
    )
    decision = state.get("decision")
    risk = state.get("risk_report")
    quote = state.get("price_quote")
    band = gold["fair_price_band"]
    flags = risk.flags if risk else []
    return {
        "job_id": gold["job_id"],
        "persona": gold["persona"],
        "job_type": gold["job_type"],
        "gold_bid": gold["decision"] == "bid",
        "predicted": decision.action.value if decision else None,
        "is_scam": gold["is_scam"],
        "flagged_scam": RiskFlag.SCAM in flags,
        "has_injection": gold["has_injection"],
        "flagged_injection": RiskFlag.INJECTION in flags,
        "price": quote.amount if quote else None,
        "price_in_band": band["min"] <= quote.amount <= band["max"] if quote else None,
        "tokens": state.get("tokens_used", 0),
        "latency_seconds": round(time.perf_counter() - started, 2),
        "error": None if decision else (state.get("attention_reason") or "no decision reached"),
    }


def _rate(values: list[bool]) -> float | None:
    return round(mean(values), 4) if values else None


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
        graphs: dict[str, Any] = {}
        rows: list[dict[str, Any]] = []
        for gold in jobs:
            key = f"{gold['persona']}:{gold['job_id']}"
            if key not in finished:
                graph = graphs.setdefault(gold["persona"], stack.graph(gold["persona"], llm))
                row = await score_job(graph, gold)
                append_row(rows_path, row)
                print(key, row["predicted"], row["tokens"], row["error"] or "", flush=True)
            rows.append(finished.get(key) or row)
        stack.stop()
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
    return options


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print(asyncio.run(run(parser().parse_args()))["overall"])
