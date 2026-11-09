from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from agent.config import get_config
from agent.handoffs import ClaimSupport, EvidenceItem, ProposalDraft, ScreeningAnswer
from rag.verify import (
    ClaimJudgement,
    LexicalJudge,
    LlmJudge,
    check_claims,
    hard_check,
    is_factual_claim,
    split_sentences,
    verify_draft,
)

CHURN = EvidenceItem(
    evidence_id="case:p:churn",
    type="case_study",
    source="churn.md",
    excerpt=(
        "A telecom operator was losing prepaid subscribers. I built rolling usage aggregates "
        "and tuned a gradient boosted model with XGBoost and scikit-learn. The retention team "
        "reached 31% more leavers at the same call volume."
    ),
)
JUDGE = LexicalJudge(get_config().verification)
VOCABULARY = ["XGBoost", "scikit-learn", "Kubernetes", "Celery"]


def draft(letter: str, *answers: str) -> ProposalDraft:
    return ProposalDraft(
        job_id="j",
        cover_letter=letter,
        screening_answers=[ScreeningAnswer(question="Q", answer=a) for a in answers],
    )


def test_sentences_keep_decimals_together() -> None:
    assert split_sentences("Accuracy rose to 9.5 points. Next sentence here.") == [
        "Accuracy rose to 9.5 points.",
        "Next sentence here.",
    ]


@pytest.mark.parametrize(
    "sentence",
    [
        "I built a churn model for a telecom.",
        "I have 10 years of experience.",
        "Retention improved by 31%.",
        "My toolkit includes Kubernetes.",
    ],
)
def test_factual_claims_are_recognised(sentence: str) -> None:
    assert is_factual_claim(sentence, VOCABULARY)


@pytest.mark.parametrize(
    "sentence",
    [
        "Hello, thanks for posting this.",
        "Could you share a sample of the data?",
        "I would start by profiling the data.",
        "I am available from Monday.",
        "Looking forward to hearing from you.",
    ],
)
def test_courtesy_and_plans_are_not_claims(sentence: str) -> None:
    assert not is_factual_claim(sentence, VOCABULARY)


def test_true_claim_is_supported_and_cites_evidence() -> None:
    ((_, claim),) = check_claims(
        "I tuned a gradient boosted model with XGBoost for a telecom operator.",
        [CHURN],
        JUDGE,
        VOCABULARY,
    )
    assert claim is not None
    assert claim.support is ClaimSupport.SUPPORTED
    assert claim.evidence_ids == ["case:p:churn"]


@pytest.mark.parametrize(
    "claim",
    [
        "I raised retention by 45%.",
        "I have ten years of experience with XGBoost.",
        "I deployed the model on Kubernetes.",
        "I built a task queue with Celery.",
    ],
)
def test_invented_figures_and_technologies_fail_the_hard_check(claim: str) -> None:
    assert hard_check(claim, [CHURN], VOCABULARY) is not None


def test_matching_figure_passes_the_hard_check() -> None:
    assert hard_check("The team reached 31% more leavers.", [CHURN], VOCABULARY) is None


def test_unrelated_claim_is_unsupported() -> None:
    ((_, claim),) = check_claims("I built payroll compliance systems.", [CHURN], JUDGE, VOCABULARY)
    assert claim is not None and claim.support is ClaimSupport.UNSUPPORTED


def test_partial_overlap_is_partly_supported() -> None:
    ((_, claim),) = check_claims(
        "I built a telecom model for insurers and hospitals.", [CHURN], JUDGE, VOCABULARY
    )
    assert claim is not None and claim.support is ClaimSupport.PARTLY_SUPPORTED


def test_cited_evidence_is_the_only_evidence_used() -> None:
    other = EvidenceItem(
        evidence_id="case:p:other", type="case_study", source="o.md", excerpt="Unrelated content."
    )
    ((_, claim),) = check_claims(
        "The team reached 31% more leavers. [case:p:other]", [CHURN, other], JUDGE, VOCABULARY
    )
    assert claim is not None and claim.support is ClaimSupport.UNSUPPORTED


