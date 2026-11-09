"""Risk Assessor: score client quality and flag scams, unpaid work and injected instructions."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.guardrails import delimit_untrusted, scan_job_post
from agent.handoffs import RiskFlag, RiskReport
from agent.nodes.analyst import job_text
from agent.runtime import AgentRuntime
from agent.state import BidState

_CERTAIN_SCAM_SCORE = 0.95


class _ModelRisk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0, le=1)
    flags: list[RiskFlag] = Field(default_factory=list)
    explanation: str


def client_quality(history: dict[str, Any]) -> float:
    """A simple score from the record. A new client is uncertain, not bad."""
    if not history["completed_projects"]:
        return 0.5 if history["payment_verified"] else 0.3
    rating = history["average_rating"] / 5
    verified = 1.0 if history["payment_verified"] else 0.6
    return round(float(rating * verified), 3)


async def run(state: BidState, runtime: AgentRuntime) -> dict[str, Any]:
    job = state["job"]
    history = await runtime.tools.call(
        "risk_assessor", "get_client_history", {"client_id": job["client_id"]}
    )
    signals = scan_job_post(job["description"])
    quality = client_quality(history)
    result = await runtime.llm.structured(
        "risk_assessor",
        runtime.prompt("risk_assessor"),
        f"{delimit_untrusted('job_post', job_text(job))}\n"
        f"Client history: {delimit_untrusted('client_history', str(history))}",
        _ModelRisk,
    )
    flags = set(result.value.flags) | signals.flags
    if quality < 0.5:
        flags.add(RiskFlag.LOW_CLIENT_QUALITY)
    if not history["payment_verified"]:
        flags.add(RiskFlag.PAYMENT_UNVERIFIED)
    # The rules set a floor: the model can raise risk but never clear a detected scam.
    score = max(result.value.score, _CERTAIN_SCAM_SCORE if RiskFlag.SCAM in signals.flags else 0.0)
    report = RiskReport(
        score=score,
        client_quality=quality,
        flags=sorted(flags, key=str),
        injected_text=signals.injected_text,
        explanation=result.value.explanation,
    )
    return {"risk_report": report, "tokens_used": result.tokens}
