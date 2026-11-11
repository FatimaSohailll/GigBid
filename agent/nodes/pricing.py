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
    """The market median when it fits the client's budget, otherwise the budget's midpoint.

    Clipping an outside median to the budget's edge bid the minimum whenever the market sat below
    the budget, which under-priced most jobs (evals/results/pricing_rules_devsim.json).
    """
    has_budget = budget_max > 0
    if median is not None and (not has_budget or budget_min <= median <= budget_max):
        target = median
    elif has_budget:
        target = (budget_min + budget_max) / 2
    else:
        target = 0.0
    return round(max(target, floor), 2)


def price_basis(median: float | None, budget_min: float, budget_max: float) -> str:
    if median is None:
        return "No market data for this category; priced at the middle of the client's budget."
    if budget_max <= 0 or budget_min <= median <= budget_max:
        return f"Market median {median:.0f}, which fits the client's budget."
    return f"Market median {median:.0f} falls outside the client's budget; priced at its middle."


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
    market = price_basis(stats.get("p50"), budget["min"], budget["max"])
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
