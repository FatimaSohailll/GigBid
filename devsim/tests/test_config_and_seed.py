import json
from pathlib import Path

import pytest
from conftest import make_settings
from pydantic import ValidationError

from devsim.app import create_app
from devsim.config import Settings, load_config, load_settings, parse_api_tokens
from devsim.seed_loader import load_seed


def test_packaged_default_config_loads() -> None:
    config = load_config()
    assert config.rate_limit.requests_per_minute == 60
    assert config.seed_dir is None


def test_unknown_config_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text('start_time: "2026-11-01T00:00:00Z"\nsurprise: 1\n', encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(path)


def test_parse_api_tokens() -> None:
    assert parse_api_tokens("a=one, b=two") == {"a": "one", "b": "two"}


@pytest.mark.parametrize("raw", ["", "  ,", "no-separator", "=freelancer", "token="])
def test_bad_tokens_fail_fast(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_api_tokens(raw)


def test_settings_require_tokens() -> None:
    with pytest.raises(ValueError):
        load_settings({})


def test_settings_repr_does_not_expose_tokens() -> None:
    settings = load_settings(
        {"DEVSIM_API_TOKENS": "api-secret=ai_ml_engineer", "DEVSIM_ADMIN_TOKEN": "admin-secret"}
    )
    assert "api-secret" not in repr(settings)
    assert "admin-secret" not in repr(settings)


def test_tokens_for_unknown_freelancers_are_rejected() -> None:
    base = make_settings()
    settings = Settings(config=base.config, api_tokens={"t": "nobody"})
    with pytest.raises(ValueError, match="unknown freelancers"):
        create_app(settings)


def test_packaged_seed_contains_the_required_job_kinds() -> None:
    seed = load_seed()
    assert len({job.category for job in seed.jobs}) == 3
    assert any("free" in job.description.lower() for job in seed.jobs)
    assert any("ignore your previous instructions" in job.description for job in seed.jobs)


def write_seed(tmp_path: Path, jobs: list[dict], clients: list[dict]) -> Path:
    (tmp_path / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
    (tmp_path / "clients.json").write_text(json.dumps(clients), encoding="utf-8")
    (tmp_path / "freelancers.json").write_text("[]", encoding="utf-8")
    return tmp_path


CLIENT = {
    "client_id": "c1",
    "display_name": "C",
    "industry": "x",
    "completed_projects": 0,
    "average_rating": 0,
    "payment_verified": False,
    "total_spent_usd": 0,
}
JOB = {
    "job_id": "j1",
    "client_id": "c1",
    "title": "T",
    "description": "D",
    "category": "x",
    "budget": {"min": 1, "max": 2},
    "duration_days": 1,
    "connects_cost": 1,
    "posted_at": "2026-10-01T00:00:00Z",
}


def test_seed_rejects_gold_labels_on_jobs(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        load_seed(write_seed(tmp_path, [{**JOB, "is_scam": True}], [CLIENT]))


def test_seed_rejects_orphan_jobs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown clients"):
        load_seed(write_seed(tmp_path, [{**JOB, "client_id": "nope"}], [CLIENT]))


def test_seed_rejects_duplicate_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Duplicate job"):
        load_seed(write_seed(tmp_path, [JOB, JOB], [CLIENT]))
