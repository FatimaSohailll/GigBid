"""The runners against real DevSim and MCP servers. Only the model replies are scripted."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from evals.harness import EvalStack, eval_config, start_eval_stack
from evals.runners.decision_eval import score_job
from evals.runners.gigsim_outcomes import run_job
from rag.ingest.pipeline import ingest_documents
from tests.support.graph_stack import EVIDENCE, fast_config
from tests.support.rag_fakes import document, memory_components
from tests.support.scripted_llm import ScriptedLlm

PERSONA = "ai_ml_engineer"
GOLD: dict[str, Any] = {
    "job_id": "job-001",
    "persona": PERSONA,
    "job_type": "good_fit",
    "decision": "bid",
    "is_scam": False,
    "has_injection": False,
    "fair_price_band": {"min": 1, "max": 1_000_000, "currency": "USD"},
}


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[EvalStack]:
    components = memory_components(tmp_path)
    ingest_documents(
        [document("case:ai:churn", EVIDENCE, project="churn", skills=["Python"])],
        PERSONA,
        components,
    )
    running = start_eval_stack(
        tmp_path,
        components,
        eval_config(fast_config()),
        None,
        personas=(PERSONA, "fullstack_developer"),
    )
    yield running
    running.stop()


async def test_decision_run_stops_before_the_writer(stack: EvalStack) -> None:
    llm = ScriptedLlm()
    row = await score_job(stack.graph(PERSONA, llm), GOLD)
    assert row["error"] is None
    assert row["predicted"] == "bid"
    assert row["price_in_band"] is True
    assert llm.calls["writer"] == 0


async def test_outcome_run_submits_and_records_the_client_reaction(stack: EvalStack) -> None:
    row = await run_job(stack.graph(PERSONA, ScriptedLlm()), GOLD, stack, 42, None)
    assert row["status"] == "submitted"
    assert row["cover_letter"]
    assert row["unsupported_after"] == 0
    assert isinstance(row["replied"], bool)
