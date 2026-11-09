from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from devsim.app import create_app
from devsim.config import FaultConfig, RateLimitConfig, Settings, SimConfig

AI = {"Authorization": "Bearer token-ai"}
WEB = {"Authorization": "Bearer token-web"}
ADMIN = {"Authorization": "Bearer token-admin"}


class FakeTime:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


def make_settings(**config: Any) -> Settings:
    return Settings(
        config=SimConfig(start_time="2026-11-01T00:00:00Z", **config),
        api_tokens={"token-ai": "ai_ml_engineer", "token-web": "fullstack_developer"},
        admin_token="token-admin",
    )


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


@pytest.fixture
def build_app(fake_time: FakeTime) -> Callable[..., FastAPI]:
    def build(
        rpm: int = 10_000,
        faults: FaultConfig | None = None,
        seed_dir: Path | None = None,
    ) -> FastAPI:
        settings = make_settings(
            rate_limit=RateLimitConfig(requests_per_minute=rpm),
            faults=faults or FaultConfig(),
            seed_dir=seed_dir,
        )
        return create_app(settings, monotonic=fake_time)

    return build


@pytest.fixture
def client(build_app: Callable[..., FastAPI]) -> TestClient:
    return TestClient(build_app())


def key(value: str) -> dict[str, str]:
    return {"Idempotency-Key": f"key-{value:0>8}"}


def draft_body(job_id: str = "job-001", answers: int = 1, bid: float = 1200) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "cover_letter": "I built a churn baseline with scikit-learn.",
        "screening_answers": ["Use precision-recall curves."] * answers,
        "bid_amount": bid,
    }


def create_draft(client: TestClient, name: str = "d1", job_id: str = "job-001") -> dict[str, Any]:
    response = client.post(
        "/v1/proposals/drafts",
        json=draft_body(job_id),
        headers={**AI, **key(name)},
    )
    assert response.status_code == 201, response.text
    return response.json()


def balance(client: TestClient, headers: dict[str, str] = AI) -> dict[str, Any]:
    return client.get("/v1/connects/balance", headers=headers).json()
