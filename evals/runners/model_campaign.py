"""The model-based sample for Part B: python -m evals.runners.model_campaign

Runs bid decisions, then proposals for V0 and the deployed variant, in growing rounds. V0 and the
winner advance together, so a provider limit cannot leave one far ahead. Finished jobs are kept:
when a run stops at a daily limit, run the same command later and it continues.
"""

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime, timedelta
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


_RETRY_IN = re.compile(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?")
DEFAULT_WAIT_SECONDS = 600.0


def daily_limit_wait(error: BaseException) -> float | None:
    """Seconds until the provider frees tokens, if this error is a rate limit; else None.

    Groq's daily token limit is a rolling window, so tokens come back gradually and the message
    says when the next request will fit.
    """
    text = str(error)
    if "rate_limit" not in text and "429" not in text and type(error).__name__ != "RateLimitError":
        return None
    match = _RETRY_IN.search(text)
    if not match or not any(match.groups()):
        return DEFAULT_WAIT_SECONDS
    hours, minutes, seconds = (float(part) if part else 0.0 for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds + 15  # a little slack after the stated time


async def main(wait: bool = False, hours: float = 30.0) -> int:
    deadline = time.monotonic() + hours * 3600
    while True:
        try:
            code = await run_campaign()
            pause = DEFAULT_WAIT_SECONDS if code == STOPPED else None
        except Exception as error:  # a limit can surface outside the graph, in the final check
            pause = daily_limit_wait(error)
            if pause is None:
                raise
            code = STOPPED
        finish()
        if code != STOPPED or not wait or pause is None:
            break
        if time.monotonic() + pause > deadline:
            print("Deadline reached; results so far are saved.", flush=True)
            break
        resume = datetime.now() + timedelta(seconds=pause)
        print(f"Provider limit reached. Waiting until {resume:%H:%M} to continue.", flush=True)
        await asyncio.sleep(pause)
    if code == STOPPED and not wait:
        print("Stopped at a provider limit. Run the same command later to continue.", flush=True)
    return code


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument(
        "--wait", action="store_true", help="Wait at provider limits and continue, until --hours"
    )
    options.add_argument("--hours", type=float, default=30.0)
    arguments = options.parse_args()
    sys.exit(asyncio.run(main(arguments.wait, arguments.hours)))
