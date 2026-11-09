import asyncio
import json
from collections.abc import Callable
from importlib import resources
from pathlib import Path

import httpx
import pytest
from conftest import ADMIN, AI, WEB, FakeTime, balance, create_draft, draft_body, key
from fastapi import FastAPI
from fastapi.testclient import TestClient

from devsim.config import FaultConfig


def submit(client: TestClient, proposal_id: str, name: str = "s1") -> httpx.Response:
    return client.post(f"/v1/proposals/{proposal_id}/submit", headers={**AI, **key(name)})


def test_health_declares_it_is_not_official(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["official_gigsim"] is False
    assert response.headers["X-Simulator"] == "gigbid-devsim"


def test_error_responses_are_also_marked_as_simulator(client: TestClient) -> None:
    assert client.get("/v1/jobs").headers["X-Simulator"] == "gigbid-devsim"


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic token-ai"}],
)
def test_invalid_credentials_are_rejected(client: TestClient, headers: dict[str, str]) -> None:
    response = client.get("/v1/jobs", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_search_returns_newest_first_and_hides_future_jobs(client: TestClient) -> None:
    ids = [job["job_id"] for job in client.get("/v1/jobs", headers=AI).json()["items"]]
    assert ids == ["job-006", "job-005", "job-004", "job-003", "job-002", "job-001"]
    assert client.get("/v1/jobs/job-007", headers=AI).status_code == 404


def test_advancing_time_releases_new_jobs(client: TestClient) -> None:
    client.post("/admin/clock/advance", json={"minutes": 361}, headers=ADMIN)
    assert client.get("/v1/jobs/job-007", headers=AI).status_code == 200


def test_search_filters_by_text_and_skill(client: TestClient) -> None:
    by_text = client.get("/v1/jobs", params={"query": "react profiling"}, headers=AI).json()
    assert [job["job_id"] for job in by_text["items"]] == ["job-006"]
    by_skill = client.get("/v1/jobs", params={"skills": ["postgres"]}, headers=AI).json()
    assert [job["job_id"] for job in by_skill["items"]] == ["job-004"]


def test_pagination_walks_every_job_once(client: TestClient) -> None:
    seen: list[str] = []
    cursor = None
    while True:
        params = {"limit": 4, **({"cursor": cursor} if cursor else {})}
        page = client.get("/v1/jobs", params=params, headers=AI).json()
        seen.extend(job["job_id"] for job in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 6


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"cursor": "abc"}])
def test_bad_paging_is_rejected(client: TestClient, params: dict[str, object]) -> None:
    assert client.get("/v1/jobs", params=params, headers=AI).status_code == 400


def test_hidden_client_preferences_are_never_returned(client: TestClient) -> None:
    response = client.get("/v1/clients/client-001/history", headers=AI)
    assert response.status_code == 200
    body = json.dumps(response.json())
    assert "hidden_preferences" not in body
    assert "budget_ceiling" not in body


def test_draft_costs_nothing(client: TestClient) -> None:
    proposal = create_draft(client)
    assert proposal["status"] == "drafted"
    assert balance(client) == {"freelancer_id": "ai_ml_engineer", "balance": 80, "spent": 0}


def test_draft_for_unknown_job_is_404(client: TestClient) -> None:
    response = client.post(
        "/v1/proposals/drafts",
        json=draft_body("job-404"),
        headers={**AI, **key("x")},
    )
    assert response.status_code == 404


def test_validation_errors_do_not_echo_input(client: TestClient) -> None:
    body = {**draft_body(), "bid_amount": -1, "cover_letter": "secret-text"}
    response = client.post("/v1/proposals/drafts", json=body, headers={**AI, **key("x")})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "secret-text" not in response.text


def test_writes_require_an_idempotency_key(client: TestClient) -> None:
    response = client.post("/v1/proposals/drafts", json=draft_body(), headers=AI)
    assert response.status_code == 422


def test_submit_spends_the_job_cost(client: TestClient) -> None:
    proposal = create_draft(client)
    response = submit(client, proposal["proposal_id"])
    assert response.status_code == 200
    assert response.json()["status"] == "submitted"
    assert response.json()["connects_spent"] == 8
    assert response.headers["Idempotent-Replay"] == "false"
    assert balance(client) == {"freelancer_id": "ai_ml_engineer", "balance": 72, "spent": 8}


def test_retry_with_same_key_replays_without_spending_again(client: TestClient) -> None:
    proposal = create_draft(client)
    first = submit(client, proposal["proposal_id"])
    second = submit(client, proposal["proposal_id"])
    assert second.status_code == 200
    assert second.json() == first.json()
    assert second.headers["Idempotent-Replay"] == "true"
    assert balance(client)["spent"] == 8


