"""Claim verification (B3): split a draft into claims and check each against evidence.

Hard checks run first and no model can overrule them: a figure or a named technology that
appears in no evidence makes the claim unsupported. Remaining claims go to a judge, which is
the language model when one is configured and a lexical comparison otherwise.
"""

import re
from collections.abc import Collection, Iterable
from typing import Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from agent.config import VerificationConfig
from agent.handoffs import ClaimSupport, EvidenceItem, ProposalClaim, ProposalDraft, ScreeningAnswer

_CITATION = re.compile(r"\s*\[((?:case|readme|code|review|proposal|cv):[^\]]+)\]")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[\"'A-Z0-9])|\n{2,}")
_NUMBER = re.compile(r"\$?\d+(?:[.,]\d+)*\s?(?:%|k\b)?")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]*")
_NUMBER_WORDS = {
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "fifteen": "15",
    "twenty": "20",
}
_STOPWORDS = frozenset(
    """a an and are as at be been but by for from had has have he i i'd i'm i've in into is it its
    me my of on or our over so than that the their them then these they this to under up was we
    were what when which who will with would you your about after all also can could did do
    does each just more most not only other out some such there through very while""".split()
)
_EXPERIENCE = re.compile(
    r"\b(built|build|developed|shipped|delivered|led|created|designed|implemented|deployed|"
    r"reduced|raised|increased|improved|cut|worked|experience|experienced|years?|expert|"
    r"specialist|certified|clients?|migrated|automated|trained|tuned|launched)\b",
    re.IGNORECASE,
)
_FORWARD_LOOKING = re.compile(
    r"^(i would|i'd|i will|i'll|i can start|i am available|i'm available|i look forward|"
    r"happy to|looking forward|let's|please|thanks|thank you|hello|hi\b|dear|best regards)",
    re.IGNORECASE,
)


class ClaimJudgement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    support: ClaimSupport
    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str


class ClaimJudge(Protocol):
    def judge(self, claim: str, evidence: list[EvidenceItem]) -> ClaimJudgement: ...


class VerificationReport(BaseModel):
    total_claims: int
    unsupported_before: int
    unsupported_after: int
    unsupported_rate_before: float
    unsupported_rate_after: float


class VerificationResult(BaseModel):
    draft: ProposalDraft
    removed_claims: list[ProposalClaim]
    report: VerificationReport
    needs_rewrite: bool


def _stem(word: str) -> str:
    word = word.lower().strip("./-")
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _content_terms(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall(text) if w.lower() not in _STOPWORDS}


def _numbers(text: str) -> set[str]:
    found = {match.strip().replace(" ", "").rstrip(".,") for match in _NUMBER.findall(text)}
    lowered = text.lower()
    found |= {digit for word, digit in _NUMBER_WORDS.items() if re.search(rf"\b{word}\b", lowered)}
    return found


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_BOUNDARY.split(text) if part.strip()]


def is_factual_claim(sentence: str, vocabulary: Collection[str] = ()) -> bool:
    """True for a sentence asserting experience or results, rather than plans or courtesy."""
    plain = _CITATION.sub("", sentence).strip()
    if plain.endswith("?"):
        return False
    lowered = plain.lower()
    names_skill = any(
        re.search(rf"(?<!\w){re.escape(t.lower())}(?!\w)", lowered) for t in vocabulary
    )
    if _FORWARD_LOOKING.match(plain):
        return False
    return names_skill or bool(_EXPERIENCE.search(plain)) or bool(_numbers(plain))


def _named_terms(sentence: str, vocabulary: Collection[str]) -> set[str]:
    """Specific names in a claim: mid-sentence capitalised words and known skills."""
    words = sentence.split()
    named = {
        word.strip(".,;:!?()\"'")
        for word in words[1:]
        if word[:1].isupper() and word.strip(".,;:!?()\"'").lower() not in _STOPWORDS
    }
    lowered = sentence.lower()
    named |= {
        term
        for term in vocabulary
        if re.search(rf"(?<!\w){re.escape(term.lower())}(?!\w)", lowered)
    }
    return {name for name in named if name}


def hard_check(
    claim: str,
    evidence: list[EvidenceItem],
    vocabulary: Collection[str] = (),
) -> str | None:
    """Return why a claim is unsupported, or None if it passes."""
    corpus = " ".join(item.excerpt for item in evidence)
    lowered = corpus.lower()
    missing_numbers = sorted(_numbers(claim) - _numbers(corpus))
    if missing_numbers:
        return f"No evidence states: {', '.join(missing_numbers)}."
    missing_names = sorted(
        name
        for name in _named_terms(claim, vocabulary)
        if not re.search(rf"(?<!\w){re.escape(name.lower())}(?!\w)", lowered)
    )
    if missing_names:
        return f"No evidence mentions: {', '.join(missing_names)}."
    return None


class LexicalJudge:
    """Measures how much of a claim's content appears in the best single evidence item."""

    def __init__(self, config: VerificationConfig) -> None:
        self._config = config

    def judge(self, claim: str, evidence: list[EvidenceItem]) -> ClaimJudgement:
        terms = _content_terms(claim)
        if not terms or not evidence:
            return ClaimJudgement(
                support=ClaimSupport.UNSUPPORTED,
                explanation="There is no evidence to compare this claim with.",
            )
        best, coverage = max(
            ((item, len(terms & _content_terms(item.excerpt)) / len(terms)) for item in evidence),
            key=lambda pair: pair[1],
        )
        if coverage >= self._config.supported_coverage:
            support = ClaimSupport.SUPPORTED
        elif coverage >= self._config.partly_supported_coverage:
            support = ClaimSupport.PARTLY_SUPPORTED
        else:
            return ClaimJudgement(
                support=ClaimSupport.UNSUPPORTED,
                explanation=f"Only {coverage:.0%} of this claim's content appears in any evidence.",
            )
        return ClaimJudgement(
            support=support,
            evidence_ids=[best.evidence_id],
            explanation=f"{coverage:.0%} of this claim's content appears in {best.evidence_id}.",
        )


