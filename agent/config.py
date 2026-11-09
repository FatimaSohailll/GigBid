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
    structured_method: str | None = None


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


class RagConfig(_Strict):
    variant: str
    collection: str
    chunk_size_words: int = Field(gt=0)
    chunk_overlap_words: int = Field(ge=0)
    candidate_multiplier: int = Field(ge=1)
    embedding_query_prefix: str = ""
    embedding_document_prefix: str = ""

    @model_validator(mode="after")
    def _overlap_smaller_than_chunk(self) -> Self:
        if self.chunk_overlap_words >= self.chunk_size_words:
            raise ValueError("chunk_overlap_words must be smaller than chunk_size_words")
        return self


class VerificationConfig(_Strict):
    supported_coverage: float = Field(gt=0, le=1)
    partly_supported_coverage: float = Field(gt=0, le=1)
    judge: Literal["llm", "lexical"] = "llm"
    max_evidence_per_claim: int = Field(default=3, ge=1)


class LlmConfig(_Strict):
    max_rate_limit_waits: int = Field(ge=0)
    max_rate_limit_wait_seconds: float = Field(ge=0)


class GraphConfig(_Strict):
    node_timeout_seconds: float = Field(gt=0)
    node_max_attempts: int = Field(ge=1)
    node_backoff_seconds: float = Field(ge=0)
    fit_evidence_per_requirement: int = Field(ge=1)
    duplicate_similarity: float = Field(gt=0, le=1)


class WritingConfig(_Strict):
    min_cover_letter_words: int = Field(gt=0)
    max_cover_letter_words: int = Field(gt=0)


class PricingConfig(_Strict):
    connect_value_usd: float = Field(ge=0)
    max_connects_share_of_bid: float = Field(gt=0, le=1)


class ComplianceConfig(_Strict):
    policy_path: Path
    rule_ids: dict[Literal["contact", "payment", "guarantee", "credentials", "fabrication"], str]


class AppConfig(_Strict):
    marketplace: MarketplaceConfig
    mcp_client: McpClientConfig
    agent_tools: dict[str, list[str]]
    models: ModelsConfig
    rag: RagConfig
    verification: VerificationConfig
    llm: LlmConfig
    graph: GraphConfig
    writing: WritingConfig
    pricing: PricingConfig
    compliance: ComplianceConfig
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
