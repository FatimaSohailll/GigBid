"""The API and worker together, with real Postgres, Redis, MCP servers and DevSim.

Only model replies are scripted. Needs TEST_DATABASE_URL and TEST_REDIS_URL.
"""

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from arq import create_pool
from arq.connections import RedisSettings
from arq.worker import Worker, func
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore

from agent.checkpoint import checkpoint_serde
from agent.graphs.bid_graph import build_bid_graph
from agent.graphs.conversation_graph import build_conversation_graph
from agent.memory.store import LongTermMemory
from agent.runtime import AgentRuntime
from apps.api.db import Repository, open_pool
from apps.api.main import create_app
from apps.api.services import ApiServices
from tests.support.graph_stack import GraphStack, fast_config, start_stack
from tests.support.rag_fakes import memory_components
from tests.support.scripted_llm import ScriptedLlm
from worker.tasks import (
    WorkerServices,
    delete_evidence,
    ingest_upload,
    run_thread,
    scout_search,
    sync_user,
)

DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
REDIS_URL = os.environ.get("TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(
    not (DATABASE_URL and REDIS_URL),
    reason="TEST_DATABASE_URL and TEST_REDIS_URL are not set",
)
PASSWORD = "correct horse battery"


@pytest.fixture
def event_loop_policy() -> Any:
    # psycopg's async mode cannot use Windows' default event loop.
    if os.name == "nt":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[GraphStack]:
    started = start_stack(tmp_path)
    yield started
    started.stop()


