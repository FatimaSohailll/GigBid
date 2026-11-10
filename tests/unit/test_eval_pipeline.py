import json
from pathlib import Path
from typing import Any

import pytest

from evals.harness import read_finished_rows, select_jobs
from evals.metrics import cohen_kappa, precision_recall_f1
from evals.results_table import END, MISSING, START, build_block, replace_block
from evals.runners import decision_eval, gigsim_outcomes
from evals.runners.judge_calibration import read_labels
from evals.runners.smoke import failures
from scripts.ci.guards import check_numbers

LIMITS = {
    "min_bid_or_skip_f1": 0.5,
    "max_unsupported_claims_after_verification": 1.0,
    "max_failed_jobs": 1,
}


def test_precision_recall_f1() -> None:
    pairs = [(True, True), (True, False), (False, True), (False, False)]
    assert precision_recall_f1(pairs) == {"precision": 0.5, "recall": 0.5, "f1": 0.5}


def test_undefined_scores_are_zero() -> None:
    assert precision_recall_f1([(False, False)])["f1"] == 0


def test_kappa_is_one_for_perfect_agreement_and_zero_at_chance() -> None:
    assert cohen_kappa([1, 2, 3], [1, 2, 3]) == 1
    assert cohen_kappa([1, 1, 2, 2], [1, 2, 1, 2]) == 0


def test_kappa_rejects_mismatched_ratings() -> None:
    with pytest.raises(ValueError):
        cohen_kappa([1], [1, 2])


def gold(job_id: str, persona: str, job_type: str) -> dict[str, Any]:
    return {"job_id": job_id, "persona": persona, "job_type": job_type}


def test_a_small_sample_covers_every_group() -> None:
    rows = [gold(f"{p}{t}{n}", p, t) for p in "ab" for t in "xy" for n in range(5)]
    chosen = select_jobs(rows, 4)
    assert {(row["persona"], row["job_type"]) for row in chosen} == {
        ("a", "x"),
        ("a", "y"),
        ("b", "x"),
        ("b", "y"),
    }
    assert chosen == select_jobs(rows, 4)


def test_failed_rows_run_again_on_resume(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    rows = [
        {"persona": "a", "job_id": "1", "error": None},
        {"persona": "a", "job_id": "2", "error": "rate limited"},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    assert set(read_finished_rows(path)) == {"a:1"}


def decision_row(**values: Any) -> dict[str, Any]:
    return {
        "persona": "a",
        "job_type": "good_fit",
        "gold_bid": True,
        "predicted": "bid",
        "is_scam": False,
        "flagged_scam": False,
        "has_injection": False,
        "flagged_injection": False,
        "price_in_band": True,
        "tokens": 100,
        "latency_seconds": 1.0,
        "error": None,
        **values,
    }


def test_a_watch_decision_is_not_a_bid() -> None:
    summary = decision_eval.summarise([decision_row(predicted="watch")])
    assert summary["bid_or_skip"]["recall"] == 0


def test_failed_jobs_are_counted_and_not_scored() -> None:
    summary = decision_eval.summarise([decision_row(), decision_row(error="boom", predicted=None)])
    assert summary["jobs"] == 1
    assert summary["failed"] == 1


def test_all_failed_jobs_do_not_crash_the_report() -> None:
    assert decision_eval.summarise([decision_row(error="boom")]) == {"jobs": 0, "failed": 1}


def outcome_row(**values: Any) -> dict[str, Any]:
    return {
        "status": "submitted",
        "submitted": True,
        "replied": True,
        "hired": False,
        "unsupported_before": 2,
        "unsupported_after": 0,
        "judge_overall": 4,
        "tokens": 100,
        "latency_seconds": 2.0,
        "error": None,
        **values,
    }


def test_reply_rate_counts_submitted_proposals_and_all_jobs_separately() -> None:
    skipped = outcome_row(
        status="skipped",
        submitted=False,
        replied=False,
        unsupported_before=None,
        unsupported_after=None,
    )
    summary = gigsim_outcomes.summarise([outcome_row(), skipped])
    assert summary["reply_rate"] == 1
    assert summary["reply_rate_over_all_jobs"] == 0.5
    assert summary["stopped_before_submission"] == {"skipped": 1}
    assert summary["unsupported_claims_before"] == 2


def test_smoke_gate_fails_on_low_f1_and_more_unsupported_claims() -> None:
    problems = failures(
        {"bid_or_skip": {"f1": 0.2}, "failed": 0},
        {"unsupported_claims_after": 2.0, "failed": 0},
        LIMITS,
    )
    assert len(problems) == 2


def test_smoke_gate_fails_when_nothing_was_verified() -> None:
    problems = failures(
        {"bid_or_skip": {"f1": 0.9}, "failed": 0},
        {"unsupported_claims_after": None, "failed": 0},
        LIMITS,
    )
    assert problems == ["no proposal reached claim verification"]


def test_smoke_gate_passes_good_results() -> None:
    assert not failures(
        {"bid_or_skip": {"f1": 0.9}, "failed": 0},
        {"unsupported_claims_after": 0.5, "failed": 1},
        LIMITS,
    )


def test_calibration_refuses_blank_labels(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"
    path.write_text("item_id,human_overall\na,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="blank"):
        read_labels(path)


def test_table_labels_devsim_and_marks_missing_values(tmp_path: Path) -> None:
    (tmp_path / "rag_v0_naive_devsim.json").write_text(
        json.dumps({"overall": {"recall_at_5": 0.093, "ndcg_at_10": 0.12}}), encoding="utf-8"
    )
    block = build_block("v2_hybrid", "devsim", tmp_path)
    assert "not GigSim" in block
    assert "| V0 | 0.093 | 0.12 |" in block
    assert f"| Your best | {MISSING} |" in block


def test_table_replaces_only_the_marked_block() -> None:
    text = f"before\n{START}\nold\n{END}\nafter"
    assert replace_block(text, "new") == f"before\n{START}\nnew\n{END}\nafter"


def repository(tmp_path: Path, table: str) -> Path:
    (tmp_path / "evals" / "results").mkdir(parents=True)
    (tmp_path / "evals" / "results" / "a.json").write_text('{"recall": 0.093}', encoding="utf-8")
    (tmp_path / "README.md").write_text(f"{START}\n{table}\n{END}\n", encoding="utf-8")
    return tmp_path


def test_number_guard_accepts_traced_values_and_ignores_names(tmp_path: Path) -> None:
    assert check_numbers(repository(tmp_path, "| V0 | Recall@5 | p95 | 0.093 |")) == []


def test_number_guard_rejects_an_untraced_value(tmp_path: Path) -> None:
    violations = check_numbers(repository(tmp_path, "| V0 | 0.5 |"))
    assert ["0.5 is not" in violation.message for violation in violations] == [True]
