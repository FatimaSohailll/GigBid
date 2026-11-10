"""Measure retrieval variants on the dev set and write evals/results/rag_*.json.

Usage: python -m evals.runners.rag_eval --all
       python -m evals.runners.rag_eval --variant v2_hybrid
"""

import argparse
import json
import time
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from statistics import mean
from typing import Any

from evals.metrics import ndcg_at_k, percentile, recall_at_k, reciprocal_rank
from rag.retriever import build_retriever, open_components
from rag.types import RagComponents, Retriever

RESULTS_DIR = Path("evals/results")
ALL_STAGES = "decompose+hybrid+rerank+outcome"
COMPARED_VARIANTS = (
    "v0_naive",
    "v1_decomposition",
    "v2_hybrid",
    "v3_rerank",
    "v4_outcome",
    "v8_corrective",
    f"combo:{ALL_STAGES}",
)


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
    by_requirements: defaultdict[str, list[dict[str, float]]] = defaultdict(list)
    refused: defaultdict[str, list[int]] = defaultdict(list)
    latencies: list[float] = []
    for gold in _gold_rows(dev_dir):
        job = jobs[gold["job_id"]]
        started = time.perf_counter()
        items = retriever.retrieve(f"{job['title']}\n{job['description']}", gold["persona"], 10)
        latencies.append((time.perf_counter() - started) * 1000)
        refused[gold["job_type"]].append(1 if not items else 0)
        relevant = gold["relevant_evidence"]
        if not relevant:
            continue
        ranked = [item.evidence_id for item in items]
        row = {
            "recall_at_5": recall_at_k(ranked, relevant, 5),
            "mrr": reciprocal_rank(ranked, relevant),
            "ndcg_at_10": ndcg_at_k(ranked, relevant, 10),
        }
        scored.append(row)
        by_persona[gold["persona"]].append(row)
        by_job_type[gold["job_type"]].append(row)
        by_requirements["4_or_more" if len(gold["requirements"]) >= 4 else "under_4"].append(row)
    return {
        "overall": _summarise(scored),
        "by_persona": {key: _summarise(value) for key, value in sorted(by_persona.items())},
        "by_job_type": {key: _summarise(value) for key, value in sorted(by_job_type.items())},
        "by_requirement_count": {
            key: _summarise(value) for key, value in sorted(by_requirements.items())
        },
        "refusal_rate_by_job_type": {
            key: round(mean(value), 4) for key, value in sorted(refused.items())
        },
        "latency_ms": {
            "p50": round(percentile(latencies, 50), 1),
            "p95": round(percentile(latencies, 95), 1),
        },
    }


def safe_name(variant: str) -> str:
    return variant.replace("combo:", "combo-").replace("+", "-")


def measure(
    components: RagComponents,
    variant: str,
    data: Path,
    source: str,
) -> dict[str, Any]:
    metrics = evaluate(
        build_retriever(components, variant),
        data / "dev_set",
        data / "marketplace" / "jobs.json",
    )
    output = {
        "variant": variant,
        "data_source": source,
        "official_gigsim": source == "gigsim",
        "embedding_model": components.embedder.name,
        **metrics,
    }
    write_result(f"rag_{safe_name(variant)}_{source}.json", output)
    print(variant, json.dumps(output["overall"]), json.dumps(output["latency_ms"]), flush=True)
    return output


def write_result(name: str, value: Any) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / name
    path.write_bytes((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", default=None)
    parser.add_argument("--all", action="store_true", help="Compare every variant")
    parser.add_argument("--data", type=Path, default=Path("data/processed/devsim"))
    parser.add_argument("--source", default="devsim", help="Label recorded with the results")
    arguments = parser.parse_args()

    components = open_components()
    if arguments.all:
        results = [
            measure(components, variant, arguments.data, arguments.source)
            for variant in COMPARED_VARIANTS
        ]
        write_result(
            f"rag_comparison_{arguments.source}.json",
            {
                "data_source": arguments.source,
                "official_gigsim": arguments.source == "gigsim",
                "rows": [
                    {
                        "variant": row["variant"],
                        **row["overall"],
                        "p95_latency_ms": row["latency_ms"]["p95"],
                        "refusal_rate": row["refusal_rate_by_job_type"],
                    }
                    for row in results
                ],
            },
        )
    else:
        measure(
            components,
            arguments.variant or components.config.variant,
            arguments.data,
            arguments.source,
        )
    components.close()


if __name__ == "__main__":
    main()
