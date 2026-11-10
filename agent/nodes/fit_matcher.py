"""Fit Matcher: map each requirement to portfolio evidence and compute a fit score.

It runs in parallel with the Job Analyst, so it identifies requirements itself. The reconcile
node later checks its matrix against the Analyst's JobSpec and fills any gaps.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.guardrails import delimit_untrusted
from agent.handoffs import CoverageStatus, EvidenceItem, FitReport, JobSpec, RequirementMatch
from agent.nodes.analyst import job_text
from agent.runtime import AgentRuntime
from agent.state import BidState


class _Requirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirements: list[str] = Field(min_length=1)


class _ModelMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: str
    status: CoverageStatus
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class _ModelFit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matches: list[_ModelMatch]
    reasons: list[str] = Field(default_factory=list)


def fit_score(matches: list[RequirementMatch]) -> float:
    if not matches:
        return 0.0
    weights = {CoverageStatus.COVERED: 1.0, CoverageStatus.PARTIAL: 0.5, CoverageStatus.MISSING: 0}
    return round(sum(weights[match.status] for match in matches) / len(matches), 3)


async def match_requirements(
    requirements: list[str],
    tenant_query_context: str,
    runtime: AgentRuntime,
) -> tuple[list[RequirementMatch], list[EvidenceItem], list[str], int]:
    """Retrieve evidence per requirement and have the model map them."""
    evidence: dict[str, EvidenceItem] = {}
    sections = []
    for requirement in requirements:
        found = await runtime.tools.call(
            "fit_matcher",
            "search_evidence",
            {
                "requirement": requirement,
                "k": runtime.config.rag.fit_evidence_per_requirement,
            },
        )
        items = [EvidenceItem.model_validate(item) for item in found["items"]]
        evidence.update({item.evidence_id: item for item in items})
        blocks = "\n".join(f"[{item.evidence_id}] {item.excerpt}" for item in items)
        sections.append(f"Requirement: {requirement}\n{delimit_untrusted('evidence', blocks)}")

    result = await runtime.llm.structured(
        "fit_matcher",
        runtime.prompt("fit_matcher"),
        f"{tenant_query_context}\n\n" + "\n\n".join(sections),
        _ModelFit,
    )
    by_requirement = {match.requirement.lower(): match for match in result.value.matches}
    matches = []
    for requirement in requirements:
        proposed = by_requirement.get(requirement.lower())
        # Evidence the model invented is discarded, which turns its claim into a gap.
        cited = [i for i in (proposed.evidence_ids if proposed else []) if i in evidence]
        status = proposed.status if proposed and cited else CoverageStatus.MISSING
        matches.append(
            RequirementMatch(
                requirement=requirement,
                status=status,
                evidence_ids=cited if status is not CoverageStatus.MISSING else [],
                reason=proposed.reason if proposed else "No mapping was produced.",
            )
        )
    return matches, list(evidence.values()), result.value.reasons, result.tokens


def build_report(matches: list[RequirementMatch], reasons: list[str]) -> FitReport:
    return FitReport(
        score=fit_score(matches),
        matches=matches,
        matched_skills=[m.requirement for m in matches if m.status is CoverageStatus.COVERED],
        missing_skills=[m.requirement for m in matches if m.status is CoverageStatus.MISSING],
        evidence_ids=sorted({item for match in matches for item in match.evidence_ids}),
        reasons=reasons,
    )


async def run(state: BidState, runtime: AgentRuntime) -> dict[str, Any]:
    job = state["job"]
    context = delimit_untrusted("job_post", job_text(job))
    tokens = 0
    requirements: list[str] = list(job.get("required_skills", []))
    if not requirements:
        extracted = await runtime.llm.structured(
            "fit_matcher", runtime.prompt("fit_requirements"), context, _Requirements
        )
        requirements = extracted.value.requirements
        tokens += extracted.tokens
    matches, evidence, reasons, used = await match_requirements(requirements, context, runtime)
    return {
        "fit_report": build_report(matches, reasons),
        "evidence": evidence,
        "tokens_used": tokens + used,
    }


async def reconcile(state: BidState, runtime: AgentRuntime) -> dict[str, Any]:
    """Check the Fit Matcher's matrix against the Analyst's JobSpec and fill the gaps."""
    spec: JobSpec = state["job_spec"]
    report = state["fit_report"]
    covered = {match.requirement.lower() for match in report.matches}
    gaps = [skill for skill in spec.required_skills if skill.lower() not in covered]
    if not gaps:
        return {}
    context = delimit_untrusted("job_post", job_text(state["job"]))
    matches, evidence, reasons, tokens = await match_requirements(gaps, context, runtime)
    return {
        "fit_report": build_report([*report.matches, *matches], [*report.reasons, *reasons]),
        "evidence": evidence,
        "tokens_used": tokens,
    }
