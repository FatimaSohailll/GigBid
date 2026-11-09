from pathlib import Path
from typing import Any

import pytest
import yaml
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import ValidationError

from agent import handoffs as h
from agent.config import CONFIG_PATH, REQUIRED_NODES, load_config
from agent.prompt_loader import load_prompt
from agent.state import BidState, NodeError, ThreadStatus, merge_evidence


def evidence(item_id: str, excerpt: str = "text") -> h.EvidenceItem:
    return h.EvidenceItem(evidence_id=item_id, type="case_study", source="s", excerpt=excerpt)


def test_all_required_handoffs_exist() -> None:
    required = (
        "JobSpec",
        "RiskReport",
        "FitReport",
        "BidDecision",
        "PriceQuote",
        "ProposalDraft",
        "Critique",
        "ComplianceVerdict",
    )
    assert all(issubclass(getattr(h, name), h.BaseModel) for name in required)


def test_jobspec_has_every_extracted_field() -> None:
    assert {
        "deliverables",
        "required_skills",
        "budget_min",
        "budget_max",
        "timeline_days",
        "screening_questions",
        "hidden_needs",
    } <= set(h.JobSpec.model_fields)


@pytest.mark.parametrize(
    ("model", "values"),
    [
        (h.JobSpec, {"job_id": "j", "title": "t", "budget_min": 10, "budget_max": 5}),
        (h.RiskReport, {"score": 1.5, "explanation": "x"}),
        (h.RiskReport, {"score": 0.5, "explanation": "x", "flags": ["not_a_flag"]}),
        (h.FitReport, {"score": -0.1}),
        (h.RequirementMatch, {"requirement": "r", "status": "covered"}),
        (
            h.BidDecision,
            {"job_id": "j", "action": "skip", "fit_score": 0, "risk_score": 0, "reasons": []},
        ),
        (
            h.PriceQuote,
            {
                "job_id": "j",
                "amount": 50,
                "floor": 100,
                "connects_cost": 1,
                "connects_worth_it": True,
                "rationale": "r",
            },
        ),
        (h.ProposalClaim, {"claim_id": "c", "text": "t", "support": "supported"}),
        (
            h.Critique,
            {
                "job_id": "j",
                "draft_version": 1,
                "verdict": "revise",
                "grounded": False,
                "personalised": True,
                "length_ok": True,
            },
        ),
        (h.ComplianceVerdict, {"job_id": "j", "draft_version": 1, "action": "block"}),
        (h.ComplianceVerdict, {"job_id": "j", "draft_version": 1, "action": "redacted"}),
        (h.JobSpec, {"job_id": "j", "title": "t", "unexpected": 1}),
    ],
)
def test_invalid_handoffs_are_rejected(model: type[h.BaseModel], values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(values)


def test_unsupported_claim_needs_no_evidence() -> None:
    assert h.ProposalClaim(claim_id="c", text="t", support="unsupported").evidence_ids == []


def test_merge_evidence_deduplicates_and_keeps_latest() -> None:
    merged = merge_evidence([evidence("a", "old"), evidence("b")], [evidence("a", "new")])
    assert {item.evidence_id: item.excerpt for item in merged} == {"a": "new", "b": "text"}
    assert merge_evidence(None, None) == []


def test_parallel_branches_merge_through_real_graph_and_checkpoint() -> None:
    def fan_out(_: BidState) -> list[Send]:
        return [Send(name, {"job_id": "j"}) for name in ("analyst", "risk", "fit")]

    def analyst(_: BidState) -> dict[str, Any]:
        return {"job_spec": h.JobSpec(job_id="j", title="t"), "steps_used": 1, "tokens_used": 10}

    def risk(_: BidState) -> dict[str, Any]:
        report = h.RiskReport(score=0.2, explanation="ok", flags=[h.RiskFlag.INJECTION])
        return {
            "risk_report": report,
            "errors": [NodeError(node="risk", message="partial")],
            "evidence": [evidence("shared", "from risk")],
            "steps_used": 1,
            "tokens_used": 20,
        }

    def fit(_: BidState) -> dict[str, Any]:
        return {
            "fit_report": h.FitReport(score=0.9),
            "errors": [NodeError(node="fit", message="partial")],
            "evidence": [evidence("own")],
            "steps_used": 1,
            "tokens_used": 30,
        }

    def join(state: BidState) -> dict[str, Any]:
        assert {"job_spec", "risk_report", "fit_report"} <= set(state)
        return {"status": ThreadStatus.AWAITING_APPROVAL}

    builder = StateGraph(BidState)
    for name, node in (("analyst", analyst), ("risk", risk), ("fit", fit), ("join", join)):
        builder.add_node(name, node)
    builder.add_conditional_edges(START, fan_out, ["analyst", "risk", "fit"])
    for name in ("analyst", "risk", "fit"):
        builder.add_edge(name, "join")
    builder.add_edge("join", END)
    graph = builder.compile(checkpointer=InMemorySaver())

    config = {"configurable": {"thread_id": "job-1"}}
    result = graph.invoke({"job_id": "j"}, config)

    assert result["steps_used"] == 3 and result["tokens_used"] == 60
    assert {error.node for error in result["errors"]} == {"risk", "fit"}
    assert {item.evidence_id for item in result["evidence"]} == {"shared", "own"}
    restored = graph.get_state(config).values
    assert isinstance(restored["job_spec"], h.JobSpec)
    assert restored["risk_report"].flags == [h.RiskFlag.INJECTION]
    assert restored["status"] is ThreadStatus.AWAITING_APPROVAL
    assert isinstance(restored["errors"][0], NodeError)


def test_config_routes_every_node_in_both_profiles() -> None:
    for profile in ("open", "paid"):
        config = load_config(env={"MODEL_PROFILE": profile})
        for node in REQUIRED_NODES:
            primary, _ = config.model_for(node)
            assert primary.model


def test_open_profile_has_no_paid_provider() -> None:
    config = load_config(env={"MODEL_PROFILE": "open"})
    for node in REQUIRED_NODES:
        primary, fallback = config.model_for(node)
        assert primary.provider in {"groq", "ollama"}
        assert fallback is None or fallback.provider in {"groq", "ollama"}


def test_cheap_nodes_have_a_fallback() -> None:
    config = load_config(env={})
    for node in ("scout", "risk_assessor", "compliance", "intent"):
        assert config.model_for(node)[1] is not None
    assert config.model_for("scout")[0] != config.model_for("writer")[0]


def test_environment_overrides_config() -> None:
    config = load_config(env={"MODEL_PROFILE": "paid", "MARKETPLACE_MODE": "upwork_live"})
    assert config.models.active_profile == "paid"
    assert config.marketplace.mode == "upwork_live"


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(ValidationError):
        load_config(env={"MODEL_PROFILE": "nope"})


def edit_config(tmp_path: Path, change: Any) -> Path:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    change(raw)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_missing_node_route_is_rejected(tmp_path: Path) -> None:
    path = edit_config(tmp_path, lambda r: r["models"]["profiles"]["open"]["nodes"].pop("writer"))
    with pytest.raises(ValidationError, match="writer"):
        load_config(path, env={})


def test_unknown_tier_is_rejected(tmp_path: Path) -> None:
    def change(raw: Any) -> None:
        raw["models"]["profiles"]["open"]["nodes"]["writer"]["tier"] = "imaginary"

    with pytest.raises(ValidationError, match="imaginary"):
        load_config(edit_config(tmp_path, change), env={})


def test_critic_loop_cannot_exceed_two_revisions(tmp_path: Path) -> None:
    def change(raw: Any) -> None:
        raw["budgets"]["max_revisions"] = 3

    with pytest.raises(ValidationError):
        load_config(edit_config(tmp_path, change), env={})


def test_analyst_and_compliance_have_no_tools() -> None:
    config = load_config(env={})
    assert config.tools_for("analyst") == [] and config.tools_for("compliance") == []
    assert "submit_proposal" not in config.tools_for("client_liaison")


def test_only_the_submission_step_has_spending_tools() -> None:
    config = load_config(env={})
    holders = [agent for agent, tools in config.agent_tools.items() if "submit_proposal" in tools]
    assert holders == ["submission"]


def test_prompt_loader_uses_highest_version_by_default(tmp_path: Path) -> None:
    (tmp_path / "writer.v1.md").write_text("one", encoding="utf-8")
    (tmp_path / "writer.v10.md").write_text("ten", encoding="utf-8")
    (tmp_path / "writer.v2.md").write_text("two", encoding="utf-8")
    (tmp_path / "critic_extra.v9.md").write_text("no", encoding="utf-8")
    assert load_prompt("writer", directory=tmp_path) == "ten"
    assert load_prompt("writer", version=2, directory=tmp_path) == "two"


def test_missing_prompt_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt("writer", directory=tmp_path)
    with pytest.raises(FileNotFoundError):
        load_prompt("writer", version=1, directory=tmp_path)
