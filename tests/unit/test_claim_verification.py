import pytest

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


@pytest.mark.parametrize("sentence", ["You need a Kubernetes specialist.", "2.", "**Answers**"])
def test_statements_about_the_client_and_fragments_are_not_claims(sentence: str) -> None:
    assert not is_factual_claim(sentence, VOCABULARY)


def test_skill_shown_by_evidence_may_be_claimed_without_being_named() -> None:
    claim = "I built Anomaly Detection for a telecom operator."
    assert hard_check(claim, [CHURN]) is not None
    assert hard_check(claim, [CHURN], trusted_terms=["Anomaly Detection"]) is None


def test_names_are_compared_without_their_hyphenated_suffix() -> None:
    assert hard_check("I built an XGBoost‑based model.", [CHURN]) is None


def test_a_skill_the_portfolio_lacks_still_fails() -> None:
    assert hard_check("I have used kubernetes in production.", [CHURN], ["Kubernetes"]) is not None


def test_names_can_come_from_other_retrieved_evidence_but_figures_cannot() -> None:
    other = EvidenceItem(
        evidence_id="o", type="t", source="s", excerpt="Used Spark for 75% of the workload."
    )
    assert hard_check("I built a job on Spark.", [CHURN], all_evidence=[CHURN, other]) is None
    assert hard_check("I sped it up by 75%.", [CHURN], all_evidence=[CHURN, other]) is not None


SUPPORTED = ClaimJudgement(
    support=ClaimSupport.SUPPORTED, evidence_ids=["case:p:churn"], explanation="Stated."
)


def test_llm_judge_passes_evidence_as_delimited_data() -> None:
    seen: list[str] = []

    def ask(data: str) -> ClaimJudgement:
        seen.append(data)
        return SUPPORTED

    assert LlmJudge(ask).judge("claim text", [CHURN]).support is ClaimSupport.SUPPORTED
    assert '<evidence id="case:p:churn">' in seen[0]
    assert "<claim>\nclaim text\n</claim>" in seen[0]


def test_llm_judge_cannot_cite_evidence_that_does_not_exist() -> None:
    invented = ClaimJudgement(
        support=ClaimSupport.SUPPORTED, evidence_ids=["invented"], explanation="Trust me."
    )
    assert LlmJudge(lambda _: invented).judge("c", [CHURN]).support is ClaimSupport.UNSUPPORTED


def test_llm_cannot_overrule_the_hard_check() -> None:
    judge = LlmJudge(lambda _: SUPPORTED)
    ((_, claim),) = check_claims("I raised retention by 45%.", [CHURN], judge, VOCABULARY)
    assert claim is not None and claim.support is ClaimSupport.UNSUPPORTED


def test_each_claim_is_judged_once_even_with_recheck() -> None:
    calls: list[str] = []

    def ask(data: str) -> ClaimJudgement:
        calls.append(data)
        return SUPPORTED

    letter = "I tuned a gradient boosted model with XGBoost for a telecom operator."
    verify_draft(draft(letter), [CHURN], LlmJudge(ask), VOCABULARY)
    assert len(calls) == 1


def test_only_the_closest_evidence_is_sent_to_the_judge() -> None:
    noise = [
        EvidenceItem(evidence_id=f"n{i}", type="t", source="s", excerpt=f"unrelated {i}")
        for i in range(5)
    ]
    seen: list[str] = []

    def ask(data: str) -> ClaimJudgement:
        seen.append(data)
        return SUPPORTED

    letter = "I tuned a gradient boosted model with XGBoost for a telecom operator."
    verify_draft(draft(letter), [*noise, CHURN], LlmJudge(ask), VOCABULARY, max_evidence=2)
    assert seen[0].count("<evidence") == 2 and "case:p:churn" in seen[0]
