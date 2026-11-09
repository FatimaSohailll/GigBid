"""Job Analyst: extract a structured JobSpec from a job post."""

from typing import Any

from agent.guardrails import delimit_untrusted
from agent.handoffs import JobSpec
from agent.runtime import AgentRuntime
from agent.state import BidState


def job_text(job: dict[str, Any]) -> str:
    return (
        f"Title: {job['title']}\nDescription: {job['description']}\n"
        f"Listed skills: {', '.join(job.get('required_skills', []))}\n"
        f"Budget: {job['budget']}\nDuration days: {job.get('duration_days')}\n"
        f"Screening questions: {job.get('screening_questions', [])}"
    )


async def run(state: BidState, runtime: AgentRuntime) -> dict[str, Any]:
    job = state["job"]
    result = await runtime.llm.structured(
        "analyst",
        runtime.prompt("analyst"),
        delimit_untrusted("job_post", job_text(job)),
        JobSpec,
    )
    # Identity and the screening questions come from the marketplace record, not the model.
    spec = result.value.model_copy(
        update={
            "job_id": job["job_id"],
            "title": job["title"],
            "screening_questions": job.get("screening_questions", []),
        }
    )
    return {"job_spec": spec, "tokens_used": result.tokens}
