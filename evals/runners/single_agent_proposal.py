"""Part A deliverable: one ReAct agent drafts a grounded proposal for one job, then it is checked.

Usage: python -m evals.runners.single_agent_proposal [--persona ai_ml_engineer] [--job dev-ai-0025]

The agent reaches the marketplace and portfolio only through the MCP servers. Claims are then
verified against the evidence the agent saw. Results: evals/results/single_agent_proposal_*.json
"""

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from agent.config import get_config
from agent.handoffs import ProposalDraft
from agent.llm import ModelRouter, create_chat_model
from agent.nodes.verifier import claim_judge
from agent.prompt_loader import load_prompt
from agent.single_agent import draft_proposal
from agent.usage import call_cost
from evals.harness import eval_config, start_eval_stack
from evals.runners.rag_eval import write_result
from rag.retriever import open_components
from rag.verify import recheck_final_text, verify_draft


async def run(arguments: argparse.Namespace) -> dict[str, Any]:
    load_dotenv()
    config = eval_config(get_config())
    spec = config.model_for("single_agent")[0]
    components = open_components(config)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as workdir:
        stack = start_eval_stack(Path(workdir), components, config, arguments.data / "marketplace")
        try:
            tools = await stack.toolbox(arguments.persona).tools_for("single_agent")
            draft = await draft_proposal(
                create_chat_model(spec),
                tools,
                load_prompt("single_agent"),
                arguments.job,
                arguments.max_steps,
            )
            judge = claim_judge(stack.runtime(arguments.persona, ModelRouter(config)), [0])
            limit = config.verification.max_evidence_per_claim
            checked = await asyncio.to_thread(
                verify_draft,
                ProposalDraft(job_id=arguments.job, cover_letter=draft.cover_letter),
                draft.evidence,
                judge,
                (),
                limit,
            )
            final = await asyncio.to_thread(
                recheck_final_text, checked.draft, draft.evidence, judge, (), limit
            )
        finally:
            stack.stop()
    components.close()
    result = {
        "data_source": arguments.source,
        "official_gigsim": False,
        "persona": arguments.persona,
        "job_id": arguments.job,
        "model": {"provider": spec.provider, "name": spec.model},
        "tools_called": draft.tool_calls,
        "evidence_seen": [item.evidence_id for item in draft.evidence],
        "draft": draft.cover_letter,
        "verified_draft": checked.draft.cover_letter,
        "verification": checked.report.model_dump(),
        "removed_claims": [claim.text for claim in checked.removed_claims],
        "final_text_check": final.model_dump(),
        "agent_input_tokens": draft.input_tokens,
        "agent_output_tokens": draft.output_tokens,
        "agent_cost_usd": call_cost(spec, draft.input_tokens, draft.output_tokens),
        "agent_latency_seconds": draft.latency_seconds,
    }
    write_result(f"single_agent_proposal_{arguments.source}.json", result)
    return result


def main() -> None:
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--persona", default="ai_ml_engineer")
    options.add_argument("--job", default="dev-ai-0025")
    options.add_argument("--max-steps", type=int, default=24)
    options.add_argument("--data", type=Path, default=Path("data/processed/devsim"))
    options.add_argument("--source", default="devsim")
    outcome = asyncio.run(run(options.parse_args()))
    print(json.dumps({k: outcome[k] for k in ("tools_called", "verification")}, indent=2))
    print(outcome["verified_draft"])


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    main()