def test_same_key_with_different_request_is_rejected(client: TestClient) -> None:
    first = create_draft(client, "d1")
    second = create_draft(client, "d2", "job-003")
    assert submit(client, first["proposal_id"], "shared").status_code == 200
    response = submit(client, second["proposal_id"], "shared")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "idempotency_key_reused"
    assert balance(client)["spent"] == 8


def test_new_key_cannot_resubmit_the_same_proposal(client: TestClient) -> None:
    proposal = create_draft(client)
    submit(client, proposal["proposal_id"], "s1")
    response = submit(client, proposal["proposal_id"], "s2")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_submitted"
    assert balance(client)["spent"] == 8


def test_second_proposal_for_same_job_cannot_be_submitted(client: TestClient) -> None:
    first = create_draft(client, "d1")
    second = create_draft(client, "d2")
    submit(client, first["proposal_id"], "s1")
    response = submit(client, second["proposal_id"], "s2")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_bid"


def test_insufficient_connects_changes_nothing(
    build_app: Callable[..., FastAPI],
    tmp_path: Path,
) -> None:
    seed = resources.files("devsim").joinpath("seed/core")
    for name in ("jobs.json", "clients.json"):
        (tmp_path / name).write_text(seed.joinpath(name).read_text("utf-8"), encoding="utf-8")
    poor = [{"freelancer_id": "ai_ml_engineer", "display_name": "P", "starting_connects": 7}]
    rich = [{"freelancer_id": "fullstack_developer", "display_name": "R", "starting_connects": 9}]
    (tmp_path / "freelancers.json").write_text(json.dumps(poor + rich), encoding="utf-8")

    client = TestClient(build_app(seed_dir=tmp_path))
    proposal = create_draft(client)  # job-001 costs 8; the balance is 7
    response = submit(client, proposal["proposal_id"])

    assert response.status_code == 402
    assert response.json()["error"]["code"] == "insufficient_connects"
    assert balance(client) == {"freelancer_id": "ai_ml_engineer", "balance": 7, "spent": 0}
    stored = client.get(f"/v1/proposals/{proposal['proposal_id']}", headers=AI).json()
    assert stored["status"] == "drafted"


def test_screening_answer_count_is_enforced(client: TestClient) -> None:
    drafted = client.post(
        "/v1/proposals/drafts",
        json=draft_body("job-001", answers=0),
        headers={**AI, **key("d")},
    ).json()
    response = submit(client, drafted["proposal_id"])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "screening_answers_mismatch"
    assert balance(client)["spent"] == 0


def test_failure_before_commit_has_no_effect(build_app: Callable[..., FastAPI]) -> None:
    app = build_app()
    client = TestClient(app)
    proposal = create_draft(client)
    client.put("/admin/faults", json={"error_rate": 1, "post_commit_error_rate": 0}, headers=ADMIN)
    response = submit(client, proposal["proposal_id"])
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    client.put("/admin/faults", json={"error_rate": 0, "post_commit_error_rate": 0}, headers=ADMIN)
    assert balance(client)["spent"] == 0


def test_failure_after_commit_is_safe_to_retry(client: TestClient) -> None:
    proposal = create_draft(client)
    client.put("/admin/faults", json={"error_rate": 0, "post_commit_error_rate": 1}, headers=ADMIN)
    first = submit(client, proposal["proposal_id"])
    assert first.status_code == 503
    assert balance(client)["spent"] == 8  # the write was applied despite the error
    retry = submit(client, proposal["proposal_id"])
    assert retry.status_code == 200
    assert retry.headers["Idempotent-Replay"] == "true"
    assert balance(client)["spent"] == 8


def test_fault_sequence_is_reproducible(build_app: Callable[..., FastAPI]) -> None:
    def statuses() -> list[int]:
        client = TestClient(build_app(faults=FaultConfig(seed=7, error_rate=0.5)))
        return [client.get("/v1/jobs", headers=AI).status_code for _ in range(30)]

    first = statuses()
    assert first == statuses()
    assert {200, 503} == set(first)


def test_rate_limit_is_per_user_and_recovers(
    build_app: Callable[..., FastAPI],
    fake_time: FakeTime,
) -> None:
    client = TestClient(build_app(rpm=3))
    for _ in range(3):
        assert client.get("/v1/jobs", headers=AI).status_code == 200
    limited = client.get("/v1/jobs", headers=AI)
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"
    assert client.get("/v1/jobs", headers=WEB).status_code == 200
    fake_time.value += 60
    assert client.get("/v1/jobs", headers=AI).status_code == 200


