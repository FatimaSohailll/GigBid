"""Measure retrieval variants two ways and write evals/results/rag_*.json.

Whole job: one query from the full post, as the assignment defines V0. Per requirement: one query
per requirement, which is how the Fit Matcher really searches. The deployed variant is chosen on
per-requirement results.

Usage: python -m evals.runners.rag_eval --all
       python -m evals.runners.rag_eval --variant v2_hybrid --limit 9 --tag check --fresh
"""

import argparse
import json
import time
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from statistics import mean
from typing import Any

from evals.harness import load_gold, select_jobs
from evals.metrics import ndcg_at_k, percentile, recall_at_k, reciprocal_rank
from rag.index.reranker import CachingReranker
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
    "combo:decompose+hybrid",
    "combo:hybrid+rerank",
    "combo:decompose+hybrid+outcome",
    f"combo:{ALL_STAGES}",
)
RETRIEVED = 10
_MANY_REQUIREMENTS = 4
Row = dict[str, Any]


def _score(ranked: list[str], relevant: list[str], persona: str, gold: Row) -> Row:
    return {
        "recall_at_5": recall_at_k(ranked, relevant, 5),
        "mrr": reciprocal_rank(ranked, relevant),
        "ndcg_at_10": ndcg_at_k(ranked, relevant, RETRIEVED),
        "persona": persona,
        "job_type": gold["job_type"],
        "requirements": (
            "4_or_more" if len(gold["requirements"]) >= _MANY_REQUIREMENTS else "under_4"
        ),
    }


def _summarise(rows: list[Row]) -> dict[str, float | int]:
    summary: dict[str, float | int] = {
        "count": len(rows),
        "recall_at_5": round(mean(row["recall_at_5"] for row in rows), 4),
        "mrr": round(mean(row["mrr"] for row in rows), 4),
        "ndcg_at_10": round(mean(row["ndcg_at_10"] for row in rows), 4),
    }
    if "hit_at_fit_k" in rows[0]:
        summary["hit_at_fit_k"] = round(mean(row["hit_at_fit_k"] for row in rows), 4)
    return summary


def _grouped(rows: list[Row], field: str) -> dict[str, dict[str, float | int]]:
    groups: defaultdict[str, list[Row]] = defaultdict(list)
    for row in rows:
        groups[row[field]].append(row)
    return {key: _summarise(value) for key, value in sorted(groups.items())}


def _report(rows: list[Row], latencies: list[float]) -> dict[str, Any]:
    if not rows:
        raise ValueError("No gold rows to score; check the dev set")
    return {
        "overall": _summarise(rows),
        "by_persona": _grouped(rows, "persona"),
        "by_job_type": _grouped(rows, "job_type"),
        "by_requirement_count": _grouped(rows, "requirements"),
        "latency_ms": {
            "p50": round(percentile(latencies, 50), 1),
            "p95": round(percentile(latencies, 95), 1),
        },
    }


def timed(retriever: Retriever, query: str, tenant: str, latencies: list[float]) -> list[Any]:
    started = time.perf_counter()
    items = retriever.retrieve(query, tenant, RETRIEVED)
    latencies.append((time.perf_counter() - started) * 1000)
    return items


def evaluate_whole_job(retriever: Retriever, gold_rows: list[Row], jobs: dict[str, Row]) -> Row:
    rows: list[Row] = []
    latencies: list[float] = []
    refused: defaultdict[str, list[int]] = defaultdict(list)
    for gold in gold_rows:
        job = jobs[gold["job_id"]]
        query = f"{job['title']}\n{job['description']}"
        items = timed(retriever, query, gold["persona"], latencies)
        refused[gold["job_type"]].append(1 if not items else 0)
        if gold["relevant_evidence"]:
            ranked = [item.evidence_id for item in items]
            rows.append(_score(ranked, gold["relevant_evidence"], gold["persona"], gold))
    return {
        **_report(rows, latencies),
        "refusal_rate_by_job_type": {
            key: round(mean(value), 4) for key, value in sorted(refused.items())
        },
    }


def evaluate_per_requirement(retriever: Retriever, gold_rows: list[Row], fit_k: int) -> Row:
    """Search once per requirement. Skills repeat across jobs, so each is retrieved only once."""
    ranked_by_query: dict[tuple[str, str], list[str]] = {}
    latencies: list[float] = []
    rows: list[Row] = []
    no_evidence: list[int] = []
    for gold in gold_rows:
        persona = gold["persona"]
        for requirement in gold["requirements"]:
            key = (persona, requirement["skill"])
            if key not in ranked_by_query:
                items = timed(retriever, requirement["skill"], persona, latencies)
                ranked_by_query[key] = [item.evidence_id for item in items]
            ranked = ranked_by_query[key]
            if requirement["evidence"]:
                row = _score(ranked, requirement["evidence"], persona, gold)
                row["hit_at_fit_k"] = int(bool(set(ranked[:fit_k]) & set(requirement["evidence"])))
                rows.append(row)
            else:
                no_evidence.append(0 if ranked else 1)
    return {
        **_report(rows, latencies),
        "fit_k": fit_k,
        "distinct_queries": len(ranked_by_query),
        "no_evidence_requirements": len(no_evidence),
        "no_evidence_refusal_rate": round(mean(no_evidence), 4) if no_evidence else None,
    }


