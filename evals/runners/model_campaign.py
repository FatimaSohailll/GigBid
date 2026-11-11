"""The model-based sample for Part B: python -m evals.runners.model_campaign

Runs bid decisions, then proposals for V0 and the deployed variant, in growing rounds. V0 and the
winner advance together, so a provider limit cannot leave one far ahead. Finished jobs are kept:
when a run stops at a daily limit, run the same command later and it continues.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from agent.config import get_config
from evals.results_table import main as regenerate_table
from evals.runners import decision_eval, gigsim_outcomes
from evals.runners.rag_eval import RESULTS_DIR, safe_name, write_result

BASELINE = "v0_naive"
ROUNDS = ((30, 10), (60, 20), (60, 30))  # (decision jobs, proposal jobs per variant)
SOURCE = "devsim"
STOPPED = 3


def _rows(variant: str, directory: Path) -> dict[str, dict[str, Any]]:
    path = directory / f"outcomes_rows_{safe_name(variant)}_{SOURCE}.jsonl"
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return {f"{r['persona']}:{r['job_id']}": r for r in rows if not r.get("error")}


def paired_comparison(baseline: str, treated: str, directory: Path = RESULTS_DIR) -> dict[str, Any]:
    """Compare only jobs both variants completed, so the two columns share one job set."""
    first, second = _rows(baseline, directory), _rows(treated, directory)
    common = sorted(set(first) & set(second))

    def side(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return gigsim_outcomes.report([rows[key] for key in common])

    reply_changes = {"treated_only": 0, "baseline_only": 0, "both": 0, "neither": 0}
    for key in common:
        a, b = first[key]["replied"], second[key]["replied"]
        reply_changes[
            "both" if a and b else "treated_only" if b else "baseline_only" if a else "neither"
        ] += 1
    return {
        "data_source": SOURCE,
        "official_gigsim": False,
        "baseline": baseline,
        "treated": treated,
        "paired_jobs": len(common),
        "reply_outcome_by_job": reply_changes,
        "baseline_results": side(first) if common else None,
        "treated_results": side(second) if common else None,
    }


async def run_campaign() -> int:
    winner = get_config().rag.variant
    for decisions, proposals in ROUNDS:
        print(f"Round: {decisions} decisions, {proposals} proposals per variant", flush=True)
        result = await decision_eval.run(
            decision_eval.parser().parse_args(["--limit", str(decisions)])
        )
        if result["stopped_early"]:
            return STOPPED
        for variant in (BASELINE, winner):
            arguments = ["--limit", str(proposals), "--variant", variant, "--judge"]
            outcome = await gigsim_outcomes.run(gigsim_outcomes.parser().parse_args(arguments))
            if outcome["stopped_early"]:
                return STOPPED
    return 0


def finish() -> None:
    winner = get_config().rag.variant
    write_result(f"outcomes_comparison_{SOURCE}.json", paired_comparison(BASELINE, winner))
    regenerate_table()


async def main() -> int:
    code = await run_campaign()
    finish()
    if code == STOPPED:
        print("Stopped at a provider limit. Run the same command later to continue.", flush=True)
    return code


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(asyncio.run(main()))
