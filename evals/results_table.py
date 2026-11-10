"""Regenerate the README results table from evals/results/: python -m evals.results_table"""

import argparse
import json
import re
from pathlib import Path
from typing import Any

from agent.config import get_config
from evals.runners.rag_eval import RESULTS_DIR, safe_name

START, END = "<!-- results:start -->", "<!-- results:end -->"
MISSING = "not measured"
HEADER = (
    "| Variant | Evidence Recall@5, whole job | Evidence Recall@5, per requirement "
    "| nDCG@10, per requirement | Unsupported claims per proposal | Judge score "
    "| Simulated reply rate | p95 latency (s) | Cost per proposal (USD) |\n"
    "|---|---|---|---|---|---|---|---|---|"
)


def _load(results: Path, name: str) -> dict[str, Any]:
    path = results / name
    return dict(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else {}


def _cell(values: dict[str, Any], key: str) -> str:
    value = values.get(key)
    return MISSING if value is None else str(value)


def table_row(label: str, variant: str, source: str, results: Path) -> str:
    name = safe_name(variant)
    retrieval = _load(results, f"rag_{name}_{source}.json")
    whole_job = retrieval.get("whole_job", {}).get("overall", {})
    per_requirement = retrieval.get("per_requirement", {}).get("overall", {})
    outcomes = _load(results, f"outcomes_{name}_{source}.json").get("overall", {})
    cells = [
        label,
        _cell(whole_job, "recall_at_5"),
        _cell(per_requirement, "recall_at_5"),
        _cell(per_requirement, "ndcg_at_10"),
        _cell(outcomes, "unsupported_claims_after"),
        _cell(outcomes, "judge_score"),
        _cell(outcomes, "reply_rate"),
        _cell(outcomes, "p95_latency_seconds"),
        _cell(outcomes, "cost_per_proposal_usd"),
    ]
    return "| " + " | ".join(cells) + " |"


def build_block(best: str, source: str, results: Path) -> str:
    kappa = _load(results, f"judge_calibration_{source}.json").get("cohen_kappa")
    origin = "official GigSim" if source == "gigsim" else "DevSim stand-in data, not GigSim"
    lines = [
        f"Data source: {origin}. Deployed variant: `{best}`.",
        "",
        HEADER,
        table_row("V0", "v0_naive", source, results),
        table_row("Your best", best, source, results),
        "",
        f"Judge calibration, Cohen's kappa: {MISSING if kappa is None else kappa}.",
    ]
    return "\n".join(lines)


def replace_block(text: str, block: str) -> str:
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)
    if not pattern.search(text):
        raise ValueError("README.md has no results markers")
    return pattern.sub(lambda _: f"{START}\n{block}\n{END}", text)


def main() -> None:
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--source", default="devsim")
    options.add_argument("--readme", type=Path, default=Path("README.md"))
    arguments = options.parse_args()
    block = build_block(get_config().rag.variant, arguments.source, RESULTS_DIR)
    text = arguments.readme.read_bytes().decode("utf-8")
    arguments.readme.write_bytes(replace_block(text, block).encode("utf-8"))
    print(block)


if __name__ == "__main__":
    main()
