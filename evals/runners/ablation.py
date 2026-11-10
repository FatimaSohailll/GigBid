"""Remove one stage at a time from the full pipeline: python -m evals.runners.ablation

Hypothesis, stated before running: combining decomposition and hybrid retrieval raises whole-job
Recall@5 by at least 15 points over V0, on jobs with four or more requirements.
"""

import argparse
from pathlib import Path
from typing import Any

from evals.runners.rag_eval import measure, open_for_evaluation, write_result

STAGES = ("decompose", "hybrid", "rerank", "outcome")
HYPOTHESIS = (
    "Decomposition plus hybrid retrieval raises whole-job Recall@5 by at least 15 points over V0 "
    "on jobs with four or more requirements."
)


def _row(removed: str | None, result: dict[str, Any]) -> dict[str, Any]:
    shapes = ("overall", "by_persona", "by_job_type", "by_requirement_count")
    return {
        "removed": removed,
        "variant": result["variant"],
        "whole_job": {key: result["whole_job"][key] for key in shapes},
        "per_requirement": {key: result["per_requirement"][key] for key in shapes},
    }


def hypothesis_check(v0: dict[str, Any], combined: dict[str, Any]) -> dict[str, Any]:
    baseline = v0["whole_job"]["by_requirement_count"]["4_or_more"]["recall_at_5"]
    treated = combined["whole_job"]["by_requirement_count"]["4_or_more"]["recall_at_5"]
    gain = round((treated - baseline) * 100, 2)
    return {
        "statement": HYPOTHESIS,
        "v0_recall_at_5": baseline,
        "decompose_hybrid_recall_at_5": treated,
        "gain_points": gain,
        "supported": gain >= 15,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/processed/devsim"))
    parser.add_argument("--source", default="devsim")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--tag", default="")
    parser.add_argument("--fresh", action="store_true")
    arguments = parser.parse_args()

    components = open_for_evaluation()
    options = (arguments.data, arguments.source, arguments.limit, arguments.tag, arguments.fresh)
    full = measure(components, f"combo:{'+'.join(STAGES)}", *options)
    rows = [_row(None, full)]
    for removed in STAGES:
        kept = "+".join(stage for stage in STAGES if stage != removed)
        rows.append(_row(removed, measure(components, f"combo:{kept}", *options)))
    check = hypothesis_check(
        measure(components, "v0_naive", *options),
        measure(components, "combo:decompose+hybrid", *options),
    )
    components.close()
    suffix = f"_{arguments.tag}" if arguments.tag else ""
    write_result(
        f"ablation_{arguments.source}{suffix}.json",
        {
            "data_source": arguments.source,
            "official_gigsim": arguments.source == "gigsim",
            "hypothesis": check,
            "rows": rows,
        },
    )
    print(check, flush=True)


if __name__ == "__main__":
    main()
