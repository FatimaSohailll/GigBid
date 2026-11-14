"""The two open configurations compared on the dev set must differ only where intended."""

import pytest

from agent.config import load_config

OPEN_PROVIDERS = {"groq", "ollama"}


def models(profile: str) -> dict[str, str]:
    config = load_config(env={"MODEL_PROFILE": profile})
    nodes = config.models.profiles[profile].nodes
    return {node: config.model_for(node)[0].model for node in nodes}


@pytest.mark.parametrize("profile", ["open", "open_small"])
def test_open_profiles_use_open_weight_models_only(profile: str) -> None:
    config = load_config(env={"MODEL_PROFILE": profile})
    for spec in config.models.profiles[profile].tiers.values():
        assert spec.provider in OPEN_PROVIDERS


def test_the_comparison_changes_only_the_writing_nodes_and_keeps_one_judge() -> None:
    large, small = models("open"), models("open_small")
    assert large["judge"] == small["judge"]
    changed = {node for node in large if large[node] != small[node]}
    assert changed == {"writer", "rewrite", "critic", "client_liaison", "single_agent"}
    assert all(small[node] == "openai/gpt-oss-20b" for node in changed)
