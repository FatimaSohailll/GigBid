"""The two comparison designs for R&D question C, behind the same JobRunner as the graph.

(a) One ReAct agent holding every tool: the Part A agent setup, given all tools.
(b) A prebuilt supervisor (langgraph-supervisor) over one ReAct worker per role.

Neither design has its own claim verifier or Compliance Guard. Both are scored by the same checks as
the graph: the final-text claim check and the deterministic outgoing-text scanner. As with the
graph in evaluation, approval is given automatically, and the harness submits a bid.
"""

import time
from typing import Any, Literal

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import BaseTool
from langgraph_supervisor import create_supervisor
from pydantic import BaseModel, ConfigDict, Field

from agent.config import AppConfig, ModelSpec
from agent.guardrails import scan_outgoing_text
from agent.handoffs import ProposalDraft
from agent.nodes.verifier import claim_judge
from agent.prompt_loader import PROMPT_DIR, load_prompt
from agent.runtime import AgentRuntime
from agent.single_agent import evidence_from
from agent.usage import call_cost
from evals.job_runner import JobResult
from mcp_servers.tool_descriptions import TOOL_DESCRIPTIONS
from rag.verify import recheck_final_text

WRITE_TOOLS = {"create_proposal_draft", "submit_proposal", "send_message"}
ROLES = ("analyst", "risk_assessor", "fit_matcher", "pricing", "writer", "critic", "compliance")


class FinalAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["bid", "skip"]
    scam: bool = False
    price: float | None = Field(default=None, gt=0)
    cover_letter: str | None = None
    screening_answers: list[str] = Field(default_factory=list)
    reasons: str = ""


def with_baseline_tools(config: AppConfig) -> AppConfig:
    """Give design (a) every tool, in evaluation only.

    Production config keeps spending and sending tools on the submission step alone; a test
    enforces that. The baseline exists only to be measured.
    """
    every = sorted(TOOL_DESCRIPTIONS)  # GigBid's own 18 tools; third-party tools excluded
    return config.model_copy(
        update={"agent_tools": {**config.agent_tools, "react_baseline": every}}
    )


def _request(gold: dict[str, Any]) -> list[BaseMessage]:
    text = load_prompt("baseline_request").strip().replace("{job_id}", gold["job_id"])
    return [HumanMessage(content=text)]


def _priced_models(config: AppConfig) -> dict[str, ModelSpec]:
    profile = config.models.profiles[config.models.active_profile]
    return {spec.model: spec for spec in profile.tiers.values()}


