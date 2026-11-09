import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from devsim.app import create_app
from devsim.config import Settings, SimConfig
from devsim.datagen.catalog import load_catalog, mentions
from devsim.datagen.generate import generate
from devsim.datagen.jobs import BID_COVERAGE_THRESHOLD, DEV_MIX, INJECTIONS
from devsim.seed_loader import load_seed

PERSONAS = load_catalog()
TOKEN = {"Authorization": "Bearer t"}


@pytest.fixture(scope="module")
def data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("devsim")
    generate(out)
    return out


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_gold(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def dev_gold(data: Path) -> list[dict[str, Any]]:
    return [row for p in PERSONAS for row in read_gold(data / "dev_set" / f"{p.persona_id}.jsonl")]


def jobs_by_id(data: Path) -> dict[str, dict[str, Any]]:
    return {job["job_id"]: job for job in read_json(data / "marketplace" / "jobs.json")}


def test_generation_is_reproducible_and_seed_sensitive(tmp_path: Path) -> None:
    sizes = {"history": 50, "per_persona": 40, "stream": 20}
    first = generate(tmp_path / "a", seed=1, **sizes)
    second = generate(tmp_path / "b", seed=1, **sizes)
    different = generate(tmp_path / "c", seed=2, **sizes)
    assert first == second
    assert first["files"] != different["files"]


def test_manifest_declares_it_is_not_official(data: Path) -> None:
    manifest = read_json(data / "MANIFEST.json")
    assert manifest["official_gigsim"] is False
    assert manifest["counts"]["history"] == 1500


def test_policy_pack_is_labelled_as_a_stand_in(data: Path) -> None:
    text = (data / "policy_pack" / "marketplace_policy.md").read_text(encoding="utf-8")
    assert "not the instructor-provided" in text
    assert "POL-03" in text


@pytest.mark.parametrize("persona", PERSONAS, ids=lambda p: p.persona_id)
def test_corpus_has_the_required_shape(data: Path, persona: Any) -> None:
    root = data / "personas" / persona.persona_id
    assert 15 <= len(list((root / "case_studies").glob("*.md"))) <= 25
    assert len(list((root / "repos").glob("*/README.md"))) == len(persona.projects)
    assert len(read_json(root / "reviews.json")) >= len(persona.projects)
    proposals = read_json(root / "proposals.json")
    assert len(proposals) == 150
    assert {proposal["outcome"] for proposal in proposals} == {"won", "replied", "ignored"}


@pytest.mark.parametrize("persona", PERSONAS, ids=lambda p: p.persona_id)
def test_every_index_item_exists_with_metadata(data: Path, persona: Any) -> None:
    root = data / "personas" / persona.persona_id
    index = read_json(root / "index.json")
    assert len({item["item_id"] for item in index}) == len(index)
    for item in index:
        assert (root / item["path"]).is_file(), item["item_id"]
        if item["type"] != "cv":
            assert {"project", "client_industry", "date"} <= set(item)


@pytest.mark.parametrize("persona", PERSONAS, ids=lambda p: p.persona_id)
def test_cv_is_a_readable_pdf(data: Path, persona: Any) -> None:
    reader = PdfReader(data / "personas" / persona.persona_id / "cv.pdf")
    text = " ".join(" ".join(page.extract_text().split()) for page in reader.pages)
    assert persona.display_name in text
    assert persona.projects[0].cv in text


@pytest.mark.parametrize("persona", PERSONAS, ids=lambda p: p.persona_id)
def test_skills_appear_only_where_the_catalog_says(data: Path, persona: Any) -> None:
    root = data / "personas" / persona.persona_id
    for project in persona.projects:
        case = (root / "case_studies" / f"{project.slug}.md").read_text(encoding="utf-8")
        repo = root / "repos" / project.codename
        readme = (repo / "README.md").read_text(encoding="utf-8")
        code = next(p for p in repo.rglob("*") if p.is_file() and p.name != "README.md").read_text(
            encoding="utf-8"
        )
        for skill in project.explicit:
            assert mentions(skill, case) and mentions(skill, readme), (project.slug, skill)
        for skill in project.implied:
            assert not mentions(skill, case), (project.slug, skill)
        for skill in project.hidden:
            label = (project.slug, skill)
            assert mentions(skill.replace("-", "_"), code) or mentions(skill, code), label
            assert not mentions(skill, case) and not mentions(skill, readme), label


def test_one_project_is_described_three_different_ways() -> None:
    for persona in PERSONAS:
        for project in persona.projects:
            assert len({project.approach, project.readme, project.cv}) == 3
            assert project.codename.lower() not in project.title.lower()


def test_some_case_studies_mix_code_and_prose(data: Path) -> None:
    for persona in PERSONAS:
        folder = data / "personas" / persona.persona_id / "case_studies"
        assert any("```" in path.read_text(encoding="utf-8") for path in folder.glob("*.md"))


def test_specific_proposals_win_more_than_generic_ones(data: Path) -> None:
    rows = [
        proposal
        for persona in PERSONAS
        for proposal in read_json(data / "personas" / persona.persona_id / "proposals.json")
    ]

    def success(items: list[dict[str, Any]]) -> float:
        return sum(item["outcome"] != "ignored" for item in items) / len(items)

    specific = [row for row in rows if row["evidence"]]
    generic = [row for row in rows if not row["evidence"]]
    assert success(specific) > success(generic) + 0.2


def test_market_files_load_under_the_strict_api_schema(data: Path) -> None:
    seed = load_seed(data / "marketplace")
    assert len(seed.jobs) == 750
    assert len(seed.history) == 1500
    assert len(seed.freelancers) == 3


def test_jobs_never_contain_gold_labels(data: Path) -> None:
    text = (data / "marketplace" / "jobs.json").read_text(encoding="utf-8")
    for label in ("is_scam", "fair_price_band", "relevant_evidence", "job_type", "scam_type"):
        assert label not in text


def test_dev_set_size_and_mix(data: Path) -> None:
    rows = dev_gold(data)
    assert Counter(row["persona"] for row in rows) == {p.persona_id: 200 for p in PERSONAS}
    counts = Counter(row["job_type"] for row in rows)
    for job_type, share in DEV_MIX.items():
        assert counts[job_type] == pytest.approx(600 * share, abs=6)


def test_gold_decision_follows_the_declared_rule(data: Path) -> None:
    jobs = jobs_by_id(data)
    floors = {persona.persona_id: persona.floor_usd for persona in PERSONAS}
    decisions = Counter()
    for row in dev_gold(data):
        budget = jobs[row["job_id"]]["budget"]["max"]
        expected = (
            not row["is_scam"]
            and row["coverage"] >= BID_COVERAGE_THRESHOLD
            and budget >= floors[row["persona"]]
        )
        assert (row["decision"] == "bid") == expected, row["job_id"]
        decisions[row["decision"]] += 1
    assert decisions["bid"] > 200 and decisions["skip"] > 200


def test_job_types_get_the_intended_labels(data: Path) -> None:
    expected = {
        "good_fit": "bid",
        "vague": "bid",
        "injection_fit": "bid",
        "partial_fit": "skip",
        "other_field": "skip",
        "low_budget": "skip",
        "scam": "skip",
        "injection_poor": "skip",
    }
    for row in dev_gold(data):
        assert row["decision"] == expected[row["job_type"]], row["job_id"]


def test_gold_evidence_exists_in_the_corpus(data: Path) -> None:
    known = {
        item["item_id"]
        for persona in PERSONAS
        for item in read_json(data / "personas" / persona.persona_id / "index.json")
    }
    for row in dev_gold(data):
        assert set(row["relevant_evidence"]) <= known


def test_bid_jobs_have_evidence_and_a_fair_price(data: Path) -> None:
    for row in dev_gold(data):
        if row["decision"] == "bid":
            band = row["fair_price_band"]
            assert row["relevant_evidence"]
            assert 0 < band["min"] < band["max"]


def test_injection_labels_match_the_text(data: Path) -> None:
    jobs = jobs_by_id(data)
    for row in dev_gold(data):
        text = jobs[row["job_id"]]["description"]
        assert row["has_injection"] == any(payload in text for payload in INJECTIONS)


def test_vague_posts_name_no_required_skills(data: Path) -> None:
    jobs = jobs_by_id(data)
    for row in dev_gold(data):
        if row["job_type"] == "vague":
            assert jobs[row["job_id"]]["required_skills"] == []


def test_scam_signals_are_noisy_not_trivial(data: Path) -> None:
    clients = {c["client_id"]: c for c in read_json(data / "marketplace" / "clients.json")}
    jobs = jobs_by_id(data)
    scams = [jobs[row["job_id"]] for row in dev_gold(data) if row["is_scam"]]
    established = [job for job in scams if clients[job["client_id"]]["completed_projects"] > 0]
    assert established and len(established) < len(scams)
    legitimate_new = [
        c
        for c in clients.values()
        if c["completed_projects"] == 0 and not c["hidden_preferences"]["scam_actor"]
    ]
    assert legitimate_new
    all_text = " ".join(job["description"] for job in jobs.values())
    assert "paid trial milestone" in all_text


def test_stream_has_duplicates_scams_and_injections_after_start(data: Path) -> None:
    rows = read_gold(data / "stream_gold.jsonl")
    jobs = jobs_by_id(data)
    assert len(rows) == 150
    assert sum(row["is_scam"] for row in rows) > 0
    assert sum(row["has_injection"] for row in rows) > 0
    positions = {row["job_id"]: index for index, row in enumerate(rows)}
    duplicates = [row for row in rows if row["duplicate_of"]]
    assert duplicates
    for row in duplicates:
        assert positions[row["duplicate_of"]] < positions[row["job_id"]]
        original, copy = jobs[row["duplicate_of"]], jobs[row["job_id"]]
        assert original["description"] != copy["description"]
        assert original["budget"] == copy["budget"]
    assert all(jobs[row["job_id"]]["posted_at"] >= "2026-11-01" for row in rows)


def test_simulator_serves_generated_data_and_price_stats(data: Path) -> None:
    settings = Settings(
        config=SimConfig(start_time="2026-11-01T00:00:00Z", seed_dir=data / "marketplace"),
        api_tokens={"t": "ai_ml_engineer"},
    )
    client = TestClient(create_app(settings))
    gold = read_gold(data / "dev_set" / "ai_ml_engineer.jsonl")[0]
    assert client.get(f"/v1/jobs/{gold['job_id']}", headers=TOKEN).status_code == 200

    stats = client.get(
        "/v1/market/price-stats",
        params={"category": "machine_learning"},
        headers=TOKEN,
    ).json()
    assert stats["sample_size"] > 100
    assert stats["p25"] < stats["p50"] < stats["p75"]

    narrowed = client.get(
        "/v1/market/price-stats",
        params={"category": "machine_learning", "skills": ["XGBoost"]},
        headers=TOKEN,
    ).json()
    assert 0 < narrowed["sample_size"] < stats["sample_size"]

    missing = client.get("/v1/market/price-stats", params={"category": "none"}, headers=TOKEN)
    assert missing.status_code == 404
