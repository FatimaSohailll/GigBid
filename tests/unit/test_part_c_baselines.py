import json
from collections.abc import Sequence
from typing import Any

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agent.config import get_config
from agent.runtime import AgentRuntime
from evals import baselines
from evals.baselines import (
    ROLES,
    FinalAnswer,
    ReactJobRunner,
    SupervisorJobRunner,
    with_baseline_tools,
    worker_prompt,
)
from evals.runners.orchestration_compare import summarise
from mcp_servers.tool_descriptions import TOOL_DESCRIPTIONS

GOLD = {"job_id": "dev-ai-0001", "persona": "ai_ml_engineer"}
LETTER = "Your churn problem needs ranking. I built a churn model in Python [case:me:churn]."


class Tools:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call(self, agent: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(tool)
        return {"proposal": {"proposal_id": "prop-1", "status": "submitted"}}


def runtime(tools: Tools) -> AgentRuntime:
    base = get_config()
    config = base.model_copy(
        update={"verification": base.verification.model_copy(update={"judge": "lexical"})}
    )
    return AgentRuntime(config, None, tools, None)  # type: ignore[arg-type]


def agent_messages(write_tool: str | None = None) -> list[BaseMessage]:
    evidence = {
        "items": [
            {
                "evidence_id": "case:me:churn",
                "type": "case_study",
                "source": "c.md",
                "excerpt": "Built a churn model in Python.",
            }
        ]
    }
    calls = [{"name": "search_evidence", "args": {"requirement": "churn"}, "id": "c1"}]
    if write_tool:
        calls.append({"name": write_tool, "args": {}, "id": "c2"})
    return [
        AIMessage(
            content="",
            tool_calls=calls,
            usage_metadata={"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100},
            response_metadata={"model_name": "openai/gpt-oss-120b"},
        ),
        ToolMessage(content=json.dumps(evidence), tool_call_id="c1", name="search_evidence"),
    ]


class FakeAgent:
    def __init__(self, answer: FinalAnswer | None, messages: list[BaseMessage]) -> None:
        self.state = {"structured_response": answer, "messages": messages}

    async def ainvoke(self, *_: Any, **__: Any) -> dict[str, Any]:
        return self.state


def react(
    monkeypatch: pytest.MonkeyPatch, answer: FinalAnswer | None, write_tool: str | None = None
) -> tuple[ReactJobRunner, Tools]:
    monkeypatch.setattr(
        baselines, "create_agent", lambda *_, **__: FakeAgent(answer, agent_messages(write_tool))
    )
    tools = Tools()
    return ReactJobRunner(runtime(tools), [], None, "s1"), tools  # type: ignore[arg-type]


async def test_a_bid_is_checked_scanned_and_submitted_once(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = FinalAnswer(decision="bid", price=900, cover_letter=LETTER + " I guarantee results.")
    runner, tools = react(monkeypatch, answer)
    result = await runner.complete(GOLD)
    assert result.status == "submitted" and result.proposal_id == "prop-1"
    assert tools.calls == ["create_proposal_draft", "submit_proposal"]
    assert result.claims_after is not None
    assert result.compliance_issues_in_output == 1
    assert result.tokens == 1100
    assert result.cost_usd == pytest.approx((1000 * 0.15 + 100 * 0.60) / 1_000_000)


async def test_a_skip_submits_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, tools = react(monkeypatch, FinalAnswer(decision="skip", scam=True))
    result = await runner.complete(GOLD)
    assert (result.status, result.decision, result.flagged_scam) == ("skipped", "skip", True)
    assert tools.calls == []


async def test_deciding_only_never_submits(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, tools = react(monkeypatch, FinalAnswer(decision="bid", price=500, cover_letter=LETTER))
    result = await runner.decide(GOLD)
    assert result.status == "drafted"
    assert tools.calls == []


async def test_a_missing_answer_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, _ = react(monkeypatch, None)
    result = await runner.complete(GOLD)
    assert result.error == "no structured answer"


async def test_writes_the_agent_tried_on_its_own_are_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, _ = react(monkeypatch, FinalAnswer(decision="skip"), write_tool="submit_proposal")
    assert (await runner.complete(GOLD)).writes_attempted == ["submit_proposal"]


def test_the_single_agent_holds_every_tool_only_in_evaluation() -> None:
    assert "react_baseline" not in get_config().agent_tools
    every = with_baseline_tools(get_config()).tools_for("react_baseline")
    assert set(every) == set(TOOL_DESCRIPTIONS)


def test_workers_get_their_role_prompt_or_a_plain_role() -> None:
    assert "freelance proposal" in worker_prompt("writer")
    assert worker_prompt("pricing").rstrip().endswith("pricing")


class ScriptedChat(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedChat":
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])


def test_the_supervisor_has_one_worker_per_role() -> None:
    models = {role: ScriptedChat() for role in ROLES}
    runner = SupervisorJobRunner(
        runtime(Tools()), {role: [] for role in ROLES}, models, ScriptedChat(), "s1"
    )
    assert set(ROLES) | {"supervisor"} <= set(runner.graph.get_graph().nodes)


def row(**values: Any) -> dict[str, Any]:
    return {
        "job_type": "good_fit",
        "gold_bid": True,
        "decision": "bid",
        "submitted": True,
        "replied": True,
        "claims_after": 2,
        "unsupported_after": 1,
        "compliance_caught": 1,
        "compliance_issues_in_output": 0,
        "writes_attempted": [],
        "tokens": 100,
        "cost_usd": 0.01,
        "latency_seconds": 1.0,
        "error": None,
        **values,
    }


def test_comparison_metrics_cover_the_assignment_list() -> None:
    summary = summarise(
        [row(), row(gold_bid=False, decision="skip", submitted=False, replied=False)]
    )
    assert summary["bid_or_skip_accuracy"] == 1
    assert summary["unsupported_claims_per_proposal"] == 1
    assert summary["compliance_caught"] == 2
    assert summary["reply_rate"] == 1
    assert summary["tokens_per_job"] == 100
