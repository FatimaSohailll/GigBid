"""The bid graph: one durable thread per job, hand-written with LangGraph's StateGraph."""

from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from agent.graphs.common import NodeFunction, end_if_failed, failed, guarded
from agent.handoffs import BidAction, ComplianceAction, CritiqueVerdict
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
from agent.state import ApprovalAction, BidState, ThreadStatus

_ANALYSIS_NODES = ("analyst", "risk_assessor", "fit_matcher")


def _after_scout(state: BidState) -> list[Send] | str:
    if failed(state) or state.get("duplicate_of"):
        return END
    return [Send(name, state) for name in _ANALYSIS_NODES]


def _after_decision(state: BidState) -> str:
    if failed(state) or state["decision"].action is not BidAction.BID:
        return END
    return "pricing"


def _after_pricing(state: BidState) -> str:
    # Compare by value: a status restored from Postgres can come back as a plain string.
    return END if failed(state) or state.get("status") == ThreadStatus.SKIPPED else "writer"


def _after_critic(max_revisions: int) -> Callable[[BidState], str]:
    def route(state: BidState) -> str:
        if failed(state):
            return END
        revisions = state["critiques"][-1].draft_version - 1
        revise = state["critiques"][-1].verdict is CritiqueVerdict.REVISE
        return "writer" if revise and revisions < max_revisions else "compliance"

    return route


def _after_compliance(state: BidState) -> str:
    if failed(state) or state["compliance_verdicts"][-1].action is ComplianceAction.BLOCK:
        return END
    return "prepare_approval"


def _after_approval(state: BidState) -> str:
    return {
        ApprovalAction.APPROVE: "submit",
        ApprovalAction.EDIT: "compliance",
        ApprovalAction.REJECT: "learn",
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
        "learn": approval.learn,
    }
    for name, function in work.items():
        graph.add_node(name, guarded(name, function, runtime))
    graph.add_node("approval", approval.approval)

    graph.add_edge(START, "scout")
    graph.add_conditional_edges("scout", _after_scout, [*_ANALYSIS_NODES, END])
    for name in _ANALYSIS_NODES:
        graph.add_edge(name, "reconcile")
    graph.add_conditional_edges("reconcile", end_if_failed("decide"), ["decide", END])
    graph.add_conditional_edges("decide", _after_decision, ["pricing", END])
    graph.add_conditional_edges("pricing", _after_pricing, ["writer", END])
    graph.add_conditional_edges("writer", end_if_failed("verify_claims"), ["verify_claims", END])
    graph.add_conditional_edges("verify_claims", end_if_failed("critic"), ["critic", END])
    graph.add_conditional_edges(
        "critic",
        _after_critic(runtime.config.budgets.max_revisions),
        ["writer", "compliance", END],
    )
    graph.add_conditional_edges("compliance", _after_compliance, ["prepare_approval", END])
    graph.add_conditional_edges("prepare_approval", end_if_failed("approval"), ["approval", END])
    graph.add_conditional_edges("approval", _after_approval, ["submit", "compliance", "learn"])
    graph.add_conditional_edges("submit", end_if_failed("learn"), ["learn", END])
    graph.add_edge("learn", END)
    return graph.compile(checkpointer=checkpointer)
