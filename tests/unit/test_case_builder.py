"""X3: case studies drafted from a repository keep only what the repository supports."""

from typing import Any

from agent.case_builder import (
    MIN_VERIFIED_CLAIMS,
    CaseStudyDraft,
    build_case_study,
    verify_case_study,
)
from agent.config import get_config
from agent.llm import LlmResult
from agent.runtime import AgentRuntime
from rag.ingest.loaders import PortfolioDocument
from rag.verify import LexicalJudge

README = PortfolioDocument(
    item_id="readme:github:me/churn:README.md",
    type="readme",
    title="churn/README.md",
    text=(
        "Churn scorer. A FastAPI service that predicts customer churn with a scikit-learn "
        "gradient boosting model, trained on subscription events stored in PostgreSQL."
    ),
    source="github.com/me/churn/README.md",
    project="churn",
)
CODE = PortfolioDocument(
    item_id="code:github:me/churn:app.py",
    type="code",
    title="churn/app.py",
    text="from fastapi import FastAPI\nfrom sklearn.ensemble import GradientBoostingClassifier",
    source="github.com/me/churn/app.py",
    project="churn",
)

DRAFT = CaseStudyDraft(
    title="Churn scorer",
    situation="The project predicts customer churn from subscription events.",
    what_i_did=(
        "I built a FastAPI service that predicts customer churn with a scikit-learn gradient "
        "boosting model. The model is trained on subscription events stored in PostgreSQL. "
        "It served two million users for a national bank."
    ),
    outcome="The repository does not state an outcome.",
    skills=["FastAPI", "scikit-learn", "PostgreSQL"],
)


def lexical() -> LexicalJudge:
    return LexicalJudge(get_config().verification)


def test_a_claim_the_repository_does_not_support_is_removed() -> None:
    sections, claims, verified, removed = verify_case_study(DRAFT, [README, CODE], lexical())
    assert any("national bank" in sentence for sentence in removed)
    assert "national bank" not in sections["what_i_did"]
    assert "FastAPI service" in sections["what_i_did"]
    assert verified >= MIN_VERIFIED_CLAIMS and claims == verified + len(removed)


class Llm:
    def __init__(self, draft: CaseStudyDraft) -> None:
        self.draft = draft
        self.data = ""

    async def structured(self, node: str, instructions: str, data: str, schema: Any) -> Any:
        assert node == "case_builder" and schema is CaseStudyDraft
        self.data = data
        return LlmResult(self.draft, 100, "m")


def runtime(llm: Llm) -> AgentRuntime:
    base = get_config()
    config = base.model_copy(
        update={"verification": base.verification.model_copy(update={"judge": "lexical"})}
    )
    return AgentRuntime(config, llm, None, None)  # type: ignore[arg-type]


async def test_the_case_study_is_saved_as_portfolio_evidence() -> None:
    llm = Llm(DRAFT)
    result = await build_case_study([README, CODE], "me", "churn", runtime(llm))
    assert result.document is not None
    assert result.document.item_id == "case:github:me/churn"
    assert result.document.type == "case_study" and result.document.project == "churn"
    assert "verified against the code" in result.document.source
    assert "national bank" not in result.document.text
    assert "<untrusted_repository_file>" in llm.data  # repository text is delimited as data


async def test_a_case_study_with_too_little_support_is_not_saved() -> None:
    invented = DRAFT.model_copy(
        update={
            "situation": "A logistics firm hired me to cut delivery times across Europe.",
            "what_i_did": "I led a team of eight engineers for two years at a large retailer.",
        }
    )
    result = await build_case_study([README, CODE], "me", "churn", runtime(Llm(invented)))
    assert result.document is None and result.verified < MIN_VERIFIED_CLAIMS


def test_evaluation_groups_projects_and_scores_skills() -> None:
    from evals.runners.case_builder_eval import projects, skill_overlap

    gold = PortfolioDocument(
        item_id="case:p:churn",
        type="case_study",
        title="Churn",
        text="x",
        source="s",
        project="churn",
        skills=["FastAPI", "scikit-learn", "Docker"],
    )
    grouped = projects([README, CODE, gold])
    assert list(grouped) == ["churn"] and len(grouped["churn"]["repo"]) == 2
    precision, recall = skill_overlap(["fastapi", "Scikit learn", "Redis"], gold.skills)
    assert precision == 2 / 3 and recall == 2 / 3
    assert skill_overlap([], gold.skills) == (None, 0.0)
