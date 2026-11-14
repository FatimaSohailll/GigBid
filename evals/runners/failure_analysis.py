"""Group failed dev jobs into the assignment's taxonomy: python -m evals.runners.failure_analysis

Reads the rows the decision and proposal runs already saved; it calls no model. Each failure
records which field put it in its category, so every count can be checked by hand. A job can
fail in several ways; its primary category is the first in TAXONOMY order, which runs from the
failure that stops a job outright to the one that only weakens a proposal.

Writes evals/results/failures_<source>.json. The fix tried for each category, and whether it
worked, is written in the report, next to these counts.
"""

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from evals.runners.rag_eval import RESULTS_DIR, write_result

TAXONOMY = (
    "tool_error",
    "injection",
    "wrong_skip",
    "mispriced",
    "compliance_miss",
    "unsupported_claim",
    "missed_evidence",
    "generic_opening",
)
WEAK_SCORE = 2  # a rubric part scored 1 or 2 out of 5


def decision_failures(row: dict[str, Any]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if row.get("error"):
        found.append(("tool_error", f"error: {str(row['error'])[:160]}"))
        return found
    if row.get("has_injection") and not row.get("flagged_injection"):
        found.append(("injection", "planted instruction was not flagged"))
    if row.get("predicted") is not None and row.get("gold_bid") != (row["predicted"] == "bid"):
        expected = "bid" if row.get("gold_bid") else "skip"
        scam = " on a scam" if row.get("is_scam") and row["predicted"] == "bid" else ""
        found.append(("wrong_skip", f"predicted {row['predicted']}{scam}, gold {expected}"))
    if row.get("price_in_band") is False:
        found.append(("mispriced", f"price {row.get('price')} outside the fair band"))
    return found


def proposal_failures(row: dict[str, Any]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if row.get("error"):
        found.append(("tool_error", f"error: {str(row['error'])[:160]}"))
        return found
    if row.get("compliance_issues_in_output"):
        found.append(
            ("compliance_miss", f"{row['compliance_issues_in_output']} issue(s) in the sent text")
        )
    if row.get("unsupported_after"):
        claims = "; ".join(row.get("unsupported_final_claims", [])[:2])
        found.append(("unsupported_claim", f"{row['unsupported_after']} unsupported: {claims}"))
    if row.get("judge_evidence") is not None and row["judge_evidence"] <= WEAK_SCORE:
        found.append(("missed_evidence", f"judge evidence score {row['judge_evidence']}/5"))
    if row.get("judge_specificity") is not None and row["judge_specificity"] <= WEAK_SCORE:
        found.append(("generic_opening", f"judge specificity score {row['judge_specificity']}/5"))
    return found


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def analyse(rows_by_file: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    for name, rows in rows_by_file.items():
        check = decision_failures if name.startswith("decision") else proposal_failures
        for row in rows:
            found = check(row)
            if not found:
                continue
            categories = sorted({category for category, _ in found}, key=TAXONOMY.index)
            failures.append(
                {
                    "source": name,
                    "job_id": row.get("job_id"),
                    "persona": row.get("persona"),
                    "job_type": row.get("job_type"),
                    "primary": categories[0],
                    "categories": categories,
                    "evidence": [detail for _, detail in found],
                }
            )
    checked = sum(len(rows) for rows in rows_by_file.values())
    return {
        "jobs_checked": checked,
        "failed_jobs": len(failures),
        "by_primary_category": dict(Counter(item["primary"] for item in failures).most_common()),
        "by_any_category": dict(
            Counter(category for item in failures for category in item["categories"]).most_common()
        ),
        "by_job_type": dict(Counter(item["job_type"] for item in failures).most_common()),
        "files": sorted(rows_by_file),
        "failures": failures,
    }


def main() -> None:
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--source", default="devsim")
    options.add_argument("--directory", type=Path, default=RESULTS_DIR)
    arguments = options.parse_args()
    files = sorted(arguments.directory.glob(f"decision_rows_{arguments.source}*.jsonl")) + sorted(
        arguments.directory.glob(f"outcomes_rows_*_{arguments.source}*.jsonl")
    )
    result = analyse({path.stem: _read(path) for path in files})
    write_result(f"failures_{arguments.source}.json", result)
    print(json.dumps({key: value for key, value in result.items() if key != "failures"}, indent=2))


if __name__ == "__main__":
    main()
