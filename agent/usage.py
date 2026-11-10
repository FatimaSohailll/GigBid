"""What each node actually spent: model, tokens, cost and time.

Graph nodes, baselines and evaluations all read the same record, so reported numbers agree.
A model without a configured price reports its cost as unknown rather than zero.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from agent.config import ModelSpec
from agent.state import NodeUsage

_calls: ContextVar[list["ModelCall"] | None] = ContextVar("gigbid_model_calls", default=None)
_MILLION = 1_000_000


@dataclass(frozen=True)
class ModelCall:
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None


def call_cost(spec: ModelSpec, input_tokens: int, output_tokens: int) -> float | None:
    if spec.input_usd_per_million is None or spec.output_usd_per_million is None:
        return None
    return (
        input_tokens * spec.input_usd_per_million + output_tokens * spec.output_usd_per_million
    ) / _MILLION


def record_model_call(spec: ModelSpec, usage: dict[str, Any]) -> None:
    """Called for every model reply, so failed attempts and retries are counted too."""
    calls = _calls.get()
    if calls is None:
        return
    input_tokens = int(usage.get("input_tokens", 0))
    output_tokens = int(usage.get("output_tokens", 0))
    calls.append(
        ModelCall(
            spec.model, input_tokens, output_tokens, call_cost(spec, input_tokens, output_tokens)
        )
    )


@contextmanager
def collect_model_calls() -> Iterator[list[ModelCall]]:
    calls: list[ModelCall] = []
    token = _calls.set(calls)
    try:
        yield calls
    finally:
        _calls.reset(token)


def node_usage(node: str, calls: Sequence[ModelCall], latency_seconds: float) -> NodeUsage:
    costs = [call.cost_usd for call in calls]
    return NodeUsage(
        node=node,
        models=sorted({call.model for call in calls}),
        calls=len(calls),
        input_tokens=sum(call.input_tokens for call in calls),
        output_tokens=sum(call.output_tokens for call in calls),
        cost_usd=None if any(cost is None for cost in costs) else sum(c or 0.0 for c in costs),
        latency_seconds=round(latency_seconds, 3),
    )
