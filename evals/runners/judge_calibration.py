"""Calibrate the rubric judge against a person's labels (assignment B4).

Step 1: python -m evals.runners.judge_calibration export
Step 2: fill the human_overall column in evals/datasets/judge_calibration.csv, 1 to 5.
Step 3: python -m evals.runners.judge_calibration score

The score step refuses to run on missing labels, so agreement is always measured against a person.
"""

import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from agent.config import get_config
from agent.llm import ModelRouter
from evals.judge import score_proposal
from evals.metrics import cohen_kappa
from evals.runners.rag_eval import RESULTS_DIR, write_result

ITEMS = 30
LABELS = Path("evals/datasets/judge_calibration.csv")
FIELDS = ["item_id", "job_title", "job_description", "cover_letter", "human_overall"]


def export(rows_glob: str, data: Path, labels: Path, force: bool) -> int:
    if labels.exists() and not force:
        print(f"{labels} already exists and may hold your labels. Use --force to replace it.")
        return 2
    jobs = {
        job["job_id"]: job
        for job in json.loads((data / "marketplace" / "jobs.json").read_text(encoding="utf-8"))
    }
    letters: dict[str, dict[str, str]] = {}
    for path in sorted(RESULTS_DIR.glob(rows_glob)):
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("submitted") and row.get("cover_letter"):
                letters[f"{path.stem}:{row['persona']}:{row['job_id']}"] = row
    if len(letters) < ITEMS:
        print(f"Found {len(letters)} proposals, and {ITEMS} are needed. Run gigsim_outcomes first.")
        return 2
    labels.parent.mkdir(parents=True, exist_ok=True)
    with labels.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, FIELDS)
        writer.writeheader()
        for item_id, row in sorted(letters.items())[:ITEMS]:
            job = jobs[row["job_id"]]
            writer.writerow(
                {
                    "item_id": item_id,
                    "job_title": job["title"],
                    "job_description": job["description"],
                    "cover_letter": row["cover_letter"],
                    "human_overall": "",
                }
            )
    print(f"Wrote {ITEMS} proposals to {labels}. Fill in human_overall, 1 to 5.")
    return 0


def read_labels(labels: Path) -> list[dict[str, Any]]:
    with labels.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    unlabelled = [row["item_id"] for row in rows if not row["human_overall"].strip()]
    if unlabelled or len(rows) < ITEMS:
        raise ValueError(
            f"{labels} needs {ITEMS} labelled items; "
            f"it has {len(rows) - len(unlabelled)} labelled and {len(unlabelled)} blank."
        )
    return rows


async def score(labels: Path, source: str) -> int:
    load_dotenv()
    try:
        rows = read_labels(labels)
    except (FileNotFoundError, ValueError) as error:
        print(error)
        return 2
    llm = ModelRouter(get_config())
    human: list[int] = []
    judge: list[int] = []
    for row in rows:
        job = {"title": row["job_title"], "description": row["job_description"]}
        result = await score_proposal(llm, job, row["cover_letter"])
        human.append(int(row["human_overall"]))
        judge.append(result.overall)
    within_one = sum(abs(a - b) <= 1 for a, b in zip(human, judge, strict=True)) / len(human)
    write_result(
        f"judge_calibration_{source}.json",
        {
            "data_source": source,
            "items": len(human),
            "cohen_kappa": cohen_kappa(human, judge),
            "agreement_within_one": round(within_one, 4),
            "human": human,
            "judge": judge,
        },
    )
    print("Cohen's kappa:", cohen_kappa(human, judge))
    return 0


def main() -> int:
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("step", choices=["export", "score"])
    options.add_argument("--rows", default="outcomes_rows_*_devsim.jsonl")
    options.add_argument("--data", type=Path, default=Path("data/processed/devsim"))
    options.add_argument("--labels", type=Path, default=LABELS)
    options.add_argument("--source", default="devsim")
    options.add_argument("--force", action="store_true")
    arguments = options.parse_args()
    if arguments.step == "export":
        return export(arguments.rows, arguments.data, arguments.labels, arguments.force)
    return asyncio.run(score(arguments.labels, arguments.source))


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(main())
