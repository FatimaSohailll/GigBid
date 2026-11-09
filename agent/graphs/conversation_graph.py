"""The conversation graph: one durable thread per client conversation."""

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent.graphs.common import NodeFunction, end_if_failed, failed, guarded
from agent.handoffs import ClientIntent, ComplianceAction
from agent.nodes import client_liaison
from agent.runtime import AgentRuntime
from agent.state import ApprovalAction, ConversationState


def _after_load(state: ConversationState) -> str:
    return END if failed(state) or not state.get("latest_message") else "classify"


def _after_classify(state: ConversationState) -> str:
    if failed(state) or state["intent"].intent is ClientIntent.SCAM_SIGNAL:
        return END
    return "find_slots" if state["intent"].intent is ClientIntent.INTERVIEW_REQUEST else "draft"


def _after_compliance(state: ConversationState) -> str:
    if failed(state) or state["compliance_verdicts"][-1].action is ComplianceAction.BLOCK:
        return END
    return "prepare_approval"


def _after_approval(state: ConversationState) -> str:
    return {
        ApprovalAction.APPROVE: "send",
        ApprovalAction.EDIT: "compliance",
        ApprovalAction.REJECT: "learn",
    }[state["approvals"][-1].action]


def build_conversation_graph(
    runtime: AgentRuntime,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    graph = StateGraph(ConversationState)
    work: dict[str, NodeFunction] = {
        "load": client_liaison.load,
        "classify": client_liaison.classify,
        "find_slots": client_liaison.find_slots,
        "draft": client_liaison.draft,
        "compliance": client_liaison.compliance,
        "prepare_approval": client_liaison.prepare,
        "send": client_liaison.send,
        "learn": client_liaison.learn,
    }
    for name, function in work.items():
        graph.add_node(name, guarded(name, function, runtime))
    graph.add_node("approval", client_liaison.approval)

    graph.add_edge(START, "load")
    graph.add_conditional_edges("load", _after_load, ["classify", END])
    graph.add_conditional_edges("classify", _after_classify, ["find_slots", "draft", END])
    graph.add_conditional_edges("find_slots", end_if_failed("draft"), ["draft", END])
    graph.add_conditional_edges("draft", end_if_failed("compliance"), ["compliance", END])
    graph.add_conditional_edges("compliance", _after_compliance, ["prepare_approval", END])
    graph.add_conditional_edges("prepare_approval", end_if_failed("approval"), ["approval", END])
    graph.add_conditional_edges("approval", _after_approval, ["send", "compliance", "learn"])
    graph.add_conditional_edges("send", end_if_failed("learn"), ["learn", END])
    graph.add_edge("learn", END)
    return graph.compile(checkpointer=checkpointer)
