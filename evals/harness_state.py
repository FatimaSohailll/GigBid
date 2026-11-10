"""The starting state for one evaluation job."""

from typing import Any

from agent.state import UserSettings

# Generous on purpose: evaluation measures decisions, not the user's weekly Connects budget.
EVAL_SETTINGS = UserSettings(floor_usd=50, weekly_connects_budget=100_000)


def trace_id(gold: dict[str, Any]) -> str:
    return f"eval-{gold['persona']}-{gold['job_id']}"


def initial_state(gold: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": gold["job_id"],
        "user_id": gold["persona"],
        "trace_id": trace_id(gold),
        "settings": EVAL_SETTINGS,
    }