@dataclass
class System:
    api: httpx.AsyncClient
    app_services: ApiServices
    worker_services: WorkerServices
    queue: Any
    stack: GraphStack
    llm: ScriptedLlm

    async def drain(self) -> None:
        worker = Worker(
            functions=[
                func(run_thread, max_tries=200),
                func(scout_search, max_tries=200),
                sync_user,
                ingest_upload,
                delete_evidence,
            ],
            redis_pool=self.queue,
            ctx={"services": self.worker_services, "redis": self.queue},
            burst=True,
            handle_signals=False,
            poll_delay=0.05,
        )
        await worker.main()

    async def signup(self) -> dict[str, Any]:
        response = await self.api.post(
            "/api/auth/signup",
            json={
                "email": f"{uuid.uuid4().hex}@example.com",
                "password": PASSWORD,
                "persona": "ai_ml_engineer",
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    async def add_job(self, user: dict[str, Any], job_id: str = "job-001") -> None:
        job = httpx.get(
            f"{self.stack.simulator.url}/v1/jobs/{job_id}",
            headers={"Authorization": "Bearer sim-ai"},
        ).json()
        await self.app_services.repository.add_job(user["id"], job)

    async def analyze_to_approval(self, user: dict[str, Any]) -> str:
        await self.add_job(user)
        started = await self.api.post("/api/jobs/job-001/analyze")
        assert started.status_code == 202, started.text
        await self.drain()
        return started.json()["thread_id"]

    def spent(self) -> int:
        return httpx.get(
            f"{self.stack.simulator.url}/v1/connects/balance",
            headers={"Authorization": "Bearer sim-ai"},
        ).json()["spent"]


@pytest.fixture
async def system(stack: GraphStack, tmp_path: Path) -> AsyncIterator[System]:
    config = fast_config()
    config = config.model_copy(
        update={"worker": config.worker.model_copy(update={"service_retry_seconds": 0.2})}
    )
    pool = open_pool(str(DATABASE_URL))
    await pool.open()
    repository = Repository(pool)
    await repository.setup()
    saver = AsyncPostgresSaver(pool, serde=checkpoint_serde())  # type: ignore[arg-type]
    await saver.setup()
    store = AsyncPostgresStore(pool)  # type: ignore[arg-type]
    await store.setup()
    queue = await create_pool(RedisSettings.from_dsn(str(REDIS_URL)))
    await queue.flushdb()
    memory = LongTermMemory(store, config.memory)
    llm = ScriptedLlm()
    runtime = AgentRuntime(config, llm, stack.gateway(config), memory)
    components = memory_components(tmp_path)
    services = ApiServices(
        config=config,
        repository=repository,
        queue=queue,
        bid_graph=build_bid_graph(runtime, saver),
        conversation_graph=build_conversation_graph(runtime, saver),
        memory=memory,
        vault=None,
        upload_dir=tmp_path / "uploads",
        marketplace_health_url=f"{stack.marketplace.url}/health",
        corpus=components.corpus,
    )
    worker_services = WorkerServices(
        repository, queue, lambda _: runtime, saver, lambda: components
    )
    app = create_app(services)
    app.state.services = services
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield System(client, services, worker_services, queue, stack, llm)
    await queue.aclose()
    await pool.close()


async def test_requests_need_a_session(system: System) -> None:
    assert (await system.api.get("/api/jobs")).status_code == 401
    assert (await system.api.get("/api/approvals")).status_code == 401


async def test_signup_login_and_wrong_password(system: System) -> None:
    user = await system.signup()
    assert "password_hash" not in user
    assert (await system.api.get("/api/auth/me")).json()["email"] == user["email"]
    wrong = await system.api.post(
        "/api/auth/login", json={"email": user["email"], "password": "nope"}
    )
    assert wrong.status_code == 401


async def test_job_goes_from_inbox_to_submitted_once(system: System) -> None:
    await system.signup()
    user = (await system.api.get("/api/auth/me")).json()
    thread = await system.analyze_to_approval(user)

    inbox = (await system.api.get("/api/jobs")).json()
    assert inbox[0]["action"] == "bid" and inbox[0]["run_status"] == "awaiting_approval"
    detail = (await system.api.get("/api/jobs/job-001")).json()
    assert detail["fit_report"]["matches"] and detail["price_quote"]["rationale"]

    # A page refresh reads the pending approval back from the checkpoint.
    pending = (await system.api.get("/api/approvals")).json()
    assert [item["thread_id"] for item in pending] == [thread]
    approval = (await system.api.get(f"/api/approvals/{thread}")).json()
    assert approval["payload"]["dry_run_preview"]["connects_cost"] == 8
    assert system.spent() == 0

    decided = await system.api.post(f"/api/approvals/{thread}", json={"action": "approve"})
    assert decided.status_code == 202
    second = await system.api.post(f"/api/approvals/{thread}", json={"action": "approve"})
    assert second.status_code == 409
    await system.drain()

    assert system.spent() == 8
    assert (await system.api.get(f"/api/runs/{thread}")).json()["status"] == "submitted"
    cards = (await system.api.get("/api/pipeline")).json()
    assert [card["status"] for card in cards] == ["submitted"]
    analytics = (await system.api.get("/api/analytics")).json()
    assert analytics["by_category"][0]["connects_spent"] == 8


async def test_progress_stream_replays_and_resumes(system: System) -> None:
    await system.signup()
    user = (await system.api.get("/api/auth/me")).json()
    thread = await system.analyze_to_approval(user)

    response = await system.api.get(f"/api/runs/{thread}/stream")
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    nodes = [event["node"] for event in events if event["type"] == "node"]
    assert {"analyst", "risk_assessor", "fit_matcher", "writer"} <= set(nodes)
    assert events[-1] == {"type": "run_end", "status": "awaiting_approval"}

    ids = [line[4:] for line in response.text.splitlines() if line.startswith("id: ")]
    resumed = await system.api.get(f"/api/runs/{thread}/stream", headers={"Last-Event-ID": ids[-2]})
    assert resumed.text.count("data: ") == 1


async def test_users_cannot_see_each_others_work(system: System) -> None:
    await system.signup()
    owner = (await system.api.get("/api/auth/me")).json()
    thread = await system.analyze_to_approval(owner)

    system.api.cookies.clear()
    await system.signup()
    assert (await system.api.get("/api/jobs")).json() == []
    assert (await system.api.get(f"/api/approvals/{thread}")).status_code == 404
    assert (await system.api.get(f"/api/runs/{thread}/stream")).status_code == 404
    denied = await system.api.post(f"/api/approvals/{thread}", json={"action": "approve"})
    assert denied.status_code == 404
    assert system.spent() == 0


async def test_invalid_decision_is_refused_without_touching_the_thread(system: System) -> None:
    await system.signup()
    user = (await system.api.get("/api/auth/me")).json()
    thread = await system.analyze_to_approval(user)
    refused = await system.api.post(f"/api/approvals/{thread}", json={"action": "reject"})
    assert refused.status_code == 422
    assert (await system.api.get(f"/api/runs/{thread}")).json()["status"] == "awaiting_approval"


async def test_repeating_a_finished_task_does_not_submit_again(system: System) -> None:
    await system.signup()
    user = (await system.api.get("/api/auth/me")).json()
    thread = await system.analyze_to_approval(user)
    await system.api.post(f"/api/approvals/{thread}", json={"action": "approve"})
    await system.drain()

    # What a retried job does after a worker restart.
    ctx = {"services": system.worker_services, "redis": system.queue}
    await run_thread(
        ctx, "bid", thread, user["id"], "job-001", "trace", decision={"action": "approve"}
    )
    assert system.spent() == 8


async def test_outage_shows_banner_queues_work_and_recovers(system: System) -> None:
    await system.signup()
    user = (await system.api.get("/api/auth/me")).json()
    await system.add_job(user)
    assert (await system.api.get("/api/status")).json()["marketplace"] == "ok"

    system.stack.marketplace.stop()
    started = (await system.api.post("/api/jobs/job-001/analyze")).json()
    worker = asyncio.create_task(system.drain())
    for _ in range(200):
        run = (await system.api.get(f"/api/runs/{started['thread_id']}")).json()
        if run["status"] == "waiting_for_service":
            break
        await asyncio.sleep(0.05)
    assert run["status"] == "waiting_for_service"
    status = (await system.api.get("/api/status")).json()
    assert status["marketplace"] == "unavailable" and "queued" in status["message"]

    system.stack.restart_marketplace()
    await asyncio.wait_for(worker, 60)
    run = (await system.api.get(f"/api/runs/{started['thread_id']}")).json()
    assert run["status"] == "awaiting_approval"
    assert (await system.api.get("/api/status")).json()["marketplace"] == "ok"


async def test_scouting_and_marketplace_sync(system: System) -> None:
    await system.signup()
    user = (await system.api.get("/api/auth/me")).json()
    too_often = await system.api.post(
        "/api/searches", json={"query": "churn", "interval_minutes": 1}
    )
    assert too_often.status_code == 422
    added = await system.api.post("/api/searches", json={"query": "churn", "interval_minutes": 60})
    assert added.status_code == 201
    await system.api.post("/api/jobs/refresh")
    await system.drain()
    inbox = (await system.api.get("/api/jobs")).json()
    first = next(job for job in inbox if job["job_id"] == "job-001")
    thread = first["thread_id"]
    assert first["run_status"] == "awaiting_approval"

    await system.api.post(f"/api/approvals/{thread}", json={"action": "approve"})
    await system.drain()
    card = (await system.api.get("/api/pipeline")).json()[0]

    url = system.stack.simulator.url
    httpx.post(
        f"{url}/admin/proposals/{card['proposal_id']}/client-message",
        json={"body": "Can you start Monday?"},
        headers={"Authorization": "Bearer sim-admin"},
    )
    ctx = {"services": system.worker_services, "redis": system.queue}
    assert await sync_user(ctx, user["id"]) > 0
    await system.drain()
    assert (await system.api.get("/api/pipeline")).json()[0]["status"] == "replied"
    conversation = (await system.api.get("/api/conversations")).json()[0]
    assert conversation["status"] == "awaiting_approval"
    detail = (await system.api.get(f"/api/conversations/{conversation['conversation_id']}")).json()
    assert detail["pending_approval"]["reply"]["body"]


async def test_settings_memory_and_upload(system: System) -> None:
    await system.signup()
    user = (await system.api.get("/api/auth/me")).json()
    updated = await system.api.put(
        "/api/settings",
        json={"floor_usd": 300, "weekly_connects_budget": 40, "autonomy": "auto_skip_low_fit"},
    )
    assert updated.json()["autonomy"] == "auto_skip_low_fit"

    edit = {"before": "We are passionate engineers.", "after": "We are engineers."}
    for number in range(3):
        await system.app_services.memory.record_edits(user["id"], f"job-{number}", [edit])
    memory = (await system.api.get("/api/memory")).json()
    assert memory["style"][0]["key"] == "avoid_word:passionate"
    await system.api.delete("/api/memory/style/avoid_word:passionate")
    assert (await system.api.get("/api/memory")).json()["style"] == []

    rejected = await system.api.post(
        "/api/portfolio/uploads",
        data={"title": "X"},
        files={"file": ("malware.exe", b"x")},
    )
    assert rejected.status_code == 415
    uploaded = await system.api.post(
        "/api/portfolio/uploads",
        data={"title": "Billing rebuild"},
        files={"file": ("work.md", b"I rebuilt a billing system with Stripe.")},
    )
    assert uploaded.status_code == 202
    await system.drain()
    assert (await system.api.get("/api/portfolio/uploads")).json()[0]["status"] == "indexed"

    described = await system.api.post(
        "/api/portfolio/uploads",
        data={
            "title": "Won bid",
            "item_type": "proposal",
            "skills": "Stripe, Postgres",
            "date": "2026-03",
            "client_industry": "fintech",
            "outcome": "won",
        },
        files={"file": ("bid.md", b"I rebuilt billing for a fintech client.")},
    )
    assert described.status_code == 202
    await system.drain()
    items = {item["id"]: item for item in (await system.api.get("/api/portfolio/evidence")).json()}
    stored = items[f"proposal:upload:{described.json()['id']}"]
    assert stored["outcome"] == "won"
    assert stored["skills"] == ["Stripe", "Postgres"]
    assert stored["client_industry"] == "fintech"

    invalid = await system.api.post(
        "/api/portfolio/uploads",
        data={"title": "X", "outcome": "maybe"},
        files={"file": ("a.md", b"text")},
    )
    assert invalid.status_code == 422

    assert (await system.api.delete(f"/api/portfolio/evidence/{stored['id']}")).status_code == 202
    await system.drain()
    remaining = (await system.api.get("/api/portfolio/evidence")).json()
    assert stored["id"] not in {item["id"] for item in remaining}
    assert (await system.api.delete(f"/api/portfolio/evidence/{stored['id']}")).status_code == 404


async def test_oauth_tokens_are_encrypted_at_rest(system: System) -> None:
    from cryptography.fernet import Fernet

    from apps.api.auth.security import TokenVault

    await system.signup()
    user = (await system.api.get("/api/auth/me")).json()
    repository = system.app_services.repository
    vault = TokenVault(Fernet.generate_key().decode(), repository)
    await vault.store(user["id"], "upwork", {"access_token": "plain-secret"})
    stored = await repository.oauth_ciphertext(user["id"], "upwork")
    assert stored is not None and b"plain-secret" not in stored
    assert await vault.load(user["id"], "upwork") == {"access_token": "plain-secret"}
