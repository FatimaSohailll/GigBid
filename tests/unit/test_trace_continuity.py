"""E3: approving a waiting run continues that run's trace instead of starting a new one."""

from typing import Any

import httpx

from apps.api.main import create_app
from apps.api.services import current_user

RUN_TRACE = "b" * 32


class Repository:
    async def run(self, user_id: str, thread_id: str) -> dict[str, Any]:
        return {"kind": "bid", "ref_id": "job-001", "trace_id": RUN_TRACE}

    async def claim_approval(self, user_id: str, thread_id: str) -> bool:
        return True


class Queue:
    def __init__(self) -> None:
        self.jobs: list[tuple[Any, ...]] = []

    async def enqueue_job(self, *args: Any, **kwargs: Any) -> None:
        self.jobs.append(args)


class Services:
    def __init__(self) -> None:
        self.repository = Repository()
        self.queue = Queue()


async def test_an_approval_answers_with_the_runs_trace_id() -> None:
    services = Services()
    app = create_app(services)  # type: ignore[arg-type]
    app.state.services = services
    app.dependency_overrides[current_user] = lambda: {"id": "u1"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/approvals/thread-1",
            json={"action": "approve"},
            headers={"x-trace-id": "c" * 32},  # the browser's own request id
        )
    assert response.status_code == 202
    assert response.headers["x-trace-id"] == RUN_TRACE
    assert services.queue.jobs[0][5] == RUN_TRACE  # the worker resumes under the same id
