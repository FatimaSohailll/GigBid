"""Compare pricing rules against the gold fair bands: python -m evals.runners.pricing_rules

Deterministic and model-free. Market statistics are computed the way the simulator computes them,
from the historical jobs that share a skill with the job.
"""

import argparse
import json
import statistics
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent.nodes.pricing import propose_amount
from evals.harness import load_gold
from evals.runners.rag_eval import write_result

Rule = Callable[[dict[str, Any], float | None], float]
FLOOR = 50.0  # the evaluation floor, below every fair band


def market_median(history: list[dict[str, Any]], category: str, skills: list[str]) -> float | None:
    wanted = {skill.lower() for skill in skills}
    prices = [
        job["final_price_usd"]
        for job in history
        if job["category"] == category and wanted & {s.lower() for s in job["required_skills"]}
    ]
    if len(prices) > 1:
        return float(statistics.quantiles(prices, n=4)[1])
    return float(prices[0]) if prices else None


def clipped_median(job: dict[str, Any], median: float | None) -> float:
    """The previous rule: the median moved to the nearest edge of the client's budget."""
    low, high = job["budget"]["min"], job["budget"]["max"]
    target = median if median is not None else (low + high) / 2
    return float(round(max(min(max(target, low), high), FLOOR), 2))


RULES: dict[str, Rule] = {
    "previous: median clipped to budget": clipped_median,
    "shipped: median if inside budget, else midpoint": lambda job, median: propose_amount(
        median, job["budget"]["min"], job["budget"]["max"], FLOOR
    ),
    "budget midpoint only (ignores market data)": lambda job, _: propose_amount(
        None, job["budget"]["min"], job["budget"]["max"], FLOOR
    ),
}


def compare(data: Path) -> dict[str, Any]:
    jobs = {
        job["job_id"]: job
        for job in json.loads((data / "marketplace" / "jobs.json").read_text(encoding="utf-8"))
    }
    history = json.loads((data / "marketplace" / "history.json").read_text(encoding="utf-8"))
    labelled = [gold for gold in load_gold(data) if gold["fair_price_band"]]
    results: dict[str, Any] = {}
    for name, rule in RULES.items():
        sides: Counter[str] = Counter()
        for gold in labelled:
            job = jobs[gold["job_id"]]
            price = rule(job, market_median(history, job["category"], job["required_skills"]))
            band = gold["fair_price_band"]
            sides[
                "in_band"
                if band["min"] <= price <= band["max"]
                else "below"
                if price < band["min"]
                else "above"
            ] += 1
        results[name] = {
            key: round(sides[key] / len(labelled), 4) for key in ("in_band", "below", "above")
        }
    return {
        "data_source": "devsim",
        "official_gigsim": False,
        "jobs": len(labelled),
        "rules": results,
    }


if __name__ == "__main__":
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--data", type=Path, default=Path("data/processed/devsim"))
    outcome = compare(options.parse_args().data)
    write_result("pricing_rules_devsim.json", outcome)
    print(json.dumps(outcome, indent=2))
