"""Re-score saved decisions after a deterministic rule change, with no model calls.

  python -m evals.runners.decision_rescore

The budget-floor rule (agent/nodes/decision.py) runs after every model step and reads only the
job's budget and the user's minimum project price, so applying it to the saved decision rows gives
exactly what a re-run would decide, for every job it changes, without spending model quota. Jobs it
does not fire on keep their saved decision. Writes evals/results/decision_rescored_<source>.json.
"""

import argparse
import json
from pathlib import Path
from typing import Any

from evals.harness_state import persona_floor
from evals.metrics import precision_recall_f1
from evals.runners.rag_eval import RESULTS_DIR, write_result


def load_budgets(data: Path) -> dict[str, float]:
    jobs = json.loads((data / "marketplace" / "jobs.json").read_text(encoding="utf-8"))
    return {job["job_id"]: float(job["budget"]["max"] or 0) for job in jobs}


def rescore(rows: list[dict[str, Any]], budgets: dict[str, float]) -> dict[str, Any]:
    done = [row for row in rows if not row.get("error")]
    changed = []
    after: list[tuple[bool, bool]] = []
    for row in done:
        budget_max = budgets.get(row["job_id"], 0.0)
        floor = persona_floor(row["persona"])
        predicted = row["predicted"]
        if predicted != "skip" and 0 < budget_max < floor:
            changed.append(
                {
                    "job_id": row["job_id"],
                    "job_type": row["job_type"],
                    "before": predicted,
                    "after": "skip",
                    "gold": "bid" if row["gold_bid"] else "skip",
                    "budget_max": budget_max,
                    "floor": floor,
                }
            )
            predicted = "skip"
        after.append((row["gold_bid"], predicted == "bid"))
    before = precision_recall_f1((row["gold_bid"], row["predicted"] == "bid") for row in done)
    return {
        "jobs": len(done),
        "bid_or_skip_before": before,
        "bid_or_skip_after": precision_recall_f1(after),
        "changed": changed,
        "method": "budget-floor rule applied to saved decisions; no model calls",
    }


def main() -> None:
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--source", default="devsim")
    options.add_argument("--data", type=Path, default=Path("data/processed/devsim"))
    args = options.parse_args()
    path = RESULTS_DIR / f"decision_rows_{args.source}.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    result = {
        "data_source": args.source,
        "official_gigsim": False,
        **rescore(rows, load_budgets(args.data)),
    }
    write_result(f"decision_rescored_{args.source}.json", result)
    print(json.dumps({key: result[key] for key in ("bid_or_skip_before", "bid_or_skip_after")}))
    for item in result["changed"]:
        print(item)


if __name__ == "__main__":
    main()