def test_tenants_cannot_see_each_others_work(client: TestClient) -> None:
    proposal = create_draft(client)
    proposal_id = proposal["proposal_id"]
    submit(client, proposal_id)
    message = client.post(
        f"/admin/proposals/{proposal_id}/client-message",
        json={"body": "Hello"},
        headers=ADMIN,
    ).json()
    conversation_id = message["conversation_id"]

    assert client.get(f"/v1/proposals/{proposal_id}", headers=WEB).status_code == 404
    assert client.get("/v1/proposals", headers=WEB).json() == []
    assert client.get("/v1/conversations", headers=WEB).json() == []
    assert client.get("/v1/events", headers=WEB).json() == []
    assert (
        client.get(f"/v1/conversations/{conversation_id}/messages", headers=WEB).status_code == 404
    )
    response = client.post(f"/v1/proposals/{proposal_id}/submit", headers={**WEB, **key("w")})
    assert response.status_code == 404


def test_client_reply_opens_thread_and_freelancer_can_answer(client: TestClient) -> None:
    proposal_id = create_draft(client)["proposal_id"]
    submit(client, proposal_id)
    received = client.post(
        f"/admin/proposals/{proposal_id}/client-message",
        json={"body": "Can you start Monday?"},
        headers=ADMIN,
    )
    assert received.status_code == 201
    assert client.get(f"/v1/proposals/{proposal_id}", headers=AI).json()["status"] == "replied"

    conversation_id = received.json()["conversation_id"]
    url = f"/v1/conversations/{conversation_id}/messages"
    headers = {**AI, **key("m1")}
    first = client.post(url, json={"body": "Yes, Monday works."}, headers=headers)
    retry = client.post(url, json={"body": "Yes, Monday works."}, headers=headers)
    assert first.status_code == 201
    assert retry.json() == first.json()
    assert retry.headers["Idempotent-Replay"] == "true"
    thread = client.get(url, headers=AI).json()
    assert [item["sender"] for item in thread] == ["client", "freelancer"]


def test_draft_cannot_receive_a_client_message(client: TestClient) -> None:
    proposal_id = create_draft(client)["proposal_id"]
    response = client.post(
        f"/admin/proposals/{proposal_id}/client-message",
        json={"body": "Hi"},
        headers=ADMIN,
    )
    assert response.status_code == 409


def test_events_feed_tracks_the_pipeline(client: TestClient) -> None:
    proposal_id = create_draft(client)["proposal_id"]
    submit(client, proposal_id)
    client.post(
        f"/admin/proposals/{proposal_id}/status",
        json={"status": "viewed"},
        headers=ADMIN,
    )
    events = client.get("/v1/events", headers=AI).json()
    assert [event["type"] for event in events] == [
        "proposal_drafted",
        "proposal_submitted",
        "proposal_status_changed",
    ]
    newest = client.get("/v1/events", params={"after": events[1]["event_id"]}, headers=AI).json()
    assert [event["status"] for event in newest] == ["viewed"]


@pytest.mark.parametrize(
    ("steps", "target"),
    [(["viewed"], "submitted"), (["hired"], "viewed"), (["lost"], "viewed"), ([], "drafted")],
)
def test_invalid_status_changes_are_rejected(
    client: TestClient,
    steps: list[str],
    target: str,
) -> None:
    proposal_id = create_draft(client)["proposal_id"]
    submit(client, proposal_id)
    url = f"/admin/proposals/{proposal_id}/status"
    for step in steps:
        assert client.post(url, json={"status": step}, headers=ADMIN).status_code == 200
    assert client.post(url, json={"status": target}, headers=ADMIN).status_code == 409


def test_admin_routes_need_the_admin_token(client: TestClient) -> None:
    assert client.post("/admin/reset", headers=AI).status_code == 403
    assert client.post("/admin/reset").status_code == 403


def test_reset_restores_the_seed_state(client: TestClient) -> None:
    proposal_id = create_draft(client)["proposal_id"]
    submit(client, proposal_id)
    assert client.post("/admin/reset", headers=ADMIN).status_code == 204
    assert balance(client)["spent"] == 0
    assert client.get("/v1/proposals", headers=AI).json() == []


def test_concurrent_identical_submits_spend_once(build_app: Callable[..., FastAPI]) -> None:
    app = build_app()
    proposal_id = create_draft(TestClient(app))["proposal_id"]

    async def run() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://sim") as async_client:
            calls = [
                async_client.post(
                    f"/v1/proposals/{proposal_id}/submit",
                    headers={**AI, **key("race")},
                )
                for _ in range(25)
            ]
            return list(await asyncio.gather(*calls))

    responses = asyncio.run(run())
    assert {response.status_code for response in responses} == {200}
    replays = [response.headers["Idempotent-Replay"] for response in responses]
    assert replays.count("false") == 1
    assert balance(TestClient(app))["spent"] == 8
