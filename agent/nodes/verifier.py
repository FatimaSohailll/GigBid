"""Claim verification: remove unsupported claims before the Critic sees the draft."""

import asyncio
from collections.abc import Mapping
from typing import Any

from agent.runtime import AgentRuntime
from agent.state import BidState
from rag.verify import (
    ClaimJudge,
    ClaimJudgement,
    FinalClaimCheck,
    LexicalJudge,
    LlmJudge,
    recheck_final_text,
    verify_draft,
)


def _claim_judge(runtime: AgentRuntime, tokens: list[int]) -> ClaimJudge:
    """The configured judge. Model calls return to the routed gateway from a worker thread."""
    settings = runtime.config.verification
    if settings.judge != "llm":
        return LexicalJudge(settings)
    loop = asyncio.get_running_loop()
    instructions = runtime.prompt("verifier")

    def ask(data: str) -> ClaimJudgement:
        call = runtime.llm.structured("verifier", instructions, data, ClaimJudgement)
        result = asyncio.run_coroutine_threadsafe(call, loop).result()
        tokens[0] += result.tokens
        return result.value

    return LlmJudge(ask)


def _terms(state: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """A skill the portfolio lacks can never be claimed. A shown skill may be left unnamed."""
    fit = state["fit_report"]
    return sorted(fit.missing_skills), [m.requirement for m in fit.matches if m.evidence_ids]


async def run(state: BidState, runtime: AgentRuntime) -> dict[str, Any]:
    vocabulary, shown = _terms(state)
    tokens = [0]
    result = await asyncio.to_thread(
        verify_draft,
        state["drafts"][-1],
        state.get("evidence", []),
        _claim_judge(runtime, tokens),
        vocabulary,
        runtime.config.verification.max_evidence_per_claim,
        shown,
    )
    return {
        "drafts": [result.draft],
        "verification_reports": [
            {
                **result.report.model_dump(),
                "removed_claims": [claim.model_dump() for claim in result.removed_claims],
                "draft_version": result.draft.version,
            }
        ],
        "needs_rewrite": result.needs_rewrite,
        "tokens_used": tokens[0],
    }


async def check_final_text(state: Mapping[str, Any], runtime: AgentRuntime) -> FinalClaimCheck:
    """Evaluation only: judge the final text with the same judge, from scratch."""
    vocabulary, shown = _terms(state)
    return await asyncio.to_thread(
        recheck_final_text,
        state["drafts"][-1],
        state.get("evidence", []),
        _claim_judge(runtime, [0]),
        vocabulary,
        runtime.config.verification.max_evidence_per_claim,
        shown,
    )
