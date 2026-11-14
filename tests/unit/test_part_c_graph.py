from pathlib import Path
from typing import Any

import pytest

from agent.config import get_config
from agent.handoffs import EvidenceItem, ProposalDraft
from agent.llm import ModelRouter
from agent.mcp_client import ToolCallError
from agent.nodes import approval, critic
from agent.nodes.pricing import price_basis
from agent.runtime import AgentRuntime
from agent.state import ApprovalAction, ApprovalRecord, ThreadStatus
from apps.api.routers.proposals import current_evidence
from evals.harness import eval_config
from rag.ingest.pipeline import ingest_documents, remove_item
from tests.support.rag_fakes import document, memory_components


class Tools:
    def __init__(self, missing: set[str] = frozenset(), confirm: bool = False) -> None:  # type: ignore[assignment]
        self.missing = missing
        self.confirm = confirm
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def call(self, agent: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((agent, tool, arguments))
        if tool == "get_case_study":
            if arguments["id"] in self.missing:
                raise ToolCallError("not_found", "gone")
            return {"content": "Built a churn model in Python."}
        if tool == "create_proposal_draft":
            return {"proposal": {"proposal_id": "p1", "status": "drafted"}}
        if tool == "confirm_proposal" and arguments["request"].get("dry_run"):
            return {"preview": {"connects_cost": 16, "connects_balance": 80, "blockers": []}}
        return {
            "proposal": {
                "proposal_id": "p1",
                "status": "drafted" if self.confirm else "submitted",
                "connects_spent": 0 if self.confirm else 8,
                "requires_platform_confirmation": self.confirm,
            }
        }


class Llm:
    def __init__(self) -> None:
        self.data = ""

    async def structured(self, node: str, instructions: str, data: str, schema: Any) -> Any:
        from agent.llm import LlmResult

        self.data = data
        return LlmResult(schema(grounded=True, personalised=True, reasons=[]), 1, "m")


def critic_state(evidence_ids: list[str]) -> dict[str, Any]:
    letter = " ".join(["Your churn problem is a ranking problem."] * 8)
    return {
        "job": {
            "job_id": "j",
            "title": "T",
            "description": "D",
            "budget": {},
            "screening_questions": [],
        },
        "drafts": [ProposalDraft(job_id="j", cover_letter=letter, evidence_ids=evidence_ids)],
    }


async def test_critic_reads_cited_case_studies_from_portfolio() -> None:
    tools, llm = Tools(), Llm()
    runtime = AgentRuntime(get_config(), llm, tools, None)  # type: ignore[arg-type]
    update = await critic.run(critic_state(["case:me:churn", "code:me:x"]), runtime)  # type: ignore[arg-type]
    assert [call[1:] for call in tools.calls] == [("get_case_study", {"id": "case:me:churn"})]
    assert "Built a churn model" in llm.data
    assert update["critiques"][0].grounded is True


async def test_critic_sends_back_a_draft_citing_deleted_evidence() -> None:
    runtime = AgentRuntime(get_config(), Llm(), Tools(missing={"case:me:churn"}), None)  # type: ignore[arg-type]
    update = await critic.run(critic_state(["case:me:churn"]), runtime)  # type: ignore[arg-type]
    critique = update["critiques"][0]
    assert critique.grounded is False
    assert any("no longer in the portfolio" in reason for reason in critique.reasons)


def test_regeneration_drops_evidence_the_user_deleted(tmp_path: Path) -> None:
    components = memory_components(tmp_path)
    ingest_documents([document("case:a", "kafka"), document("case:b", "dbt")], "u1", components)
    remove_item("u1", "case:b", components)
    evidence = [
        EvidenceItem(evidence_id=item, type="case_study", source="s", excerpt="x")
        for item in ("case:a", "case:b")
    ]
    kept, missing = current_evidence(evidence, components.corpus, "u1")
    assert [item.evidence_id for item in kept] == ["case:a"]
    assert missing == ["case:b"]
    assert current_evidence(evidence, None, "u1") == (evidence, [])


def submit_state() -> dict[str, Any]:
    from datetime import UTC, datetime

    return {
        "user_id": "u",
        "job": {"job_id": "j"},
        "drafts": [ProposalDraft(job_id="j", cover_letter="Letter", bid_amount=100)],
        "approvals": [
            ApprovalRecord(action=ApprovalAction.APPROVE, user_id="u", decided_at=datetime.now(UTC))
        ],
    }


@pytest.mark.parametrize(
    ("confirm", "status"),
    [(False, ThreadStatus.SUBMITTED), (True, ThreadStatus.AWAITING_PLATFORM_CONFIRMATION)],
)
async def test_submission_status_follows_the_marketplace(
    confirm: bool, status: ThreadStatus
) -> None:
    runtime = AgentRuntime(get_config(), None, Tools(confirm=confirm), None)  # type: ignore[arg-type]
    update = await approval.submit(submit_state(), runtime)  # type: ignore[arg-type]
    assert update["status"] is status
    assert ("platform_preview" in update) is confirm


def test_price_rationale_says_why_the_price_was_chosen() -> None:
    assert "fits" in price_basis(1000, 800, 1800)
    assert "outside" in price_basis(100, 800, 1800)
    assert "No market data" in price_basis(None, 800, 1800)


def test_the_fallback_model_is_open_and_not_a_missing_local_model() -> None:
    fallback = get_config().model_for("analyst")[1]
    assert fallback is not None
    assert fallback.provider == "groq"


def test_evaluations_never_fall_back() -> None:
    assert get_config().llm.allow_fallback is True
    assert eval_config(get_config()).llm.allow_fallback is False


async def test_disabled_fallback_raises_instead_of_switching_models() -> None:
    used: list[str] = []

    def factory(spec: Any) -> Any:
        used.append(spec.model)
        raise RuntimeError("primary is down")

    config = eval_config(get_config())
    with pytest.raises(RuntimeError):
        await ModelRouter(config, factory=factory).structured("analyst", "i", "d", Any)  # type: ignore[arg-type]
    assert used == [config.model_for("analyst")[0].model]
