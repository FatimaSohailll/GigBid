"""Measure one retrieval variant on the dev set and write evals/results/rag_<variant>_<source>.json.

Usage: python -m evals.runners.rag_eval --variant v0_naive
"""

import argparse
import json
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from statistics import mean
from typing import Any

from evals.metrics import ndcg_at_k, recall_at_k, reciprocal_rank
from rag.retriever import build_retriever, open_components
from rag.types import Retriever

RESULTS_DIR = Path("evals/results")


def _gold_rows(dev_dir: Path) -> Iterable[dict[str, Any]]:
    for path in sorted(dev_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            yield json.loads(line)


def _summarise(rows: list[dict[str, float]]) -> dict[str, float | int]:
    return {
        "jobs": len(rows),
        "recall_at_5": round(mean(row["recall_at_5"] for row in rows), 4),
        "mrr": round(mean(row["mrr"] for row in rows), 4),
        "ndcg_at_10": round(mean(row["ndcg_at_10"] for row in rows), 4),
    }


def evaluate(retriever: Retriever, dev_dir: Path, jobs_path: Path) -> dict[str, Any]:
    jobs = {job["job_id"]: job for job in json.loads(jobs_path.read_text(encoding="utf-8"))}
    scored: list[dict[str, float]] = []
    by_persona: defaultdict[str, list[dict[str, float]]] = defaultdict(list)
    by_job_type: defaultdict[str, list[dict[str, float]]] = defaultdict(list)
    for gold in _gold_rows(dev_dir):
        relevant = gold["relevant_evidence"]
        if not relevant:
            continue
        job = jobs[gold["job_id"]]
        items = retriever.retrieve(f"{job['title']}\n{job['description']}", gold["persona"], 10)
        ranked = [item.evidence_id for item in items]
        row = {
            "recall_at_5": recall_at_k(ranked, relevant, 5),
            "mrr": reciprocal_rank(ranked, relevant),
            "ndcg_at_10": ndcg_at_k(ranked, relevant, 10),
        }
        scored.append(row)
        by_persona[gold["persona"]].append(row)
        by_job_type[gold["job_type"]].append(row)
    return {
        "overall": _summarise(scored),
        "by_persona": {key: _summarise(value) for key, value in sorted(by_persona.items())},
        "by_job_type": {key: _summarise(value) for key, value in sorted(by_job_type.items())},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", default=None)
    parser.add_argument("--data", type=Path, default=Path("data/processed/devsim"))
    parser.add_argument("--source", default="devsim", help="Label recorded with the results")
    arguments = parser.parse_args()

    components = open_components()
    variant = arguments.variant or components.config.variant
    metrics = evaluate(
        build_retriever(components, variant),
        arguments.data / "dev_set",
        arguments.data / "marketplace" / "jobs.json",
    )
    components.close()
    output = {
        "variant": variant,
        "data_source": arguments.source,
        "official_gigsim": arguments.source == "gigsim",
        "embedding_model": components.embedder.name,
        **metrics,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"rag_{variant}_{arguments.source}.json"
    path.write_bytes((json.dumps(output, indent=2, sort_keys=True) + "\n").encode())
    print(json.dumps(output["overall"]), f"-> {path}")


if __name__ == "__main__":
    main()
