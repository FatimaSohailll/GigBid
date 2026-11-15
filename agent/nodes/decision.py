"""Decide skip, watch or bid with explicit, auditable thresholds from config."""

from typing import Any

from agent.handoffs import BidAction, BidDecision, RiskFlag
from agent.runtime import AgentRuntime
from agent.state import BidState, ThreadStatus


async def run(state: BidState, runtime: AgentRuntime) -> dict[str, Any]:
    risk = state["risk_report"]
    fit = state["fit_report"]
    limits = runtime.config.thresholds
    budget_max = float(state["job"].get("budget", {}).get("max") or 0)
    floor = state["settings"].floor_usd
    if RiskFlag.SCAM in risk.flags or risk.score > limits.maximum_risk_score:
        action, reasons = BidAction.SKIP, [f"Risk is too high: {risk.explanation}"]
    elif 0 < budget_max < floor:
        # A budget that tops out below the user's minimum project price is not worth Connects,
        # however good the fit. Hourly jobs with no stated budget (max 0) are not skipped here.
        reason = f"The client's budget tops out at {budget_max:.0f}, below your minimum {floor:.0f}"
        action, reasons = BidAction.SKIP, [reason]
    elif fit.score >= limits.minimum_fit_score:
        action, reasons = BidAction.BID, [f"Fit score {fit.score:.2f} meets the threshold."]
    elif fit.score >= limits.watch_fit_score:
        action, reasons = BidAction.WATCH, [f"Fit score {fit.score:.2f} is borderline."]
    else:
        action, reasons = BidAction.SKIP, [f"Fit score {fit.score:.2f} is too low."]
    decision = BidDecision(
        job_id=state["job"]["job_id"],
        action=action,
        fit_score=fit.score,
        risk_score=risk.score,
        reasons=reasons,
    )
    status = {
        BidAction.SKIP: ThreadStatus.SKIPPED,
        BidAction.WATCH: ThreadStatus.WATCHING,
        BidAction.BID: ThreadStatus.RUNNING,
    }[action]
    return {"decision": decision, "status": status}
