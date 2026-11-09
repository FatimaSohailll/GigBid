"""Scout: find new jobs, remove duplicates and hand each job to its bid thread."""

import re
from typing import Any

from agent.runtime import AgentRuntime
from agent.state import BidState, ThreadStatus


def _fingerprint(job: dict[str, Any]) -> set[str]:
    return set(re.findall(r"[a-z0-9+#.]+", f"{job['title']} {job['description']}".lower()))


def similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    a, b = _fingerprint(left), _fingerprint(right)
    return len(a & b) / len(a | b) if a | b else 0.0


def find_duplicate(
    job: dict[str, Any],
    known: list[dict[str, Any]],
    threshold: float,
) -> str | None:
    for other in known:
        same_client = other.get("client_id") == job.get("client_id")
        if other["job_id"] != job["job_id"] and same_client and similarity(job, other) >= threshold:
            return str(other["job_id"])
    return None


async def discover_jobs(
    runtime: AgentRuntime,
    query: str | None,
    skills: list[str],
    seen: list[dict[str, Any]],
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Run one saved search and return jobs that are neither seen nor duplicates."""
    page = await runtime.tools.call(
        "scout", "search_jobs", {"query": query, "skills": skills, "limit": limit}
    )
    known = list(seen)
    known_ids = {job["job_id"] for job in seen}
    fresh = []
    for job in page["items"]:
        threshold = runtime.config.graph.duplicate_similarity
        if job["job_id"] in known_ids or find_duplicate(job, known, threshold):
            continue
        fresh.append(job)
        known.append(job)
        known_ids.add(job["job_id"])
    return fresh


async def run(state: BidState, runtime: AgentRuntime) -> dict[str, Any]:
    job = state.get("job") or await runtime.tools.call(
        "scout", "get_job", {"job_id": state["job_id"]}
    )
    duplicate = find_duplicate(
        job, state.get("recent_jobs", []), runtime.config.graph.duplicate_similarity
    )
    if duplicate:
        return {"job": job, "duplicate_of": duplicate, "status": ThreadStatus.SKIPPED}
    return {"job": job, "duplicate_of": None, "status": ThreadStatus.RUNNING}
