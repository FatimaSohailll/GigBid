"""E5: measured latency and cost against the declared budgets: python -m evals.runners.budget_report

Reads the rows the decision and proposal runs saved; it calls no model. Also measures the
optimisation GigBid relies on most: skipping early. The decision node stops weak jobs before
Pricing, Writer, Critic and the claim checks run, so a skipped job costs a fraction of a proposal.

Writes evals/results/budgets_<source>.json.
"""

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any

from agent.config import ServiceBudget, get_config
from evals.runners.rag_eval import RESULTS_DIR, write_result


def percentile(values: list[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(share * (len(ordered) - 1)))]


def measure(rows: list[dict[str, Any]], budget: ServiceBudget) -> dict[str, Any]:
    done = [row for row in rows if not row.get("error")]
    costs = [row.get("cost_usd") for row in done]
    priced = bool(costs) and all(cost is not None for cost in costs)
    p95 = percentile([row["latency_seconds"] for row in done], 0.95)
    mean_cost = round(mean(cost for cost in costs if cost is not None), 6) if priced else None
    return {
        "jobs": len(done),
        "declared_p95_seconds": budget.p95_seconds,
        "measured_p95_seconds": round(p95, 2) if p95 is not None else None,
        "declared_usd": budget.usd,
        "measured_mean_usd": mean_cost,
        "within_latency": p95 is not None and p95 <= budget.p95_seconds,
        "within_cost": mean_cost is not None and mean_cost <= budget.usd,
    }


def skip_early(decisions: list[dict[str, Any]], proposals: list[dict[str, Any]]) -> dict[str, Any]:
    """Tokens a skipped job used against tokens a full proposal used, both measured."""
    skipped = [
        r["tokens"] for r in decisions if not r.get("error") and r.get("predicted") == "skip"
    ]
    full = [r["tokens"] for r in proposals if not r.get("error") and r.get("submitted")]
    if not skipped or not full:
        return {"measured": False}
    per_skip, per_proposal = mean(skipped), mean(full)
    return {
        "measured": True,
        "skipped_jobs": len(skipped),
        "proposals": len(full),
        "mean_tokens_skipped_job": round(per_skip),
        "mean_tokens_proposal": round(per_proposal),
        "tokens_avoided_per_skip": round(per_proposal - per_skip),
        "share_avoided_per_skip": round(1 - per_skip / per_proposal, 4),
    }


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def report(directory: Path, source: str) -> dict[str, Any]:
    budgets = get_config().budgets
    decisions = _read(directory / f"decision_rows_{source}.jsonl")
    proposals = [
        row
        for path in sorted(directory.glob(f"outcomes_rows_*_{source}.jsonl"))
        for row in _read(path)
    ]
    sent = [row for row in proposals if row.get("submitted")]
    return {
        "data_source": source,
        "profile": get_config().models.active_profile,
        "note": "Latency includes waiting out the model provider's free-tier rate limits.",
        "per_job_analysed": measure(decisions, budgets.per_job_analysed),
        "per_proposal": measure(sent, budgets.per_proposal),
        "optimisation_skip_early": skip_early(decisions, proposals),
    }


def main() -> None:
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--source", default="devsim")
    arguments = options.parse_args()
    result = report(RESULTS_DIR, arguments.source)
    write_result(f"budgets_{arguments.source}.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
