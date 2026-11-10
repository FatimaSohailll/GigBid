"""Route each node to its configured model, with a fallback for cheap nodes."""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from agent.config import AppConfig, ModelSpec
from agent.usage import record_model_call

logger = logging.getLogger(__name__)
ModelFactory = Callable[[ModelSpec], BaseChatModel]
_DEFAULT_RATE_LIMIT_DELAY = 5.0


@dataclass(frozen=True)
class LlmResult[T: BaseModel]:
    value: T
    tokens: int
    model: str


class LlmGateway(Protocol):
    async def structured[T: BaseModel](
        self,
        node: str,
        instructions: str,
        data: str,
        schema: type[T],
    ) -> LlmResult[T]: ...


def rate_limit_delay(error: Exception) -> float | None:
    """Return the seconds to wait if this is a provider rate-limit error, else None."""
    if getattr(error, "status_code", None) != 429:
        return None
    match = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", str(error))
    if match:
        return int(match.group(1) or 0) * 60 + float(match.group(2)) + 0.5
    return _DEFAULT_RATE_LIMIT_DELAY


def create_chat_model(spec: ModelSpec) -> BaseChatModel:
    return init_chat_model(spec.model, model_provider=spec.provider, temperature=0)


def structured_runnable(model: BaseChatModel, spec: ModelSpec, schema: type[BaseModel]) -> Any:
    options: dict[str, Any] = {"method": spec.structured_method} if spec.structured_method else {}
    return model.with_structured_output(schema, include_raw=True, **options)


class ModelRouter:
    def __init__(
        self,
        config: AppConfig,
        factory: ModelFactory = create_chat_model,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._config = config
        self._factory = factory
        self._sleep = sleep

    async def _invoke(self, runnable: Any, messages: list[Any]) -> Any:
        """Wait out provider rate limits, for as long as the provider says, within bounds."""
        limits = self._config.llm
        for attempt in range(limits.max_rate_limit_waits + 1):
            try:
                return await runnable.ainvoke(messages)
            except Exception as error:
                delay = rate_limit_delay(error)
                if delay is None or attempt == limits.max_rate_limit_waits:
                    raise
                await self._sleep(min(delay, limits.max_rate_limit_wait_seconds))
        raise AssertionError("unreachable")

    async def _call[T: BaseModel](
        self,
        spec: ModelSpec,
        instructions: str,
        data: str,
        schema: type[T],
    ) -> LlmResult[T]:
        runnable = structured_runnable(self._factory(spec), spec, schema)
        output = await self._invoke(
            runnable, [SystemMessage(content=instructions), HumanMessage(content=data)]
        )
        usage = getattr(output["raw"], "usage_metadata", None) or {}
        record_model_call(spec, usage)  # before validation: a rejected reply still cost tokens
        parsed = output["parsed"]
        if not isinstance(parsed, schema):
            raise ValueError(f"Model output did not match {schema.__name__}")
        return LlmResult(parsed, int(usage.get("total_tokens", 0)), spec.model)

    async def structured[T: BaseModel](
        self,
        node: str,
        instructions: str,
        data: str,
        schema: type[T],
    ) -> LlmResult[T]:
        primary, fallback = self._config.model_for(node)
        try:
            return await self._call(primary, instructions, data, schema)
        except Exception as error:
            if fallback is None:
                raise
            logger.warning("Node %s falling back after %s", node, type(error).__name__)
            return await self._call(fallback, instructions, data, schema)
