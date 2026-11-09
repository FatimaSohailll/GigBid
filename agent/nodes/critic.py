"""Critic: check grounding, personalisation and length. Rules back up the model's judgement."""

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.guardrails import delimit_untrusted, scan_job_post
from agent.handoffs import ClaimSupport, Critique, CritiqueVerdict, RiskFlag
from agent.nodes.analyst import job_text
from agent.runtime import AgentRuntime
from agent.state import BidState

_GENERIC_OPENING = re.compile(
    r"^\s*(?:dear (?:sir|client|hiring)|hello,? i am|hi there|i am very interested|"
    r"i read your job|to whom)",
    re.IGNORECASE,
)


class _ModelCritique(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grounded: bool
    personalised: bool
    reasons: list[str] = Field(default_factory=list)


async def run(state: BidState, runtime: AgentRuntime) -> dict[str, Any]:
    draft = state["drafts"][-1]
    claims = "\n".join(f"- [{c.support}] {c.text} {c.evidence_ids}" for c in draft.claims)
    result = await runtime.llm.structured(
        "critic",
        runtime.prompt("critic"),
        f"{delimit_untrusted('job_post', job_text(state['job']))}\n\n"
        f"Draft:\n{draft.cover_letter}\n\nVerified claims:\n{claims}",
        _ModelCritique,
    )
    words = len(draft.cover_letter.split())
    limits = runtime.config.writing
    # A critic can echo an injected instruction, so its feedback is screened before reuse.
    reasons = [
        reason
        for reason in result.value.reasons
        if RiskFlag.INJECTION not in scan_job_post(reason).flags
    ]
    questions = state["job"].get("screening_questions", [])
    answered = len(draft.screening_answers) == len(questions) and all(
        item.answer.strip() for item in draft.screening_answers
    )
    if not answered:
        reasons.append(f"Give one answer for each of the {len(questions)} screening questions.")
    length_ok = limits.min_cover_letter_words <= words <= limits.max_cover_letter_words
    if not length_ok:
        reasons.append(
            f"Length is {words} words; keep it between {limits.min_cover_letter_words} "
            f"and {limits.max_cover_letter_words}."
        )
    personalised = result.value.personalised and not _GENERIC_OPENING.match(draft.cover_letter)
    if personalised != result.value.personalised:
        reasons.append("Replace the generic opening with something specific to this job.")
    grounded = (
        result.value.grounded
        and not state.get("needs_rewrite", False)
        and all(c.support is not ClaimSupport.UNSUPPORTED for c in draft.claims)
    )
    if grounded != result.value.grounded:
        reasons.append("Some of the draft could not be grounded in evidence; rewrite that part.")
    passed = grounded and personalised and length_ok and answered
    if not passed and not reasons:
        reasons.append("The draft needs improvement.")
    critique = Critique(
        job_id=draft.job_id,
        draft_version=draft.version,
        verdict=CritiqueVerdict.PASS if passed else CritiqueVerdict.REVISE,
        grounded=grounded,
        personalised=personalised,
        length_ok=length_ok,
        reasons=reasons,
    )
    return {"critiques": [critique], "tokens_used": result.tokens}
