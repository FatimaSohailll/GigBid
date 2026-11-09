"""Pricing Strategist: propose a bid and decide whether the Connects cost is worth it.

This is arithmetic over market statistics, so it is deterministic: no model call is needed.
"""

from typing import Any

from agent.handoffs import PriceQuote
from agent.mcp_client import ToolCallError
from agent.runtime import AgentRuntime
from agent.state import BidState, ThreadStatus


def propose_amount(
    median: float | None,
    budget_min: float,
    budget_max: float,
    floor: float,
) -> float:
    """Market median, kept inside the client's budget, and never below the user's floor."""
    target = median if median is not None else (budget_min + budget_max) / 2
    if budget_max > 0:
        target = min(max(target, budget_min), budget_max)
    return round(max(target, floor), 2)


async def run(state: BidState, runtime: AgentRuntime) -> dict[str, Any]:
    job = state["job"]
    settings = state["settings"]
    rules = runtime.config.pricing
    stats: dict[str, Any] = {}
    try:
        stats = await runtime.tools.call(
            "pricing",
            "get_price_stats",
            {"category": job["category"], "skills": job.get("required_skills", [])},
        )
    except ToolCallError as error:
        if error.code != "not_found":
            raise
    balance = await runtime.tools.call("pricing", "get_connects_balance", {})
    budget = job["budget"]
    amount = propose_amount(stats.get("p50"), budget["min"], budget["max"], settings.floor_usd)
    cost = job["connects_cost"]
    affordable = balance["balance"] >= cost
    within_budget = settings.connects_spent_this_week + cost <= settings.weekly_connects_budget
    cost_share = (cost * rules.connect_value_usd) / amount if amount else 1.0
    worth_it = affordable and within_budget and cost_share <= rules.max_connects_share_of_bid
    market = f"Market median {stats['p50']:.0f}." if stats else "No market data for this category."
    rationale = (
        f"{market} Client budget {budget['min']:.0f} to {budget['max']:.0f}. "
        f"Your floor {settings.floor_usd:.0f}. Bid {amount:.0f}. "
        f"This job costs {cost} Connects ({cost_share:.1%} of the bid); "
        f"balance {balance['balance']}, weekly budget {settings.weekly_connects_budget}."
    )
    quote = PriceQuote(
        job_id=job["job_id"],
        amount=amount,
        currency=budget.get("currency", "USD"),
        floor=settings.floor_usd,
        market_p25=stats.get("p25"),
        market_p50=stats.get("p50"),
        market_p75=stats.get("p75"),
        connects_cost=cost,
        connects_worth_it=worth_it,
        rationale=rationale,
    )
    update: dict[str, Any] = {"price_quote": quote}
    if not worth_it:
        update["status"] = ThreadStatus.SKIPPED
    return update
