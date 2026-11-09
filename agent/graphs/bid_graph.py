"""The bid graph: one durable thread per job, hand-written with LangGraph's StateGraph."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from agent.handoffs import BidAction, ComplianceAction, CritiqueVerdict
from agent.marketplace_port import current_trace_id
from agent.mcp_client import ToolCallError, ToolServerUnavailable
from agent.nodes import (
    analyst,
    approval,
    compliance,
    critic,
    decision,
    fit_matcher,
    pricing,
    risk_assessor,
    scout,
    verifier,
    writer,
)
from agent.runtime import AgentRuntime
from agent.state import ApprovalAction, BidState, NodeError, ThreadStatus

logger = logging.getLogger(__name__)
NodeFunction = Callable[[BidState, AgentRuntime], Awaitable[dict[str, Any]]]
_FAILED = {ThreadStatus.NEEDS_ATTENTION, ThreadStatus.DEAD_LETTER}
_ANALYSIS_NODES = ("analyst", "risk_assessor", "fit_matcher")


def _budget_problem(state: BidState, runtime: AgentRuntime) -> str | None:
    budgets = runtime.config.budgets
    if state.get("steps_used", 0) >= budgets.max_graph_steps:
        return f"Step budget of {budgets.max_graph_steps} was reached."
    if state.get("tokens_used", 0) >= budgets.max_tokens_per_job:
        return f"Token budget of {budgets.max_tokens_per_job} was reached."
    return None


def _guarded(name: str, function: NodeFunction, runtime: AgentRuntime) -> Callable[..., Any]:
    """Apply budgets, timeout and bounded retries, and dead-letter what cannot complete.

    An unavailable service is deliberately re-raised. The checkpoint stays at the last good
    step, so the worker can queue the thread and resume it when the service returns.
    """
    settings = runtime.config.graph

    async def node(state: BidState) -> dict[str, Any]:
        if state.get("status") in _FAILED:
            return {}
        problem = _budget_problem(state, runtime)
        if problem:
            return {"status": ThreadStatus.NEEDS_ATTENTION, "attention_reason": problem}
        trace = current_trace_id.set(state.get("trace_id"))
        try:
            failure: Exception | None = None
            for attempt in range(settings.node_max_attempts):
                try:
                    update = await asyncio.wait_for(
                        function(state, runtime), settings.node_timeout_seconds
                    )
                    return {**update, "steps_used": 1}
                except ToolServerUnavailable:
                    raise
                except (ToolCallError, PermissionError) as error:
                    failure = error
                    break
                except Exception as error:  # a bad model reply or a transient failure
                    failure = error
                    logger.warning("Node %s attempt %d failed: %r", name, attempt + 1, error)
                    if attempt + 1 < settings.node_max_attempts:
                        await asyncio.sleep(settings.node_backoff_seconds * (2**attempt))
            return {
                "status": ThreadStatus.DEAD_LETTER,
                "attention_reason": f"{name} could not complete.",
                "errors": [NodeError(node=name, message=repr(failure))],
                "steps_used": 1,
            }
        finally:
            current_trace_id.reset(trace)

    return node


def _failed(state: BidState) -> bool:
    return state.get("status") in _FAILED


def _after_scout(state: BidState) -> list[Send] | str:
    if _failed(state) or state.get("duplicate_of"):
        return END
    return [Send(name, state) for name in _ANALYSIS_NODES]


def _after_decision(state: BidState) -> str:
    if _failed(state) or state["decision"].action is not BidAction.BID:
        return END
    return "pricing"


def _after_pricing(state: BidState) -> str:
    return END if _failed(state) or state.get("status") is ThreadStatus.SKIPPED else "writer"


def _end_if_failed(next_node: str) -> Callable[[BidState], str]:
    def route(state: BidState) -> str:
        return END if _failed(state) else next_node

    return route


def _after_critic(max_revisions: int) -> Callable[[BidState], str]:
    def route(state: BidState) -> str:
        if _failed(state):
            return END
        revisions = state["critiques"][-1].draft_version - 1
        revise = state["critiques"][-1].verdict is CritiqueVerdict.REVISE
        return "writer" if revise and revisions < max_revisions else "compliance"

    return route


def _after_compliance(state: BidState) -> str:
    if _failed(state) or state["compliance_verdicts"][-1].action is ComplianceAction.BLOCK:
        return END
    return "prepare_approval"


def _after_approval(state: BidState) -> str:
    return {
        ApprovalAction.APPROVE: "submit",
        ApprovalAction.EDIT: "compliance",
        ApprovalAction.REJECT: END,
    }[state["approvals"][-1].action]


def build_bid_graph(
    runtime: AgentRuntime,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    graph = StateGraph(BidState)
    work: dict[str, NodeFunction] = {
        "scout": scout.run,
        "analyst": analyst.run,
        "risk_assessor": risk_assessor.run,
        "fit_matcher": fit_matcher.run,
        "reconcile": fit_matcher.reconcile,
        "decide": decision.run,
        "pricing": pricing.run,
        "writer": writer.run,
        "verify_claims": verifier.run,
        "critic": critic.run,
        "compliance": compliance.run,
        "prepare_approval": approval.prepare,
        "submit": approval.submit,
    }
    for name, function in work.items():
        graph.add_node(name, _guarded(name, function, runtime))
    graph.add_node("approval", approval.approval)

    graph.add_edge(START, "scout")
    graph.add_conditional_edges("scout", _after_scout, [*_ANALYSIS_NODES, END])
    for name in _ANALYSIS_NODES:
        graph.add_edge(name, "reconcile")
    graph.add_conditional_edges("reconcile", _end_if_failed("decide"), ["decide", END])
    graph.add_conditional_edges("decide", _after_decision, ["pricing", END])
    graph.add_conditional_edges("pricing", _after_pricing, ["writer", END])
    graph.add_conditional_edges("writer", _end_if_failed("verify_claims"), ["verify_claims", END])
    graph.add_conditional_edges("verify_claims", _end_if_failed("critic"), ["critic", END])
    graph.add_conditional_edges(
        "critic",
        _after_critic(runtime.config.budgets.max_revisions),
        ["writer", "compliance", END],
    )
    graph.add_conditional_edges("compliance", _after_compliance, ["prepare_approval", END])
    graph.add_conditional_edges("prepare_approval", _end_if_failed("approval"), ["approval", END])
    graph.add_conditional_edges("approval", _after_approval, ["submit", "compliance", END])
    graph.add_edge("submit", END)
    return graph.compile(checkpointer=checkpointer)
