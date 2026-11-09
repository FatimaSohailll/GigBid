"""Configuration loading. Behaviour comes from YAML, secrets only from the environment."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RateLimitConfig(_Strict):
    requests_per_minute: int = Field(default=60, gt=0)


class FaultConfig(_Strict):
    seed: int = 1234
    error_rate: float = Field(default=0.0, ge=0, le=1)
    post_commit_error_rate: float = Field(default=0.0, ge=0, le=1)
    retry_after_seconds: int = Field(default=1, ge=0)


class LimitsConfig(_Strict):
    max_cover_letter_chars: int = Field(default=5000, gt=0)
    max_message_chars: int = Field(default=4000, gt=0)
    max_page_size: int = Field(default=100, gt=0)


class SimConfig(_Strict):
    start_time: AwareDatetime
    seed_dir: Path | None = None
    rate_limit: RateLimitConfig = RateLimitConfig()
    faults: FaultConfig = FaultConfig()
    limits: LimitsConfig = LimitsConfig()


@dataclass(frozen=True)
class Settings:
    config: SimConfig
    api_tokens: Mapping[str, str] = field(repr=False)  # token -> freelancer_id
    admin_token: str | None = field(default=None, repr=False)


def load_config(path: Path | None = None) -> SimConfig:
    """Load a YAML config file, or the packaged default when no path is given."""
    if path is not None:
        text = path.read_text(encoding="utf-8")
    else:
        text = resources.files("devsim").joinpath("config.default.yaml").read_text("utf-8")
    return SimConfig.model_validate(yaml.safe_load(text))


def parse_api_tokens(raw: str) -> dict[str, str]:
    """Parse ``token=freelancer_id,token2=freelancer_id2``."""
    tokens: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        token, separator, freelancer_id = pair.partition("=")
        if not separator or not token.strip() or not freelancer_id.strip():
            raise ValueError("DEVSIM_API_TOKENS entries must look like token=freelancer_id")
        tokens[token.strip()] = freelancer_id.strip()
    if not tokens:
        raise ValueError("DEVSIM_API_TOKENS must define at least one token=freelancer_id pair")
    return tokens


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build settings from the environment. Fails fast when tokens are not configured."""
    environ = os.environ if env is None else env
    raw_tokens = environ.get("DEVSIM_API_TOKENS", "")
    config_path = environ.get("DEVSIM_CONFIG")
    return Settings(
        config=load_config(Path(config_path) if config_path else None),
        api_tokens=parse_api_tokens(raw_tokens),
        admin_token=environ.get("DEVSIM_ADMIN_TOKEN") or None,
    )
