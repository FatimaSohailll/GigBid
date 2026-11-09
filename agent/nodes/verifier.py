"""Claim verification: remove unsupported claims before the Critic sees the draft."""

import asyncio
from typing import Any

from agent.runtime import AgentRuntime
from agent.state import BidState
from rag.verify import ClaimJudge, ClaimJudgement, LexicalJudge, LlmJudge, verify_draft


async def run(state: BidState, runtime: AgentRuntime) -> dict[str, Any]:
    fit = state["fit_report"]
    settings = runtime.config.verification
    # A skill the portfolio lacks can never be claimed. A shown skill may be unnamed in the text.
    vocabulary = sorted(fit.missing_skills)
    shown = [match.requirement for match in fit.matches if match.evidence_ids]
    loop = asyncio.get_running_loop()
    instructions = runtime.prompt("verifier")
    tokens = [0]

    def ask(data: str) -> ClaimJudgement:
        # Verification runs in a worker thread; model calls go back through the routed gateway.
        call = runtime.llm.structured("verifier", instructions, data, ClaimJudgement)
        result = asyncio.run_coroutine_threadsafe(call, loop).result()
        tokens[0] += result.tokens
        return result.value

    judge: ClaimJudge = LlmJudge(ask) if settings.judge == "llm" else LexicalJudge(settings)
    result = await asyncio.to_thread(
        verify_draft,
        state["drafts"][-1],
        state.get("evidence", []),
        judge,
        vocabulary,
        settings.max_evidence_per_claim,
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