def _delimited_data(claim: str, evidence_blocks: str) -> str:
    return f"<claim>\n{claim}\n</claim>\n{evidence_blocks}"


class LlmJudge:
    """Entailment by the configured language model. Evidence is passed as delimited data."""

    def __init__(self, model: BaseChatModel, instructions: str) -> None:
        self._model = model.with_structured_output(ClaimJudgement)
        self._instructions = instructions

    def judge(self, claim: str, evidence: list[EvidenceItem]) -> ClaimJudgement:
        blocks = "\n".join(
            f'<evidence id="{item.evidence_id}">\n{item.excerpt}\n</evidence>' for item in evidence
        )
        result = self._model.invoke(
            [
                SystemMessage(content=self._instructions),
                HumanMessage(content=_delimited_data(claim, blocks)),
            ]
        )
        if not isinstance(result, ClaimJudgement):
            raise TypeError("The model did not return a claim judgement")
        known = {item.evidence_id for item in evidence}
        cited = [item for item in result.evidence_ids if item in known]
        if result.support is not ClaimSupport.UNSUPPORTED and not cited:
            return ClaimJudgement(
                support=ClaimSupport.UNSUPPORTED,
                explanation="The judge cited no evidence that exists.",
            )
        return result.model_copy(update={"evidence_ids": cited})


def check_claims(
    text: str,
    evidence: list[EvidenceItem],
    judge: ClaimJudge,
    vocabulary: Collection[str] = (),
    prefix: str = "c",
) -> list[tuple[str, ProposalClaim | None]]:
    """Return each sentence with its verified claim, or None for a non-claim sentence."""
    by_id = {item.evidence_id: item for item in evidence}
    checked: list[tuple[str, ProposalClaim | None]] = []
    for number, sentence in enumerate(split_sentences(text), start=1):
        plain = _CITATION.sub("", sentence).strip()
        if not is_factual_claim(sentence, vocabulary):
            checked.append((plain, None))
            continue
        # A claim that cites evidence is checked against that evidence only.
        cited = [by_id[i] for i in _CITATION.findall(sentence) if i in by_id]
        pool = cited or evidence
        failure = hard_check(plain, pool, vocabulary)
        judgement = (
            ClaimJudgement(support=ClaimSupport.UNSUPPORTED, explanation=failure)
            if failure
            else judge.judge(plain, pool)
        )
        checked.append(
            (
                plain,
                ProposalClaim(
                    claim_id=f"{prefix}{number}",
                    text=plain,
                    evidence_ids=judgement.evidence_ids,
                    support=judgement.support,
                    explanation=judgement.explanation,
                ),
            )
        )
    return checked


def _is_unsupported(claim: ProposalClaim | None) -> bool:
    return claim is not None and claim.support is ClaimSupport.UNSUPPORTED


def _rate(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def _sections(draft: ProposalDraft) -> Iterable[tuple[str, str]]:
    yield "cover", draft.cover_letter
    for number, answer in enumerate(draft.screening_answers, start=1):
        yield f"a{number}-", answer.answer


def verify_draft(
    draft: ProposalDraft,
    evidence: list[EvidenceItem],
    judge: ClaimJudge,
    vocabulary: Collection[str] = (),
) -> VerificationResult:
    """Label every claim, remove unsupported ones, then re-verify the result."""
    checked = {
        prefix: check_claims(text, evidence, judge, vocabulary, prefix)
        for prefix, text in _sections(draft)
    }

    def keep(prefix: str) -> str:
        return " ".join(s for s, claim in checked[prefix] if not _is_unsupported(claim))

    answers = [
        ScreeningAnswer(question=answer.question, answer=keep(f"a{number}-"))
        for number, answer in enumerate(draft.screening_answers, start=1)
    ]
    cleaned = draft.model_copy(update={"cover_letter": keep("cover"), "screening_answers": answers})

    before = [claim for rows in checked.values() for _, claim in rows if claim is not None]
    removed = [claim for claim in before if _is_unsupported(claim)]
    recheck = [
        claim
        for prefix, text in _sections(cleaned)
        for _, claim in check_claims(text, evidence, judge, vocabulary, prefix)
        if claim is not None
    ]
    unsupported_after = sum(_is_unsupported(claim) for claim in recheck)
    final = cleaned.model_copy(
        update={
            "claims": recheck,
            "evidence_ids": sorted({item for claim in recheck for item in claim.evidence_ids}),
        }
    )
    report = VerificationReport(
        total_claims=len(before),
        unsupported_before=len(removed),
        unsupported_after=unsupported_after,
        unsupported_rate_before=_rate(len(removed), len(before)),
        unsupported_rate_after=_rate(unsupported_after, len(recheck)),
    )
    emptied = not final.cover_letter or any(not a.answer for a in final.screening_answers)
    return VerificationResult(
        draft=final,
        removed_claims=removed,
        report=report,
        needs_rewrite=bool(removed) and emptied,
    )
