"""Behaviour shared by every graph node: budgets, timeouts, retries and dead-lettering."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langgraph.graph import END

from agent.marketplace_port import current_trace_id
from agent.mcp_client import ToolCallError, ToolServerUnavailable
from agent.runtime import AgentRuntime
from agent.state import NodeError, ThreadStatus

logger = logging.getLogger(__name__)
NodeFunction = Callable[[Any, AgentRuntime], Awaitable[dict[str, Any]]]
_FAILED = {ThreadStatus.NEEDS_ATTENTION, ThreadStatus.DEAD_LETTER}


def failed(state: Mapping[str, Any]) -> bool:
    return state.get("status") in _FAILED


def end_if_failed(next_node: str) -> Callable[[Any], str]:
    def route(state: Mapping[str, Any]) -> str:
        return str(END) if failed(state) else next_node

    return route


def _budget_problem(state: Mapping[str, Any], runtime: AgentRuntime) -> str | None:
    budgets = runtime.config.budgets
    if state.get("steps_used", 0) >= budgets.max_graph_steps:
        return f"Step budget of {budgets.max_graph_steps} was reached."
    if state.get("tokens_used", 0) >= budgets.max_tokens_per_job:
        return f"Token budget of {budgets.max_tokens_per_job} was reached."
    return None


def guarded(name: str, function: NodeFunction, runtime: AgentRuntime) -> Callable[..., Any]:
    """Apply budgets, timeout and bounded retries, and dead-letter what cannot complete.

    An unavailable service is deliberately re-raised. The checkpoint stays at the last good
    step, so the worker can queue the thread and resume it when the service returns.
    """
    settings = runtime.config.graph

    async def node(state: Mapping[str, Any]) -> dict[str, Any]:
        if failed(state):
            return {}
        problem = _budget_problem(state, runtime)
        if problem:
            return {"status": ThreadStatus.NEEDS_ATTENTION, "attention_reason": problem}
        trace = current_trace_id.set(state.get("trace_id"))
        try:
            failure: Exception | None = None
            for attempt in range(settings.node_max_attempts):
                try:
                    update = await asyncio.wait_for(
                        function(state, runtime), settings.node_timeout_seconds
                    )
                    return {**update, "steps_used": 1}
                except ToolServerUnavailable:
                    raise
                except (ToolCallError, PermissionError) as error:
                    failure = error
                    break
                except Exception as error:  # a bad model reply or a transient failure
                    failure = error
                    logger.warning("Node %s attempt %d failed: %r", name, attempt + 1, error)
                    if attempt + 1 < settings.node_max_attempts:
                        await asyncio.sleep(settings.node_backoff_seconds * (2**attempt))
            return {
                "status": ThreadStatus.DEAD_LETTER,
                "attention_reason": f"{name} could not complete.",
                "errors": [NodeError(node=name, message=repr(failure))],
                "steps_used": 1,
            }
        finally:
            current_trace_id.reset(trace)

    return node
