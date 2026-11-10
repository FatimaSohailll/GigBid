"""Blind pairwise panel: classmates choose between anonymised proposals (assignment B4).

Step 1: python -m evals.runners.human_panel export
Step 2: give each classmate evals/datasets/panel/pairs.csv and rating_template.csv only.
        Each saves their ratings as evals/datasets/panel/ratings/<their name>.csv.
Step 3: python -m evals.runners.human_panel score

The key linking A and B to a variant is kept separately. Do not share key.json with raters.
"""

import argparse
import csv
import json
import math
import random
import sys
from pathlib import Path
from typing import Any

from agent.guardrails import mask_pii
from evals.runners.rag_eval import RESULTS_DIR, safe_name, write_result

PANEL_DIR = Path("evals/datasets/panel")
_CHOICES = {"A", "B", "TIE"}


def _submitted(path: Path) -> dict[str, dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return {
        f"{row['persona']}:{row['job_id']}": row
        for row in rows
        if row.get("submitted") and row.get("cover_letter")
    }


def build_pairs(
    baseline: dict[str, dict[str, Any]],
    treated: dict[str, dict[str, Any]],
    jobs: dict[str, dict[str, Any]],
    seed: int,
) -> tuple[list[dict[str, str]], dict[str, str]]:
    """Pair proposals for the same job, with the side and the order chosen at random."""
    draw = random.Random(seed)
    common = sorted(set(baseline) & set(treated))
    draw.shuffle(common)
    pairs: list[dict[str, str]] = []
    key: dict[str, str] = {}
    for number, job_key in enumerate(common, start=1):
        pair_id = f"pair-{number:03d}"
        job = jobs[baseline[job_key]["job_id"]]
        first, second = baseline[job_key], treated[job_key]
        key[pair_id] = "A=treated" if draw.random() < 0.5 else "A=baseline"
        a, b = (second, first) if key[pair_id] == "A=treated" else (first, second)
        pairs.append(
            {
                "pair_id": pair_id,
                "job_title": mask_pii(job["title"]),
                "job_description": mask_pii(job["description"]),
                "proposal_a": mask_pii(a["cover_letter"]),
                "proposal_b": mask_pii(b["cover_letter"]),
            }
        )
    return pairs, key


def sign_test(wins: int, losses: int) -> float:
    """Exact two-sided probability of a split at least this uneven, if neither side is better."""
    total = wins + losses
    if total == 0:
        return 1.0
    tail = sum(math.comb(total, k) for k in range(max(wins, losses), total + 1)) / 2**total
    return float(round(min(1.0, 2 * tail), 4))


def read_ratings(folder: Path, known_pairs: set[str]) -> list[dict[str, str]]:
    ratings: list[dict[str, str]] = []
    for path in sorted(folder.glob("*.csv")):
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                choice = (row.get("preferred") or "").strip().upper()
                if not choice:
                    continue
                if row["pair_id"] not in known_pairs or choice not in _CHOICES:
                    raise ValueError(
                        f"{path.name}: bad rating {row['pair_id']!r} = {choice!r}; use A, B or TIE"
                    )
                ratings.append({"rater": path.stem, "pair_id": row["pair_id"], "choice": choice})
    return ratings


def tally(ratings: list[dict[str, str]], key: dict[str, str]) -> dict[str, Any]:
    treated = baseline = ties = 0
    for rating in ratings:
        if rating["choice"] == "TIE":
            ties += 1
        elif (rating["choice"] == "A") == (key[rating["pair_id"]] == "A=treated"):
            treated += 1
        else:
            baseline += 1
    decided = treated + baseline
    return {
        "raters": len({rating["rater"] for rating in ratings}),
        "ratings": len(ratings),
        "pairs_rated": len({rating["pair_id"] for rating in ratings}),
        "preferred_treated": treated,
        "preferred_baseline": baseline,
        "ties": ties,
        "treated_share_of_decided": round(treated / decided, 4) if decided else None,
        "sign_test_p": sign_test(treated, baseline),
    }


def export(baseline: str, treated: str, data: Path, source: str, seed: int, force: bool) -> int:
    key_path = PANEL_DIR / "key.json"
    if key_path.exists() and not force:
        print(f"{key_path} already exists and may match ratings. Use --force to replace it.")
        return 2
    rows = [
        _submitted(RESULTS_DIR / f"outcomes_rows_{safe_name(variant)}_{source}.jsonl")
        for variant in (baseline, treated)
    ]
    jobs = {
        job["job_id"]: job
        for job in json.loads((data / "marketplace" / "jobs.json").read_text(encoding="utf-8"))
    }
    pairs, key = build_pairs(rows[0], rows[1], jobs, seed)
    if not pairs:
        print("No job has a submitted proposal from both variants. Run gigsim_outcomes first.")
        return 2
    (PANEL_DIR / "ratings").mkdir(parents=True, exist_ok=True)
    with (PANEL_DIR / "pairs.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, list(pairs[0]))
        writer.writeheader()
        writer.writerows(pairs)
    with (PANEL_DIR / "rating_template.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, ["pair_id", "preferred"])
        writer.writeheader()
        writer.writerows({"pair_id": pair["pair_id"], "preferred": ""} for pair in pairs)
    key_path.write_bytes(
        (
            json.dumps({"baseline": baseline, "treated": treated, "sides": key}, indent=2) + "\n"
        ).encode()
    )
    print(f"Wrote {len(pairs)} pairs to {PANEL_DIR}. Share pairs.csv and rating_template.csv only.")
    return 0


def score(source: str) -> int:
    key_path = PANEL_DIR / "key.json"
    if not key_path.exists():
        print("Run the export step first.")
        return 2
    key = json.loads(key_path.read_text(encoding="utf-8"))
    try:
        ratings = read_ratings(PANEL_DIR / "ratings", set(key["sides"]))
    except ValueError as error:
        print(error)
        return 2
    if not ratings:
        print(f"No ratings found in {PANEL_DIR / 'ratings'}.")
        return 2
    result = {
        "data_source": source,
        "baseline": key["baseline"],
        "treated": key["treated"],
        "pairs": len(key["sides"]),
        **tally(ratings, key["sides"]),
    }
    write_result(f"human_panel_{source}.json", result)
    print(json.dumps(result, indent=2))
    return 0


def main() -> int:
    from agent.config import get_config

    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("step", choices=["export", "score"])
    options.add_argument("--baseline", default="v0_naive")
    options.add_argument("--treated", default=None)
    options.add_argument("--data", type=Path, default=Path("data/processed/devsim"))
    options.add_argument("--source", default="devsim")
    options.add_argument("--seed", type=int, default=2026)
    options.add_argument("--force", action="store_true")
    arguments = options.parse_args()
    if arguments.step == "score":
        return score(arguments.source)
    treated = arguments.treated or get_config().rag.variant
    return export(
        arguments.baseline,
        treated,
        arguments.data,
        arguments.source,
        arguments.seed,
        arguments.force,
    )


if __name__ == "__main__":
    sys.exit(main())