def test_no_evidence_means_unsupported() -> None:
    ((_, claim),) = check_claims("I built a churn model.", [], JUDGE, VOCABULARY)
    assert claim is not None and claim.support is ClaimSupport.UNSUPPORTED


def test_unsupported_claims_are_removed_before_the_critic_sees_the_draft() -> None:
    original = draft(
        "Hello there. I tuned a gradient boosted model with XGBoost for a telecom operator "
        "[case:p:churn]. I have ten years of experience. Could we talk on Friday?",
        "I deployed it on Kubernetes.",
    )
    result = verify_draft(original, [CHURN], JUDGE, VOCABULARY)

    assert "ten years" not in result.draft.cover_letter
    assert "Hello there." in result.draft.cover_letter
    assert "Could we talk" in result.draft.cover_letter
    assert "[case:p:churn]" not in result.draft.cover_letter
    assert result.draft.screening_answers[0].answer == ""
    assert result.needs_rewrite is True
    assert len(result.removed_claims) == 2
    report = result.report
    assert (report.total_claims, report.unsupported_before, report.unsupported_after) == (3, 2, 0)
    assert report.unsupported_rate_before == pytest.approx(0.6667)
    assert report.unsupported_rate_after == 0
    assert result.draft.evidence_ids == ["case:p:churn"]
    assert all(claim.support is not ClaimSupport.UNSUPPORTED for claim in result.draft.claims)


def test_clean_draft_is_unchanged_and_needs_no_rewrite() -> None:
    letter = "I tuned a gradient boosted model with XGBoost for a telecom operator."
    result = verify_draft(draft(letter), [CHURN], JUDGE, VOCABULARY)
    assert result.draft.cover_letter == letter
    assert result.removed_claims == [] and result.needs_rewrite is False


def test_removing_evidence_makes_the_claim_unsupported() -> None:
    letter = "I tuned a gradient boosted model with XGBoost for a telecom operator."
    assert verify_draft(draft(letter), [CHURN], JUDGE, VOCABULARY).removed_claims == []
    after = verify_draft(draft(letter), [], JUDGE, VOCABULARY)
    assert len(after.removed_claims) == 1
    assert after.draft.cover_letter == ""


def fake_llm(judgement: ClaimJudgement) -> Any:
    class Model(GenericFakeChatModel):
        def with_structured_output(self, schema: Any, **_: Any) -> Any:
            class Structured:
                def __init__(self) -> None:
                    self.messages: list[Any] = []

                def invoke(self, messages: Any) -> ClaimJudgement:
                    self.messages = messages
                    return judgement

            return Structured()

    return Model(messages=iter([]))


def test_llm_judge_passes_evidence_as_delimited_data() -> None:
    judgement = ClaimJudgement(
        support=ClaimSupport.SUPPORTED, evidence_ids=["case:p:churn"], explanation="Stated."
    )
    judge = LlmJudge(fake_llm(judgement), "instructions")
    assert judge.judge("claim text", [CHURN]).support is ClaimSupport.SUPPORTED
    system, human = judge._model.messages  # type: ignore[attr-defined]
    assert system.content == "instructions"
    assert '<evidence id="case:p:churn">' in human.content
    assert "<claim>\nclaim text\n</claim>" in human.content


def test_llm_judge_cannot_cite_evidence_that_does_not_exist() -> None:
    judgement = ClaimJudgement(
        support=ClaimSupport.SUPPORTED, evidence_ids=["invented"], explanation="Trust me."
    )
    result = LlmJudge(fake_llm(judgement), "x").judge("claim", [CHURN])
    assert result.support is ClaimSupport.UNSUPPORTED


def test_llm_cannot_overrule_the_hard_check() -> None:
    judgement = ClaimJudgement(
        support=ClaimSupport.SUPPORTED, evidence_ids=["case:p:churn"], explanation="Sure."
    )
    judge = LlmJudge(fake_llm(judgement), "x")
    ((_, claim),) = check_claims("I raised retention by 45%.", [CHURN], judge, VOCABULARY)
    assert claim is not None and claim.support is ClaimSupport.UNSUPPORTED
