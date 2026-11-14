"""The change request: a new platform rule is absorbed by configuration, with no code change."""

from typing import Any

import pytest
from pydantic import ValidationError

from agent.config import AppConfig, ComplianceConfig, PolicyRule, get_config
from agent.guardrails import scan_outgoing_text
from agent.handoffs import ComplianceAction, ProposalDraft
from agent.nodes import compliance
from agent.runtime import AgentRuntime

# The rule rehearsed in docs/CHANGE_REQUEST.md: trading reviews for discounts or extra work.
REVIEW_TRADE = PolicyRule(
    rule_id="POL-13",
    explanation="Reviews must not be traded for discounts or extra work.",
    pattern=r"\b(?:discount|refund|free|extra)\b.{0,60}\b(?:5[- ]star|review|rating)s?\b",
)


def with_rule(name: str, rule: PolicyRule) -> AppConfig:
    base = get_config()
    rules = {**base.compliance.rules, name: rule}
    return base.model_copy(
        update={"compliance": base.compliance.model_copy(update={"rules": rules})}
    )


class Llm:
    """The model review finds nothing, so any block comes from the configured rule."""

    async def structured(self, node: str, instructions: str, data: str, schema: Any) -> Any:
        from agent.llm import LlmResult

        return LlmResult(schema(violations=[], block=False, reason=""), 0, "m")


async def test_a_rule_added_only_in_config_blocks_the_draft() -> None:
    runtime = AgentRuntime(with_rule("review_trade", REVIEW_TRADE), Llm(), None, None)  # type: ignore[arg-type]
    letter = (
        "I can deliver the dashboard in a week. I will give a 10% discount for a 5-star review."
    )
    state = {"drafts": [ProposalDraft(job_id="j", cover_letter=letter)]}
    update = await compliance.run(state, runtime)  # type: ignore[arg-type]
    verdict = update["compliance_verdicts"][0]
    assert verdict.action is ComplianceAction.BLOCK
    assert [item.rule_id for item in verdict.violations] == ["POL-13"]
    assert verdict.violations[0].explanation == REVIEW_TRADE.explanation


def test_without_the_rule_the_same_text_passes() -> None:
    text = "I will give a 10% discount for a 5-star review."
    assert not scan_outgoing_text(text, get_config().compliance.pattern_rules()).blocked


def test_a_redacting_rule_removes_the_text_and_does_not_block() -> None:
    rule = REVIEW_TRADE.model_copy(update={"action": "redact"})
    scan = scan_outgoing_text(
        "Happy to help. A free extra revision for a review!",
        with_rule("review_trade", rule).compliance.pattern_rules(),
    )
    assert not scan.blocked and "[removed]" in scan.text


@pytest.mark.parametrize(
    "rule",
    [
        {"rule_id": "POL-14", "explanation": "x"},  # a new rule needs a pattern
        {"rule_id": "POL-14", "explanation": "x", "pattern": "("},  # and it must compile
    ],
)
def test_a_broken_rule_is_refused_when_the_config_loads(rule: dict[str, Any]) -> None:
    rules = {**get_config().compliance.model_dump()["rules"], "new_rule": rule}
    with pytest.raises(ValidationError):
        ComplianceConfig(policy_path=get_config().compliance.policy_path, rules=rules)


def test_the_built_in_rules_cannot_be_dropped() -> None:
    rules = get_config().compliance.model_dump()["rules"]
    del rules["contact"]
    with pytest.raises(ValidationError):
        ComplianceConfig(policy_path=get_config().compliance.policy_path, rules=rules)
