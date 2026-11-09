"""The LLM claim judge inside a real graph run, with its tokens counted against the budget."""

from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from agent.checkpoint import checkpoint_serde
from agent.graphs.bid_graph import build_bid_graph
from agent.runtime import AgentRuntime
from agent.state import ThreadStatus, UserSettings
from tests.support.graph_stack import USER, fast_config, new_memory, start_stack
from tests.support.scripted_llm import ScriptedLlm


async def test_llm_judge_runs_through_the_router_and_counts_tokens(tmp_path: Path) -> None:
    stack = start_stack(tmp_path)
    try:
        base = fast_config()
        config = base.model_copy(
            update={"verification": base.verification.model_copy(update={"judge": "llm"})}
        )
        llm = ScriptedLlm()
        graph = build_bid_graph(
            AgentRuntime(config, llm, stack.gateway(config), new_memory(config)),
            InMemorySaver(serde=checkpoint_serde()),
        )
        result = await graph.ainvoke(
            {
                "job_id": "job-001",
                "user_id": USER,
                "trace_id": "t",
                "settings": UserSettings(floor_usd=200, weekly_connects_budget=50),
            },
            {"configurable": {"thread_id": "llm-judge"}},
        )
    finally:
        stack.stop()
    assert result["status"] is ThreadStatus.AWAITING_APPROVAL
    assert llm.calls["verifier"] >= 1
    assert result["tokens_used"] == sum(llm.calls.values()) * llm.tokens_per_call
