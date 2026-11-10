"""LangSmith tracing for graph runs, with personal data masked before anything leaves.

Tracing attaches per run through callbacks, so a run without a key or with tracing disabled
behaves exactly the same and sends nothing.
"""

import os
from functools import lru_cache
from typing import Any

from langchain_core.tracers.langchain import LangChainTracer
from langsmith import Client
from langsmith.run_helpers import get_current_run_tree

from agent.config import AppConfig
from agent.guardrails import mask_pii
from agent.state import NodeUsage


@lru_cache(maxsize=4)
def _tracer(project: str) -> LangChainTracer:
    return LangChainTracer(project_name=project, client=Client(anonymizer=mask_pii))


def tracing_active(config: AppConfig) -> bool:
    return config.tracing.enabled and bool(os.environ.get("LANGSMITH_API_KEY"))


def run_config(
    config: AppConfig,
    thread_id: str,
    trace_id: str,
    user_id: str,
    run_name: str,
) -> dict[str, Any]:
    """The graph config for one run, tagged so a GigBid trace id finds its LangSmith trace."""
    graph_config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    if tracing_active(config):
        graph_config.update(
            callbacks=[_tracer(config.tracing.project)],
            run_name=run_name,
            metadata={"trace_id": trace_id, "thread_id": thread_id, "gigbid_user": user_id},
        )
    return graph_config


def annotate_node(usage: NodeUsage) -> None:
    """Show this node's tokens and cost on its own span in the trace."""
    run = get_current_run_tree()
    if run is not None:
        run.add_metadata(
            {
                "models": usage.models,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cost_usd": usage.cost_usd,
            }
        )


def flush_traces(config: AppConfig) -> None:
    """Send what is still queued, for short-lived processes such as evaluation runs."""
    if tracing_active(config):
        tracer = _tracer(config.tracing.project)
        tracer.wait_for_futures()
        tracer.client.flush()
