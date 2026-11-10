"""Remove one stage at a time from the full pipeline: python -m evals.runners.ablation

Hypothesis, stated before running: combining decomposition and hybrid retrieval raises Recall@5
by at least 15 points over V0, on jobs with four or more requirements as well as overall.
"""

import argparse
from pathlib import Path

from evals.runners.rag_eval import measure, write_result
from rag.retriever import open_components

STAGES = ("decompose", "hybrid", "rerank", "outcome")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/processed/devsim"))
    parser.add_argument("--source", default="devsim")
    arguments = parser.parse_args()

    components = open_components()
    full = measure(components, f"combo:{'+'.join(STAGES)}", arguments.data, arguments.source)
    rows = [{"removed": None, **full["overall"], "by_requirements": full["by_requirement_count"]}]
    for removed in STAGES:
        kept = "+".join(stage for stage in STAGES if stage != removed)
        result = measure(components, f"combo:{kept}", arguments.data, arguments.source)
        rows.append(
            {
                "removed": removed,
                **result["overall"],
                "by_requirements": result["by_requirement_count"],
            }
        )
    components.close()
    write_result(
        f"ablation_{arguments.source}.json",
        {
            "data_source": arguments.source,
            "official_gigsim": arguments.source == "gigsim",
            "rows": rows,
        },
    )


if __name__ == "__main__":
    main()