class BaselineRunner:
    """Shared scoring and submission. Subclasses only build and call their agent."""

    name = "baseline"

    def __init__(self, runtime: AgentRuntime, seed_key: str) -> None:
        self._runtime = runtime
        self._seed_key = seed_key
        self._prices = _priced_models(runtime.config)

    async def _answer(self, gold: dict[str, Any]) -> tuple[FinalAnswer | None, list[BaseMessage]]:
        raise NotImplementedError

    def _usage(self, messages: list[BaseMessage]) -> tuple[int, float | None, list[str]]:
        tokens, costs, writes = 0, [], []
        for message in messages:
            if not isinstance(message, AIMessage):
                continue
            usage: dict[str, Any] = dict(message.usage_metadata or {})
            used_in, used_out = (
                int(usage.get("input_tokens", 0)),
                int(usage.get("output_tokens", 0)),
            )
            tokens += used_in + used_out
            spec = self._prices.get(str(message.response_metadata.get("model_name", "")))
            costs.append(call_cost(spec, used_in, used_out) if spec else None)
            writes += [call["name"] for call in message.tool_calls if call["name"] in WRITE_TOOLS]
        known = costs and all(cost is not None for cost in costs)
        return tokens, round(sum(c or 0.0 for c in costs), 6) if known else None, writes

    async def _run(self, gold: dict[str, Any], submit: bool) -> JobResult:
        started = time.perf_counter()
        answer, messages = await self._answer(gold)
        tokens, cost, writes = self._usage(messages)
        result = JobResult(
            status="failed" if answer is None else "skipped",
            decision=answer.decision if answer else None,
            flagged_scam=bool(answer and answer.scam),
            price=answer.price if answer else None,
            cover_letter=answer.cover_letter if answer else None,
            tokens=tokens,
            cost_usd=cost,
            writes_attempted=writes,
            error=None if answer else "no structured answer",
        )
        if answer and answer.decision == "bid" and answer.cover_letter:
            await self._check(answer, messages, result)
            if submit:
                await self._submit(gold, answer, result)
            else:
                result.status = "drafted"
        result.latency_seconds = round(time.perf_counter() - started, 2)
        return result

    async def _check(
        self, answer: FinalAnswer, messages: list[BaseMessage], result: JobResult
    ) -> None:
        import asyncio

        letter = answer.cover_letter or ""
        check = await asyncio.to_thread(
            recheck_final_text,
            ProposalDraft(job_id="baseline", cover_letter=letter),
            evidence_from(messages),
            claim_judge(self._runtime, [0]),
            (),
            self._runtime.config.verification.max_evidence_per_claim,
        )
        result.claims_after = check.total_claims
        result.unsupported_after = check.unsupported
        result.unsupported_final_claims = check.unsupported_claims
        result.compliance_issues_in_output = len(scan_outgoing_text(letter).findings)

    async def _submit(self, gold: dict[str, Any], answer: FinalAnswer, result: JobResult) -> None:
        key = f"rqc-{self.name}-{self._seed_key}-{gold['job_id']}"
        created = await self._runtime.tools.call(
            "submission",
            "create_proposal_draft",
            {
                "request": {
                    "job_id": gold["job_id"],
                    "cover_letter": answer.cover_letter,
                    "screening_answers": answer.screening_answers,
                    "bid_amount": answer.price or 1.0,
                    "idempotency_key": f"{key}-draft",
                }
            },
        )
        proposal_id = created["proposal"]["proposal_id"]
        await self._runtime.tools.call(
            "submission",
            "submit_proposal",
            {"request": {"proposal_id": proposal_id, "idempotency_key": f"{key}-submit"}},
        )
        result.status, result.proposal_id = "submitted", proposal_id

    async def decide(self, gold: dict[str, Any]) -> JobResult:
        return await self._run(gold, submit=False)

    async def complete(self, gold: dict[str, Any]) -> JobResult:
        return await self._run(gold, submit=True)


class ReactJobRunner(BaselineRunner):
    """(a) One ReAct agent holding every tool."""

    name = "react"

    def __init__(
        self,
        runtime: AgentRuntime,
        tools: list[BaseTool],
        model: BaseChatModel,
        seed_key: str,
        max_steps: int = 40,
    ) -> None:
        super().__init__(runtime, seed_key)
        self._agent: Any = create_agent(
            model, tools, system_prompt=load_prompt("react_baseline"), response_format=FinalAnswer
        )
        self._max_steps = max_steps

    async def _answer(self, gold: dict[str, Any]) -> tuple[FinalAnswer | None, list[BaseMessage]]:
        state = await self._agent.ainvoke(
            {"messages": _request(gold)}, {"recursion_limit": self._max_steps}
        )
        return state.get("structured_response"), state["messages"]


def worker_prompt(role: str) -> str:
    """The role's own node prompt where one exists, after the shared worker instructions."""
    own = PROMPT_DIR / f"{role}.v1.md"
    detail = load_prompt(role) if own.exists() else role.replace("_", " ")
    return f"{load_prompt('baseline_worker').rstrip()} {role}\n\n{detail}"


class SupervisorJobRunner(BaselineRunner):
    """(b) A prebuilt supervisor over one ReAct worker per role, each with that role's tools."""

    name = "supervisor"

    def __init__(
        self,
        runtime: AgentRuntime,
        tools_by_role: dict[str, list[BaseTool]],
        models: dict[str, BaseChatModel],
        lead: BaseChatModel,
        seed_key: str,
        max_steps: int = 80,
    ) -> None:
        super().__init__(runtime, seed_key)
        workers = [
            create_agent(
                models[role], tools_by_role[role], system_prompt=worker_prompt(role), name=role
            )
            for role in ROLES
        ]
        self.graph: Any = create_supervisor(
            workers,  # type: ignore[arg-type]
            model=lead,
            prompt=load_prompt("supervisor"),
            response_format=FinalAnswer,
            output_mode="full_history",
        ).compile()
        self._max_steps = max_steps

    async def _answer(self, gold: dict[str, Any]) -> tuple[FinalAnswer | None, list[BaseMessage]]:
        state = await self.graph.ainvoke(
            {"messages": _request(gold)}, {"recursion_limit": self._max_steps}
        )
        return state.get("structured_response"), state["messages"]
