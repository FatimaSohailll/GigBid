"""Typed access to agent/config.yaml. Models, thresholds and budgets live there, not in code."""

import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_PATH = Path(__file__).with_name("config.yaml")
REQUIRED_NODES = frozenset(
    {
        "scout",
        "analyst",
        "risk_assessor",
        "fit_matcher",
        "pricing",
        "writer",
        "verifier",
        "critic",
        "compliance",
        "intent",
        "client_liaison",
    }
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MarketplaceConfig(_Strict):
    mode: Literal["gigsim", "upwork_live"] = "gigsim"
    max_requests_per_minute: int = Field(gt=0)
    request_timeout_seconds: float = Field(gt=0)
    max_retries: int = Field(ge=0)
    backoff_base_seconds: float = Field(ge=0)
    max_retry_wait_seconds: float = Field(ge=0)
    max_rate_limit_wait_seconds: float = Field(ge=0)


class McpClientConfig(_Strict):
    tool_timeout_seconds: float = Field(gt=0)
    max_retries: int = Field(ge=0)
    backoff_base_seconds: float = Field(ge=0)


class ModelSpec(_Strict):
    provider: str
    model: str


class NodeRoute(_Strict):
    tier: str
    fallback: str | None = None


class ModelProfile(_Strict):
    tiers: dict[str, ModelSpec]
    nodes: dict[str, NodeRoute]

    @model_validator(mode="after")
    def _complete(self) -> Self:
        missing = REQUIRED_NODES - set(self.nodes)
        if missing:
            raise ValueError(f"profile has no model for nodes: {sorted(missing)}")
        for node, route in self.nodes.items():
            for tier in filter(None, (route.tier, route.fallback)):
                if tier not in self.tiers:
                    raise ValueError(f"node '{node}' uses unknown tier '{tier}'")
        return self


class ModelsConfig(_Strict):
    active_profile: str
    embeddings: str
    reranker: str
    profiles: dict[str, ModelProfile]

    @model_validator(mode="after")
    def _known_profile(self) -> Self:
        if "open" not in self.profiles:
            raise ValueError("the open profile is required")
        if self.active_profile not in self.profiles:
            raise ValueError(f"unknown model profile '{self.active_profile}'")
        return self


class Budgets(_Strict):
    max_graph_steps: int = Field(gt=0)
    max_revisions: int = Field(ge=0, le=2)
    max_tokens_per_job: int = Field(gt=0)


class Thresholds(_Strict):
    minimum_fit_score: float = Field(ge=0, le=1)
    watch_fit_score: float = Field(ge=0, le=1)
    maximum_risk_score: float = Field(ge=0, le=1)


class AppConfig(_Strict):
    marketplace: MarketplaceConfig
    mcp_client: McpClientConfig
    agent_tools: dict[str, list[str]]
    models: ModelsConfig
    prompt_versions: dict[str, int] = Field(default_factory=dict)
    budgets: Budgets
    thresholds: Thresholds

    def model_for(self, node: str) -> tuple[ModelSpec, ModelSpec | None]:
        """Return the (primary, fallback) models for a node in the active profile."""
        profile = self.models.profiles[self.models.active_profile]
        route = profile.nodes[node]
        fallback = profile.tiers[route.fallback] if route.fallback else None
        return profile.tiers[route.tier], fallback

    def tools_for(self, agent: str) -> list[str]:
        return self.agent_tools[agent]


def load_config(path: Path = CONFIG_PATH, env: Mapping[str, str] | None = None) -> AppConfig:
    environ = os.environ if env is None else env
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if environ.get("MODEL_PROFILE"):
        raw["models"]["active_profile"] = environ["MODEL_PROFILE"]
    if environ.get("MARKETPLACE_MODE"):
        raw["marketplace"]["mode"] = environ["MARKETPLACE_MODE"]
    return AppConfig.model_validate(raw)


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    return load_config()
