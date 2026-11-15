"""The decision skips jobs whose budget tops out below the user's minimum project price."""

from typing import Any

import pytest

from agent.config import get_config
from agent.handoffs import BidAction, FitReport, RiskReport
from agent.nodes import decision
from agent.runtime import AgentRuntime
from agent.state import UserSettings
from evals.harness_state import eval_settings, persona_floor


def state(budget_max: float, floor: float, fit: float = 0.9) -> dict[str, Any]:
    return {
        "job": {"job_id": "j1", "budget": {"min": 0, "max": budget_max}},
        "settings": UserSettings(floor_usd=floor, weekly_connects_budget=100),
        "risk_report": RiskReport(score=0.1, explanation="Looks fine."),
        "fit_report": FitReport(score=fit),
    }


def runtime() -> AgentRuntime:
    return AgentRuntime(get_config(), None, None, None)  # type: ignore[arg-type]


async def test_a_budget_below_the_floor_is_skipped_however_good_the_fit() -> None:
    result = await decision.run(state(budget_max=240, floor=500), runtime())  # type: ignore[arg-type]
    assert result["decision"].action == BidAction.SKIP
    assert "below your minimum 500" in result["decision"].reasons[0]


@pytest.mark.parametrize("budget_max", [500, 6000, 0])
async def test_a_budget_at_the_floor_or_unstated_still_bids(budget_max: float) -> None:
    result = await decision.run(state(budget_max=budget_max, floor=500), runtime())  # type: ignore[arg-type]
    assert result["decision"].action == BidAction.BID


async def test_risk_is_still_checked_first() -> None:
    values = state(budget_max=240, floor=500)
    values["risk_report"] = RiskReport(score=0.95, explanation="Asks for payment off-platform.")
    result = await decision.run(values, runtime())  # type: ignore[arg-type]
    assert result["decision"].reasons[0].startswith("Risk is too high")


def test_evaluation_uses_each_personas_own_minimum() -> None:
    assert persona_floor("ai_ml_engineer") == 500
    assert eval_settings("data_analyst").floor_usd == 300
    assert persona_floor("nobody") == 50


def test_rescore_changes_only_jobs_below_the_floor() -> None:
    from evals.runners.decision_rescore import rescore

    rows = [
        {
            "job_id": "low",
            "job_type": "low_budget",
            "persona": "ai_ml_engineer",
            "predicted": "bid",
            "gold_bid": False,
        },
        {
            "job_id": "ok",
            "job_type": "good_fit",
            "persona": "ai_ml_engineer",
            "predicted": "bid",
            "gold_bid": True,
        },
    ]
    result = rescore(rows, {"low": 240.0, "ok": 6000.0})
    assert [item["job_id"] for item in result["changed"]] == ["low"]
    assert result["bid_or_skip_after"]["precision"] == 1.0
    assert result["bid_or_skip_before"]["precision"] == 0.5
