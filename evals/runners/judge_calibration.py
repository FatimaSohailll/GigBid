"""Calibrate the rubric judge against a person's labels (assignment B4).

Step 1: python -m evals.runners.judge_calibration export
Step 2: fill the human_overall column in evals/datasets/judge_calibration.csv, 1 to 5,
        using the rubric in docs/JUDGE_LABELLING.md. Re-run export as proposals finish.
Step 3: python -m evals.runners.judge_calibration score

The score step refuses to run on missing labels, so agreement is always measured against a person.
"""

import argparse
import asyncio
import csv
import hashlib
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

ITEMS = 30  # what the assignment asks for; fewer are allowed, and the result says how many
LABELS = Path("evals/datasets/judge_calibration.csv")
KEY = Path("evals/datasets/judge_calibration_key.json")  # which proposal each blind id is
FIELDS = ["item_id", "job_title", "job_description", "cover_letter", "human_overall"]


def _blind_id(key: str) -> str:
    """An id that does not reveal the variant, so the labels stay blind."""
    return "item-" + hashlib.sha256(key.encode()).hexdigest()[:8]


def _proposals(rows_glob: str) -> dict[str, dict[str, Any]]:
    letters: dict[str, dict[str, Any]] = {}
    for path in sorted(RESULTS_DIR.glob(rows_glob)):
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("submitted") and row.get("cover_letter") and not row.get("error"):
                letters[f"{path.stem}:{row['persona']}:{row['job_id']}"] = row
    return letters


def export(rows_glob: str, data: Path, labels: Path, key_path: Path, force: bool) -> int:
    """Write every finished proposal to label, up to ITEMS, keeping labels already given.

    Run it again when more proposals finish: new ones are added, nothing is lost. Items are in a
    shuffled, stable order with blind ids, so you cannot tell which variant wrote which.
    """
    jobs = {
        job["job_id"]: job
        for job in json.loads((data / "marketplace" / "jobs.json").read_text(encoding="utf-8"))
    }
    kept: dict[str, dict[str, str]] = {}
    if labels.exists() and not force:
        with labels.open(encoding="utf-8", newline="") as handle:
            kept = {row["item_id"]: row for row in csv.DictReader(handle)}
    key: dict[str, str] = json.loads(key_path.read_text("utf-8")) if key_path.exists() else {}
    if force:
        key = {}
    for name in _proposals(rows_glob):
        key.setdefault(_blind_id(name), name)
    chosen = sorted(key)[:ITEMS]  # sorted by hash: a stable shuffle
    letters = _proposals(rows_glob)
    labels.parent.mkdir(parents=True, exist_ok=True)
    with labels.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, FIELDS)
        writer.writeheader()
        for item_id in chosen:
            row = letters.get(key[item_id])
            if row is None:
                continue
            job = jobs[row["job_id"]]
            writer.writerow(
                {
                    "item_id": item_id,
                    "job_title": job["title"],
                    "job_description": job["description"],
                    "cover_letter": row["cover_letter"],
                    "human_overall": kept.get(item_id, {}).get("human_overall", ""),
                }
            )
    key_path.write_text(json.dumps(key, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    labelled = sum(1 for item in chosen if kept.get(item, {}).get("human_overall", "").strip())
    print(
        f"{labels}: {len(chosen)} proposals ({labelled} already labelled). "
        f"The assignment asks for {ITEMS}; run export again as more proposals finish."
    )
    return 0


def read_labels(labels: Path) -> list[dict[str, Any]]:
    with labels.open(encoding="utf-8", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["human_overall"].strip()]
    for row in rows:
        if row["human_overall"].strip() not in {"1", "2", "3", "4", "5"}:
            raise ValueError(f"{row['item_id']}: human_overall must be a whole number from 1 to 5")
    if len(rows) < 2:
        raise ValueError(f"{labels} needs at least 2 labelled items to compare.")
    return rows


async def score(labels: Path, key_path: Path, rows_glob: str, source: str) -> int:
    """Agreement between your labels and the judge score each proposal got in the run."""
    load_dotenv()
    try:
        rows = read_labels(labels)
    except (FileNotFoundError, ValueError) as error:
        print(error)
        return 2
    key: dict[str, str] = json.loads(key_path.read_text("utf-8"))
    saved = _proposals(rows_glob)
    llm: ModelRouter | None = None
    human: list[int] = []
    judge: list[int] = []
    for row in rows:
        proposal = saved.get(key.get(row["item_id"], ""), {})
        overall = proposal.get("judge_overall")
        if overall is None:  # only when the run did not judge this proposal
            llm = llm or ModelRouter(get_config())
            job = {"title": row["job_title"], "description": row["job_description"]}
            overall = (await score_proposal(llm, job, row["cover_letter"])).overall
        human.append(int(row["human_overall"]))
        judge.append(int(overall))
    within_one = sum(abs(a - b) <= 1 for a, b in zip(human, judge, strict=True)) / len(human)
    write_result(
        f"judge_calibration_{source}.json",
        {
            "data_source": source,
            "items": len(human),
            "items_required": ITEMS,
            "cohen_kappa": cohen_kappa(human, judge),
            "agreement_within_one": round(within_one, 4),
            "human": human,
            "judge": judge,
        },
    )
    print(f"Cohen's kappa on {len(human)} items:", cohen_kappa(human, judge))
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
        return export(arguments.rows, arguments.data, arguments.labels, KEY, arguments.force)
    return asyncio.run(score(arguments.labels, KEY, arguments.rows, arguments.source))


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(main())
