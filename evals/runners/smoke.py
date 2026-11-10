"""The pull-request gate: 30 decisions and 3 full proposals, checked against thresholds."""

import asyncio
import sys
from pathlib import Path
from typing import Any

import yaml

from evals.runners import decision_eval, gigsim_outcomes

THRESHOLDS = Path("evals/thresholds.yaml")
DECISION_JOBS, PROPOSAL_JOBS = 30, 3


def failures(
    decision: dict[str, Any], outcomes: dict[str, Any], limits: dict[str, Any]
) -> list[str]:
    found: list[str] = []
    f1 = decision.get("bid_or_skip", {}).get("f1", 0.0)
    if f1 < limits["min_bid_or_skip_f1"]:
        found.append(f"bid-or-skip F1 {f1} is below {limits['min_bid_or_skip_f1']}")
    unsupported = outcomes.get("unsupported_claims_after")
    if unsupported is None:
        found.append("no proposal reached claim verification")
    elif unsupported > limits["max_unsupported_claims_after_verification"]:
        found.append(f"unsupported claims per proposal rose to {unsupported}")
    failed = decision.get("failed", 0) + outcomes.get("failed", 0)
    if failed > limits["max_failed_jobs"]:
        found.append(f"{failed} jobs failed")
    return found


async def main() -> int:
    limits = yaml.safe_load(THRESHOLDS.read_text(encoding="utf-8"))
    decision = await decision_eval.run(
        decision_eval.parser().parse_args(
            ["--limit", str(DECISION_JOBS), "--tag", "smoke", "--fresh"]
        )
    )
    outcomes = await gigsim_outcomes.run(
        gigsim_outcomes.parser().parse_args(
            ["--limit", str(PROPOSAL_JOBS), "--tag", "smoke", "--fresh"]
        )
    )
    problems = failures(decision["overall"], outcomes["overall"], limits)
    for problem in problems:
        print("FAILED:", problem)
    print("Smoke evaluation:", "failed" if problems else "passed")
    return 1 if problems else 0


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(asyncio.run(main()))
