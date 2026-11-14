"""One way to run a job, whichever system runs it.

The hand-built graph implements this now; the ReAct and supervisor baselines implement it for the
multi-agent comparison. Every runner is scored by the same code, so comparisons are like for like.
"""

import time
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from langgraph.types import Command

from agent.guardrails import defences_off
from agent.handoffs import RiskFlag
from agent.nodes.compliance import scan as compliance_scan
from agent.nodes.verifier import check_final_text
from agent.runtime import AgentRuntime
from agent.state import NodeUsage, ThreadStatus
from agent.tracing import run_config
from agent.usage import collect_model_calls
from evals.harness_state import initial_state, trace_id

_FAILED = {ThreadStatus.NEEDS_ATTENTION.value, ThreadStatus.DEAD_LETTER.value}


@dataclass
class JobResult:
    status: str
    decision: str | None = None
    flagged_scam: bool = False
    flagged_injection: bool = False
    price: float | None = None
    proposal_id: str | None = None
    cover_letter: str | None = None
    claims_before: int | None = None
    unsupported_before: int | None = None
    claims_after: int | None = None
    unsupported_after: int | None = None
    unsupported_final_claims: list[str] = field(default_factory=list)
    final_check_tokens: int = 0
    final_check_cost_usd: float | None = None
    compliance_caught: int = 0  # problems the design's own Compliance step found
    compliance_issues_in_output: int = 0  # problems the shared scanner finds in the text sent
    writes_attempted: list[str] = field(default_factory=list)
    tokens: int = 0
    cost_usd: float | None = None
    latency_seconds: float = 0.0
    usage: list[dict[str, Any]] = field(default_factory=list)
    job: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def as_row(self) -> dict[str, Any]:
        row = asdict(self)
        del row["job"], row["proposal_id"]
        return row


class JobRunner(Protocol):
    name: str

    async def decide(self, gold: dict[str, Any]) -> JobResult:
        """Run far enough to get a bid decision and a price, without drafting."""
        ...

    async def complete(self, gold: dict[str, Any]) -> JobResult:
        """Run to submission, approving automatically. Used only in evaluation."""
        ...


def total_cost(usage: list[NodeUsage]) -> float | None:
    costs = [item.cost_usd for item in usage if item.calls]
    if not costs or any(cost is None for cost in costs):
        return None
    return round(sum(cost or 0.0 for cost in costs), 6)


class GraphJobRunner:
    name = "graph"

    def __init__(self, graph: Any, runtime: AgentRuntime, defences: bool = True) -> None:
        self._graph = graph
        self._runtime = runtime
        self._config = runtime.config
        self._defences = defences

    def _thread(self, gold: dict[str, Any]) -> dict[str, Any]:
        thread_id = f"eval-{gold['persona']}-{gold['job_id']}"
        return run_config(
            self._config, thread_id, trace_id(gold), gold["persona"], "eval-bid-graph"
        )

    def _result(self, state: dict[str, Any], started: float) -> JobResult:
        usage: list[NodeUsage] = state.get("node_usage", [])
        risk = state.get("risk_report")
        flags = risk.flags if risk else []
        decision = state.get("decision")
        quote = state.get("price_quote")
        reports = state.get("verification_reports", [])
        drafts = state.get("drafts", [])
        status = ThreadStatus(state.get("status", ThreadStatus.RUNNING)).value
        counted = sum(item.input_tokens + item.output_tokens for item in usage)
        return JobResult(
            status=status,
            decision=decision.action.value if decision else None,
            flagged_scam=RiskFlag.SCAM in flags,
            flagged_injection=RiskFlag.INJECTION in flags,
            price=quote.amount if quote else None,
            proposal_id=state.get("proposal_id"),
            cover_letter=drafts[-1].cover_letter if drafts else None,
            claims_before=reports[0]["total_claims"] if reports else None,
            unsupported_before=reports[0]["unsupported_before"] if reports else None,
            tokens=counted or state.get("tokens_used", 0),
            cost_usd=total_cost(usage),
            latency_seconds=round(time.perf_counter() - started, 2),
            usage=[item.model_dump() for item in usage],
            job=state.get("job", {}),
            error=(state.get("attention_reason") or status) if status in _FAILED else None,
        )

    async def decide(self, gold: dict[str, Any]) -> JobResult:
        started = time.perf_counter()
        with nullcontext() if self._defences else defences_off():
            state = await self._graph.ainvoke(
                initial_state(gold), self._thread(gold), interrupt_before=["writer"]
            )
        result = self._result(state, started)
        if result.decision is None and result.error is None:
            result.error = "no decision reached"
        return result

    async def complete(self, gold: dict[str, Any]) -> JobResult:
        started = time.perf_counter()
        thread = self._thread(gold)
        with nullcontext() if self._defences else defences_off():
            state = await self._graph.ainvoke(initial_state(gold), thread)
            if "__interrupt__" in state:
                state = await self._graph.ainvoke(Command(resume={"action": "approve"}), thread)
        result = self._result(state, started)
        if state.get("drafts"):
            await self._check_final_text(state, result)
            result.compliance_caught = sum(
                len(verdict.violations) for verdict in state.get("compliance_verdicts", [])
            )
            result.compliance_issues_in_output = len(
                compliance_scan(state["drafts"][-1].cover_letter, self._runtime).findings
            )
        return result

    async def _check_final_text(self, state: dict[str, Any], result: JobResult) -> None:
        """Measure claims in the text that went out. Its cost is reported, not added to the job."""
        with collect_model_calls() as calls:
            check = await check_final_text(state, self._runtime)
        result.claims_after = check.total_claims
        result.unsupported_after = check.unsupported
        result.unsupported_final_claims = check.unsupported_claims
        result.final_check_tokens = sum(c.input_tokens + c.output_tokens for c in calls)
        costs = [call.cost_usd for call in calls]
        if costs and all(cost is not None for cost in costs):
            result.final_check_cost_usd = round(sum(c or 0.0 for c in costs), 6)
