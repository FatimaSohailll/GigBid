"""Measure signature extension X3: python -m evals.runners.case_builder_eval [--limit 5]

Each DevSim persona has projects with a README, code and a hand-written case study. For each
project, the builder drafts a case study from the README and code alone, then the result is
compared with what the persona actually wrote:

- claims verified and claims removed: how much of the draft its own repository supports;
- built: whether enough verified claims remained to save the case study;
- skill precision and recall against the hand-written case study's skills;
- tokens per case study.

Only the corpus is opened (not the vector index), so it can run beside other evaluations.
Writes evals/results/case_builder_<source>.json. Needs model quota: one strong-model call per
project plus one claim check per sentence.
"""

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from dotenv import load_dotenv

from agent.case_builder import build_case_study
from agent.config import get_config
from agent.llm import ModelRouter
from agent.runtime import AgentRuntime
from evals.harness import eval_config
from evals.runners.rag_eval import RESULTS_DIR, write_result
from rag.index.corpus import CorpusStore
from rag.ingest.loaders import PortfolioDocument

PERSONAS = ("ai_ml_engineer", "data_analyst", "fullstack_developer")


def _norm(skill: str) -> str:
    return skill.strip().lower().replace("-", " ")


def skill_overlap(built: list[str], gold: list[str]) -> tuple[float | None, float | None]:
    found, wanted = {_norm(s) for s in built}, {_norm(s) for s in gold}
    precision = len(found & wanted) / len(found) if found else None
    recall = len(found & wanted) / len(wanted) if wanted else None
    return precision, recall


def projects(documents: list[PortfolioDocument]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {"repo": [], "gold": None})
    for item in documents:
        if not item.project:
            continue
        if item.type in {"readme", "code"}:
            grouped[item.project]["repo"].append(item)
        elif item.type == "case_study":
            grouped[item.project]["gold"] = item
    return {name: group for name, group in grouped.items() if group["repo"] and group["gold"]}


async def run(limit: int, source: str) -> dict[str, Any]:
    load_dotenv()
    config = eval_config(get_config())
    runtime = AgentRuntime(config, ModelRouter(config), None, None)  # type: ignore[arg-type]
    corpus = CorpusStore(
        Path(os.environ.get("RAG_DATA_DIR", "data/processed/rag")) / "corpus.sqlite3"
    )
    rows: list[dict[str, Any]] = []
    for persona in PERSONAS:
        for name, group in sorted(projects(corpus.documents(persona)).items())[:limit]:
            result = await build_case_study(group["repo"], persona, name, runtime)
            built_skills = result.document.skills if result.document else []
            precision, recall = skill_overlap(built_skills, group["gold"].skills)
            rows.append(
                {
                    "persona": persona,
                    "project": name,
                    "built": result.document is not None,
                    "claims": result.claims,
                    "verified": result.verified,
                    "removed": result.removed,
                    "skill_precision": precision,
                    "skill_recall": recall,
                    "tokens": result.tokens,
                }
            )
            print(persona, name, "built" if result.document else "not built", flush=True)
    claims = sum(row["claims"] for row in rows)

    def average(field: str) -> float | None:
        values = [row[field] for row in rows if row[field] is not None]
        return round(mean(values), 4) if values else None

    return {
        "data_source": source,
        "projects": len(rows),
        "built_rate": round(mean(float(row["built"]) for row in rows), 4) if rows else None,
        "claims": claims,
        "verified_claim_rate": round(sum(r["verified"] for r in rows) / claims, 4)
        if claims
        else None,
        "removed_claims_per_case_study": average_removed(rows),
        "skill_precision": average("skill_precision"),
        "skill_recall": average("skill_recall"),
        "tokens_per_case_study": average("tokens"),
        "rows": rows,
    }


def average_removed(rows: list[dict[str, Any]]) -> float | None:
    return round(mean(len(row["removed"]) for row in rows), 4) if rows else None


def main() -> int:
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--limit", type=int, default=5, help="Projects per persona")
    options.add_argument("--source", default="devsim")
    arguments = options.parse_args()
    result = asyncio.run(run(arguments.limit, arguments.source))
    write_result(f"case_builder_{arguments.source}.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=2))
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(main())

# Imported for tests and other runners.
__all__ = ["RESULTS_DIR", "projects", "skill_overlap"]
