from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import ADMIN, AI, create_draft, key
from fastapi.testclient import TestClient

from devsim.client_sim import react, reply_probability
from devsim.models import Budget, ClientRecord, Job, Proposal, ProposalStatus

NOW = datetime(2026, 11, 1, tzinfo=UTC)


@pytest.fixture
def client_app(client: TestClient) -> TestClient:
    return client


def job() -> Job:
    return Job(
        job_id="j1",
        client_id="c1",
        title="Churn model",
        description="Build it.",
        category="ml",
        required_skills=["Python"],
        budget=Budget.model_validate({"min": 500, "max": 1000, "currency": "USD"}),
        duration_days=10,
        connects_cost=8,
        posted_at=NOW,
    )


def buyer(**preferences: Any) -> ClientRecord:
    return ClientRecord(
        client_id="c1",
        display_name="C",
        industry="telecom",
        completed_projects=1,
        average_rating=5,
        payment_verified=True,
        total_spent_usd=1,
        hidden_preferences={"reply_propensity": 0.5, **preferences},
    )


def proposal(
    letter: str = "Your churn needs Python. I lifted recall 31%.", bid: float = 800
) -> Proposal:
    return Proposal(
        proposal_id="p1",
        job_id="j1",
        freelancer_id="f1",
        cover_letter=letter,
        screening_answers=[],
        bid_amount=bid,
        status=ProposalStatus.SUBMITTED,
        created_at=NOW,
    )


def test_a_bid_over_the_hidden_ceiling_rarely_gets_a_reply() -> None:
    fine = reply_probability(job(), buyer(budget_ceiling_usd=900), proposal())
    too_high = reply_probability(job(), buyer(budget_ceiling_usd=700), proposal())
    assert too_high < fine / 2


def test_generic_openings_lose_replies_only_with_clients_who_mind() -> None:
    generic = proposal("Dear client, I am skilled in Python.")
    assert reply_probability(job(), buyer(dislikes_generic_openings=True), generic) < (
        reply_probability(job(), buyer(dislikes_generic_openings=False), generic)
    )


def test_proof_raises_replies_from_clients_who_value_evidence() -> None:
    assert reply_probability(job(), buyer(values_evidence=True), proposal()) > (
        reply_probability(job(), buyer(values_evidence=True), proposal("I know Python well."))
    )


def test_scam_clients_never_reply() -> None:
    assert reply_probability(job(), buyer(scam_actor=True), proposal()) == 0


def test_the_same_seed_gives_the_same_reaction() -> None:
    assert react(job(), buyer(), proposal(), 7) == react(job(), buyer(), proposal(), 7)


def test_admin_route_records_the_outcome(client_app: TestClient) -> None:
    draft = create_draft(client_app)
    proposal_id = draft["proposal_id"]
    client_app.post(f"/v1/proposals/{proposal_id}/submit", headers={**AI, **key("s1")})
    response = client_app.post(
        f"/admin/proposals/{proposal_id}/simulate-client", json={"seed": 1}, headers=ADMIN
    )
    assert response.status_code == 200
    body = response.json()
    assert body["official_gigsim"] is False
    expected = {"hired"} if body["hired"] else {"replied"} if body["replied"] else {"viewed"}
    assert {body["status"]} == expected


def test_only_a_submitted_proposal_can_be_simulated(client_app: TestClient) -> None:
    draft = create_draft(client_app)
    response = client_app.post(
        f"/admin/proposals/{draft['proposal_id']}/simulate-client", json={"seed": 1}, headers=ADMIN
    )
    assert response.status_code == 409
