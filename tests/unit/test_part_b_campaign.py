import csv
import json
from pathlib import Path
from typing import Any

import pytest

from evals.runners import human_panel
from evals.runners.decision_eval import models_used
from evals.runners.human_panel import build_pairs, read_ratings, sign_test, tally
from evals.runners.model_campaign import ROUNDS, paired_comparison


def row(job: str, letter: str, **values: Any) -> dict[str, Any]:
    return {
        "persona": "p",
        "job_id": job,
        "job_type": "good_fit",
        "status": "submitted",
        "submitted": True,
        "replied": False,
        "hired": False,
        "cover_letter": letter,
        "claims_before": 2,
        "unsupported_before": 1,
        "claims_after": 2,
        "unsupported_after": 0,
        "judge_overall": 4,
        "tokens": 10,
        "cost_usd": 0.01,
        "usage": [],
        "latency_seconds": 1.0,
        "error": None,
        **values,
    }


JOBS = {
    "j1": {"title": "T", "description": "Contact me at a@b.io"},
    "j2": {"title": "T", "description": "D"},
}


def pairs_for() -> tuple[list[dict[str, str]], dict[str, str]]:
    baseline = {
        "p:j1": row("j1", "base one"),
        "p:j2": row("j2", "base two"),
        "p:j3": row("j3", "x"),
    }
    treated = {"p:j1": row("j1", "best one"), "p:j2": row("j2", "best two")}
    return build_pairs(baseline, treated, JOBS, 7)


def test_pairs_cover_only_jobs_both_variants_finished() -> None:
    pairs, key = pairs_for()
    assert len(pairs) == 2
    assert set(key) == {"pair-001", "pair-002"}


def test_pairs_hide_the_variant_and_mask_contact_details() -> None:
    pairs, _ = pairs_for()
    assert not any("variant" in name or "persona" in name for name in pairs[0])
    assert all("a@b.io" not in pair["job_description"] for pair in pairs)


def test_sides_follow_the_key() -> None:
    pairs, key = pairs_for()
    for pair in pairs:
        shown = pair["proposal_a"]
        assert ("best" in shown) == (key[pair["pair_id"]] == "A=treated")


def test_pairs_are_reproducible_for_a_seed() -> None:
    assert pairs_for() == pairs_for()


def test_sign_test_values() -> None:
    assert sign_test(5, 5) == 1.0
    assert sign_test(10, 0) == 0.002
    assert sign_test(0, 0) == 1.0


def test_tally_maps_choices_through_the_key() -> None:
    key = {"p1": "A=treated", "p2": "A=baseline", "p3": "A=treated"}
    ratings = [
        {"rater": "x", "pair_id": "p1", "choice": "A"},
        {"rater": "x", "pair_id": "p2", "choice": "A"},
        {"rater": "y", "pair_id": "p3", "choice": "TIE"},
    ]
    result = tally(ratings, key)
    assert (result["preferred_treated"], result["preferred_baseline"], result["ties"]) == (1, 1, 1)
    assert result["raters"] == 2
    assert result["treated_share_of_decided"] == 0.5


def write_ratings(folder: Path, name: str, rows: list[tuple[str, str]]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{name}.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["pair_id", "preferred"])
        writer.writerows(rows)


def test_blank_ratings_are_skipped_and_bad_ones_refused(tmp_path: Path) -> None:
    write_ratings(tmp_path, "sara", [("p1", "a"), ("p2", "")])
    assert read_ratings(tmp_path, {"p1", "p2"}) == [
        {"rater": "sara", "pair_id": "p1", "choice": "A"}
    ]
    write_ratings(tmp_path, "omar", [("p1", "maybe")])
    with pytest.raises(ValueError, match="use A, B or TIE"):
        read_ratings(tmp_path, {"p1", "p2"})


def test_score_refuses_to_report_without_ratings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(human_panel, "PANEL_DIR", tmp_path)
    (tmp_path / "ratings").mkdir()
    (tmp_path / "key.json").write_text(
        json.dumps({"baseline": "a", "treated": "b", "sides": {"p1": "A=treated"}}),
        encoding="utf-8",
    )
    assert human_panel.score("devsim") == 2


def write_rows(directory: Path, variant: str, rows: list[dict[str, Any]]) -> None:
    text = "".join(json.dumps(item) + "\n" for item in rows)
    (directory / f"outcomes_rows_{variant}_devsim.jsonl").write_text(text, encoding="utf-8")


def test_comparison_uses_only_jobs_completed_by_both(tmp_path: Path) -> None:
    write_rows(
        tmp_path,
        "base",
        [row("j1", "a", replied=True), row("j2", "b"), row("j3", "c", error="limit")],
    )
    write_rows(tmp_path, "best", [row("j1", "a", replied=True), row("j2", "b", replied=True)])
    result = paired_comparison("base", "best", tmp_path)
    assert result["paired_jobs"] == 2
    assert result["reply_outcome_by_job"] == {
        "treated_only": 1,
        "baseline_only": 0,
        "both": 1,
        "neither": 0,
    }
    assert result["treated_results"]["overall"]["reply_rate"] == 1
    assert result["baseline_results"]["overall"]["reply_rate"] == 0.5


def test_comparison_with_nothing_in_common_reports_no_numbers(tmp_path: Path) -> None:
    write_rows(tmp_path, "base", [row("j1", "a")])
    write_rows(tmp_path, "best", [row("j2", "b")])
    result = paired_comparison("base", "best", tmp_path)
    assert result["paired_jobs"] == 0
    assert result["treated_results"] is None


def test_rounds_grow_so_variants_stay_level() -> None:
    proposals = [count for _, count in ROUNDS]
    decisions = [count for count, _ in ROUNDS]
    assert proposals == sorted(proposals) and len(set(proposals)) == len(proposals)
    assert decisions == sorted(decisions)


def test_models_used_reveals_a_fallback() -> None:
    rows = [{"usage": [{"models": ["openai/gpt-oss-20b"]}, {"models": ["qwen3:4b"]}]}]
    assert models_used(rows) == ["openai/gpt-oss-20b", "qwen3:4b"]
