from typing import Any

from langgraph.store.memory import InMemoryStore

from agent.config import load_config
from agent.memory.promotion_rules import (
    CUT_FIRST_SENTENCE,
    extract_style_signals,
    promote_outcomes,
    should_promote_style,
)
from agent.memory.store import LongTermMemory

CONFIG = load_config(env={}).memory
PASSIONATE = {"before": "We are passionate engineers.", "after": "We are engineers."}


def memory() -> LongTermMemory:
    return LongTermMemory(InMemoryStore(), CONFIG)


def test_signals_find_cut_first_sentence_and_removed_words() -> None:
    signals = extract_style_signals(
        "I am very excited. We build great pipelines.", "We build pipelines."
    )
    assert CUT_FIRST_SENTENCE in signals.observed
    assert "avoid_word:great" in signals.observed
    assert "avoid_word:build" not in signals.observed


def test_a_small_edit_is_not_learned_as_cutting_or_shortening() -> None:
    signals = extract_style_signals(PASSIONATE["before"], PASSIONATE["after"])
    assert signals.observed == {"avoid_word:passionate"}


def test_capitalised_names_are_never_learned_as_words_to_avoid() -> None:
    signals = extract_style_signals("We used Kubernetes.", "We used it.")
    assert "avoid_word:kubernetes" not in signals.observed


def test_promotion_needs_repetition_and_consistency() -> None:
    assert should_promote_style(3, 4, CONFIG)
    assert not should_promote_style(2, 2, CONFIG)
    assert not should_promote_style(3, 10, CONFIG)


async def test_one_or_two_edits_do_not_create_a_rule() -> None:
    store = memory()
    await store.record_edits("u", "job-1", [PASSIONATE])
    await store.record_edits("u", "job-2", [PASSIONATE])
    assert await store.style_rules("u") == []


async def test_third_separate_draft_promotes_the_rule() -> None:
    store = memory()
    for number in range(3):
        await store.record_edits("u", f"job-{number}", [PASSIONATE])
    assert "Never use the word 'passionate'." in await store.style_rules("u")


async def test_replaying_the_same_draft_does_not_count_twice() -> None:
    store = memory()
    for _ in range(5):
        await store.record_edits("u", "job-1", [PASSIONATE])
    assert await store.style_rules("u") == []


async def test_memory_is_scoped_by_user() -> None:
    store = memory()
    for number in range(3):
        await store.record_edits("alice", f"job-{number}", [PASSIONATE])
    assert await store.style_rules("bob") == []


async def test_deleted_rule_must_be_earned_again() -> None:
    store = memory()
    for number in range(3):
        await store.record_edits("u", f"job-{number}", [PASSIONATE])
    await store.delete_style_rule("u", "avoid_word:passionate")
    assert "Never use the word 'passionate'." not in await store.style_rules("u")
    await store.record_edits("u", "job-9", [PASSIONATE])
    assert "Never use the word 'passionate'." not in await store.style_rules("u")


async def test_rule_is_withdrawn_when_the_habit_stops() -> None:
    store = memory()
    for number in range(3):
        await store.record_edits("u", f"job-{number}", [PASSIONATE])
    keeps = {"before": "We are passionate engineers.", "after": "We are passionate builders."}
    for number in range(3, 6):
        await store.record_edits("u", f"job-{number}", [keeps])
    assert "Never use the word 'passionate'." not in await store.style_rules("u")


def records(positive: int, negative: int) -> list[dict[str, Any]]:
    good = {"category": "ml", "projects": ["churn"], "status": "replied"}
    bad = {"category": "ml", "projects": ["churn"], "status": "lost"}
    return [good] * positive + [bad] * negative


def test_outcome_needs_enough_samples_and_replies() -> None:
    assert promote_outcomes(records(3, 1), CONFIG) == {}
    assert promote_outcomes(records(1, 5), CONFIG) == {}
    insight = promote_outcomes(records(3, 2), CONFIG)["ml|churn"]
    assert insight["samples"] == 5 and insight["positive"] == 3


async def test_outcomes_flow_from_submission_to_insight() -> None:
    store = memory()
    for number in range(5):
        await store.record_submission("u", f"p{number}", "ml", ["case:u:churn"])
    for number in range(4):
        await store.record_outcome("u", f"p{number}", "replied")
    assert len(await store.outcome_insights("u", "ml")) == 1
    assert await store.outcome_insights("u", "web") == []
    await store.delete_outcome_insight("u", "ml|churn")
    assert await store.outcome_insights("u", "ml") == []


async def test_outcome_for_an_unknown_proposal_is_ignored() -> None:
    store = memory()
    await store.record_outcome("u", "never-submitted", "hired")
    assert await store.outcome_items("u") == []