def check_gold(gold_rows: list[Row], jobs: dict[str, Row], components: RagComponents) -> Row:
    """Refuse to score against labels that do not match the indexed corpus or the job set."""
    known = {
        persona: set(components.corpus.hashes(persona))
        for persona in {g["persona"] for g in gold_rows}
    }
    missing_jobs = [gold["job_id"] for gold in gold_rows if gold["job_id"] not in jobs]
    missing_items = sorted(
        {
            item
            for gold in gold_rows
            for item in [
                *gold["relevant_evidence"],
                *(i for r in gold["requirements"] for i in r["evidence"]),
            ]
            if item not in known[gold["persona"]]
        }
    )
    if missing_jobs or missing_items:
        raise ValueError(
            f"Gold labels do not match the data: {len(missing_jobs)} unknown jobs, "
            f"{len(missing_items)} unknown evidence items, for example {missing_items[:3]}"
        )
    return {
        "jobs": len(gold_rows),
        "requirements": sum(len(gold["requirements"]) for gold in gold_rows),
        "labelled_requirements": sum(
            1 for gold in gold_rows for r in gold["requirements"] if r["evidence"]
        ),
    }


def safe_name(variant: str) -> str:
    return variant.replace("combo:", "combo-").replace("+", "-")


def write_result(name: str, value: Any) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / name
    path.write_bytes((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())
    return path


def measure(
    components: RagComponents,
    variant: str,
    data: Path,
    source: str,
    limit: int | None = None,
    tag: str = "",
    fresh: bool = True,
) -> Row:
    suffix = f"_{tag}" if tag else ""
    name = f"rag_{safe_name(variant)}_{source}{suffix}.json"
    if not fresh and (RESULTS_DIR / name).exists():
        existing: Row = json.loads((RESULTS_DIR / name).read_text(encoding="utf-8"))
        if "per_requirement" in existing:
            print(variant, "already measured", flush=True)
            return existing
    gold_rows = select_jobs(load_gold(data), limit)
    jobs = {
        job["job_id"]: job
        for job in json.loads((data / "marketplace" / "jobs.json").read_text(encoding="utf-8"))
    }
    gold_check = check_gold(gold_rows, jobs, components)
    retriever = build_retriever(components, variant)
    output: Row = {
        "variant": variant,
        "data_source": source,
        "official_gigsim": source == "gigsim",
        "embedding_model": components.embedder.name,
        "reranker_model": getattr(components.reranker, "name", None),
        "gold_check": gold_check,
        "whole_job": evaluate_whole_job(retriever, gold_rows, jobs),
        "per_requirement": evaluate_per_requirement(
            retriever, gold_rows, components.config.fit_evidence_per_requirement
        ),
    }
    write_result(name, output)
    print(variant, "whole", json.dumps(output["whole_job"]["overall"]), flush=True)
    print(variant, "per-req", json.dumps(output["per_requirement"]["overall"]), flush=True)
    return output


def select_variant(results: list[Row]) -> str:
    """Highest per-requirement Recall@5, then nDCG@10, then MRR."""

    def rank(row: Row) -> tuple[float, float, float]:
        overall = row["per_requirement"]["overall"]
        return overall["recall_at_5"], overall["ndcg_at_10"], overall["mrr"]

    return str(max(results, key=rank)["variant"])


def open_for_evaluation(data_dir: Path = Path("data/processed/rag")) -> RagComponents:
    """Reranker scores are saved, so repeat comparisons do not redo minutes of CPU work."""
    components = open_components()
    if components.reranker is None:
        return components
    cached = CachingReranker(components.reranker, data_dir / "reranker_cache.sqlite3")
    return replace(components, reranker=cached)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", default=None)
    parser.add_argument("--all", action="store_true", help="Compare every variant")
    parser.add_argument("--limit", type=int, default=None, help="Use only this many balanced jobs")
    parser.add_argument("--tag", default="", help="Added to result file names")
    parser.add_argument("--fresh", action="store_true", help="Measure again even if saved")
    parser.add_argument("--data", type=Path, default=Path("data/processed/devsim"))
    parser.add_argument("--source", default="devsim", help="Label recorded with the results")
    arguments = parser.parse_args()

    components = open_for_evaluation()
    options = (arguments.data, arguments.source, arguments.limit, arguments.tag, arguments.fresh)
    if arguments.all:
        results = [measure(components, variant, *options) for variant in COMPARED_VARIANTS]
        suffix = f"_{arguments.tag}" if arguments.tag else ""
        write_result(
            f"rag_comparison_{arguments.source}{suffix}.json",
            {
                "data_source": arguments.source,
                "official_gigsim": arguments.source == "gigsim",
                "selected_variant": select_variant(results),
                "selection_basis": "per-requirement Recall@5, then nDCG@10, then MRR",
                "rows": [
                    {
                        "variant": row["variant"],
                        "whole_job": row["whole_job"]["overall"],
                        "per_requirement": row["per_requirement"]["overall"],
                        "per_requirement_p95_latency_ms": row["per_requirement"]["latency_ms"][
                            "p95"
                        ],
                        "no_evidence_refusal_rate": row["per_requirement"][
                            "no_evidence_refusal_rate"
                        ],
                        "refusal_rate": row["whole_job"]["refusal_rate_by_job_type"],
                    }
                    for row in results
                ],
            },
        )
        print("selected:", select_variant(results), flush=True)
    else:
        measure(components, arguments.variant or components.config.variant, *options)
    components.close()


if __name__ == "__main__":
    main()
