"""Signature extension X3: case studies drafted from a GitHub repository, verified against its code.

GitHub ingestion indexes a repository's README and code as evidence. This step also writes one
case study per repository, so the Writer can cite a project as a whole. Every factual sentence is
checked against the repository's own files with the same claim checker the proposals use; anything
the files do not support is removed before the case study reaches the portfolio. A case study left
with too few verified claims is not saved at all.
"""

import asyncio
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from agent.guardrails import delimit_untrusted
from agent.handoffs import ClaimSupport, EvidenceItem
from agent.nodes.verifier import claim_judge
from agent.runtime import AgentRuntime
from rag.ingest.loaders import PortfolioDocument
from rag.verify import ClaimJudge, check_claims

MIN_VERIFIED_CLAIMS = 2
_EXCERPT_CHARS = 4000
_DATA_CHARS = 24000


class CaseStudyDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    situation: str
    what_i_did: str
    outcome: str
    skills: list[str] = Field(default_factory=list, max_length=12)


@dataclass(frozen=True)
class CaseStudyResult:
    """What the builder produced, with the counts the evaluation reports."""

    document: PortfolioDocument | None
    claims: int
    verified: int
    removed: list[str]
    tokens: int


def repository_evidence(documents: list[PortfolioDocument]) -> list[EvidenceItem]:
    return [
        EvidenceItem(
            evidence_id=item.item_id,
            type=item.type,
            source=item.source,
            excerpt=item.text[:_EXCERPT_CHARS],
        )
        for item in documents
    ]


def _repository_data(documents: list[PortfolioDocument]) -> str:
    """READMEs first, since they describe the project; then code, up to a size limit."""
    ordered = sorted(documents, key=lambda item: item.type != "readme")
    parts, size = [], 0
    for item in ordered:
        block = delimit_untrusted("repository_file", f"{item.title}\n{item.text}")
        if size + len(block) > _DATA_CHARS:
            break
        parts.append(block)
        size += len(block)
    return "\n\n".join(parts)


def verify_case_study(
    draft: CaseStudyDraft,
    documents: list[PortfolioDocument],
    judge: ClaimJudge,
    max_evidence: int | None = None,
) -> tuple[dict[str, str], int, int, list[str]]:
    """Keep each section's supported sentences. Returns sections, claims, verified, removed."""
    evidence = repository_evidence(documents)
    sections: dict[str, str] = {}
    claims = verified = 0
    removed: list[str] = []
    for name in ("situation", "what_i_did", "outcome"):
        kept = []
        for sentence, claim in check_claims(
            getattr(draft, name), evidence, judge, (), name[:1], max_evidence
        ):
            if claim is None:  # not a factual claim: kept as written
                kept.append(sentence)
                continue
            claims += 1
            if claim.support is ClaimSupport.UNSUPPORTED:
                removed.append(sentence)
            else:
                verified += 1
                kept.append(sentence)
        sections[name] = " ".join(kept).strip()
    return sections, claims, verified, removed


async def build_case_study(
    documents: list[PortfolioDocument],
    owner: str,
    repo: str,
    runtime: AgentRuntime,
) -> CaseStudyResult:
    if not documents:
        return CaseStudyResult(None, 0, 0, [], 0)
    drafted = await runtime.llm.structured(
        "case_builder", runtime.prompt("case_builder"), _repository_data(documents), CaseStudyDraft
    )
    tokens = [drafted.tokens]
    judge = claim_judge(runtime, tokens)
    sections, claims, verified, removed = await asyncio.to_thread(
        verify_case_study,
        drafted.value,
        documents,
        judge,
        runtime.config.verification.max_evidence_per_claim,
    )
    if verified < MIN_VERIFIED_CLAIMS:
        return CaseStudyResult(None, claims, verified, removed, tokens[0])
    draft = drafted.value
    text = "\n\n".join(
        part
        for part in (
            f"# {draft.title}",
            f"## The situation\n{sections['situation']}" if sections["situation"] else "",
            f"## What I did\n{sections['what_i_did']}" if sections["what_i_did"] else "",
            f"## Outcome\n{sections['outcome']}" if sections["outcome"] else "",
        )
        if part
    )
    document = PortfolioDocument(
        item_id=f"case:github:{owner}/{repo}",
        type="case_study",
        title=draft.title,
        text=text,
        source=f"github.com/{owner}/{repo} (generated, verified against the code)",
        project=repo,
        skills=sorted({skill.strip() for skill in draft.skills if skill.strip()}),
    )
    return CaseStudyResult(document, claims, verified, removed, tokens[0])
