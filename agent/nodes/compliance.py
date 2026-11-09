"""Compliance Guard: enforce the policy pack. Runs after every draft and every human edit."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.guardrails import scan_outgoing_text
from agent.handoffs import ComplianceAction, ComplianceVerdict, PolicyViolation, ScreeningAnswer
from agent.runtime import AgentRuntime
from agent.state import BidState, ThreadStatus

_RULE_EXPLANATIONS = {
    "contact": "Contact details before a contract are not allowed.",
    "payment": "Payment outside the platform is not allowed.",
    "guarantee": "Results cannot be guaranteed.",
    "credentials": "Credentials must not be requested or shared.",
}


class _ModelVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    violations: list[PolicyViolation] = Field(default_factory=list)
    block: bool = False
    reason: str = ""


async def run(state: BidState, runtime: AgentRuntime) -> dict[str, Any]:
    draft = state["drafts"][-1]
    rules = runtime.config.compliance
    letter = scan_outgoing_text(draft.cover_letter)
    answers = [(item, scan_outgoing_text(item.answer)) for item in draft.screening_answers]
    scans = [letter, *(scan for _, scan in answers)]
    violations = [
        PolicyViolation(
            rule_id=rules.rule_ids[rule],  # type: ignore[index]
            excerpt=excerpt,
            explanation=_RULE_EXPLANATIONS[rule],
        )
        for scan in scans
        for rule, excerpt in scan.findings
    ]
    combined = "\n".join(scan.text for scan in scans)
    policy = Path(rules.policy_path).read_text(encoding="utf-8")
    result = await runtime.llm.structured(
        "compliance",
        runtime.prompt("compliance"),
        f"<policy>\n{policy}\n</policy>\n<draft>\n{combined}\n</draft>",
        _ModelVerdict,
    )
    # This model cannot see the portfolio. Whether a claim is true is the claim verifier's job.
    model_violations = [
        item for item in result.value.violations if item.rule_id != rules.rule_ids["fabrication"]
    ]
    violations += model_violations
    changed = any(scan.text != item.answer for item, scan in answers) or (
        letter.text != draft.cover_letter
    )
    block = (result.value.block and bool(model_violations)) or any(s.blocked for s in scans)

    update: dict[str, Any] = {"tokens_used": result.tokens}
    if block:
        reason = result.value.reason or "This draft breaks a marketplace rule and cannot be sent."
        action = ComplianceAction.BLOCK
        update["status"] = ThreadStatus.BLOCKED
    elif changed:
        reason, action = "Prohibited content was removed.", ComplianceAction.REDACTED
        update["drafts"] = [
            draft.model_copy(
                update={
                    "cover_letter": letter.text,
                    "screening_answers": [
                        ScreeningAnswer(question=item.question, answer=scan.text)
                        for item, scan in answers
                    ],
                }
            )
        ]
    else:
        reason, action = "", ComplianceAction.ALLOW
    update["compliance_verdicts"] = [
        ComplianceVerdict(
            job_id=draft.job_id,
            draft_version=draft.version,
            action=action,
            violations=violations,
            redacted_text=letter.text if action is ComplianceAction.REDACTED else None,
            reason=reason,
        )
    ]
    return update
