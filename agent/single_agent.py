"""One ReAct agent that drafts a grounded proposal through MCP tools only (Part A deliverable).

The same function is design (a) in R&D question C, where it is given every tool instead.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

from agent.handoffs import EvidenceItem
from agent.prompt_loader import load_prompt


@dataclass
class AgentDraft:
    cover_letter: str
    evidence: list[EvidenceItem]
    tool_calls: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_seconds: float = 0.0


def _payload(message: ToolMessage) -> Any:
    content = message.content
    text = (
        content
        if isinstance(content, str)
        else "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    )
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def evidence_from(messages: list[BaseMessage]) -> list[EvidenceItem]:
    """The evidence the agent actually saw, so its claims are checked against exactly that."""
    found: dict[str, EvidenceItem] = {}
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        data = _payload(message)
        if not isinstance(data, dict):
            continue
        if message.name == "search_evidence":
            for item in data.get("items", []):
                found.setdefault(item["evidence_id"], EvidenceItem.model_validate(item))
        elif message.name == "get_case_study" and "id" in data:
            found[data["id"]] = EvidenceItem(
                evidence_id=data["id"],
                type="case_study",
                source=data.get("source", ""),
                excerpt=data.get("content", ""),
                date=data.get("date"),
            )
    return list(found.values())


async def draft_proposal(
    model: BaseChatModel,
    tools: list[BaseTool],
    instructions: str,
    job_id: str,
    max_steps: int,
) -> AgentDraft:
    agent = create_agent(model, tools, system_prompt=instructions)
    request = load_prompt("single_agent_request").strip().replace("{job_id}", job_id)
    started = time.perf_counter()
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=request)]},
        {"recursion_limit": max_steps},
    )
    messages: list[BaseMessage] = result["messages"]
    replies = [m for m in messages if isinstance(m, AIMessage)]
    final = next((m for m in reversed(replies) if not m.tool_calls), None)
    usage: list[dict[str, Any]] = [dict(m.usage_metadata or {}) for m in replies]
    return AgentDraft(
        cover_letter=final.text.strip() if final else "",
        evidence=evidence_from(messages),
        tool_calls=[call["name"] for m in replies for call in m.tool_calls],
        input_tokens=sum(int(u.get("input_tokens", 0)) for u in usage),
        output_tokens=sum(int(u.get("output_tokens", 0)) for u in usage),
        latency_seconds=round(time.perf_counter() - started, 2),
    )
