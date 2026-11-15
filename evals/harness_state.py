"""The starting state for one evaluation job."""

import json
from functools import cache
from pathlib import Path
from typing import Any

from agent.state import UserSettings

PERSONAS_DIR = Path("data/processed/devsim/personas")
DEFAULT_FLOOR_USD = 50.0
# Generous on purpose: evaluation measures decisions, not the user's weekly Connects budget.
WEEKLY_CONNECTS = 100_000


@cache
def persona_floor(persona: str, personas_dir: Path = PERSONAS_DIR) -> float:
    """The persona's own minimum project price, as a real user would set it in onboarding."""
    try:
        profile = json.loads((personas_dir / persona / "profile.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Before `make seed` writes the profiles (a fresh checkout, CI), read the same value from
        # the DevSim catalog the profiles are generated from.
        return _catalog_floor(persona)
    return float(profile.get("minimum_project_usd") or DEFAULT_FLOOR_USD)


def _catalog_floor(persona: str) -> float:
    from devsim.datagen.catalog import load_catalog

    floors = {entry.persona_id: float(entry.floor_usd) for entry in load_catalog()}
    return floors.get(persona, DEFAULT_FLOOR_USD)


def eval_settings(persona: str) -> UserSettings:
    return UserSettings(floor_usd=persona_floor(persona), weekly_connects_budget=WEEKLY_CONNECTS)


def trace_id(gold: dict[str, Any]) -> str:
    return f"eval-{gold['persona']}-{gold['job_id']}"


def initial_state(gold: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": gold["job_id"],
        "user_id": gold["persona"],
        "trace_id": trace_id(gold),
        "settings": eval_settings(gold["persona"]),
    }
