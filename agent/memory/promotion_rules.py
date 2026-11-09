"""The explicit rules that decide what enters long-term memory (R7).

Nothing is promoted from a single event. A style rule needs the same edit repeated across
separate drafts, and an outcome insight needs enough real marketplace results.
"""

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from agent.config import MemoryConfig

CUT_FIRST_SENTENCE = "cut_first_sentence"
WRITE_SHORTER = "write_shorter"
AVOID_WORD_PREFIX = "avoid_word:"
POSITIVE_OUTCOMES = {"replied", "interview", "hired"}

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"\b[a-z][a-z-]{4,}\b")
_SHORTER_RATIO = 0.8
_SHORTER_MIN_WORDS = 30
_CUT_MAX_WORDS_KEPT = 0.5


@dataclass(frozen=True)
class StyleSignals:
    observed: set[str]
    opportunities: set[str]


def extract_style_signals(before: str, after: str) -> StyleSignals:
    """Find what one edit shows. Capitalised words are skipped, since they are usually names."""
    observed: set[str] = set()
    opportunities = {CUT_FIRST_SENTENCE, WRITE_SHORTER}
    kept = {word.lower() for word in re.findall(r"[A-Za-z-]+", after)}
    first_words = {word.lower() for word in re.findall(r"[A-Za-z-]+", _SENTENCE.split(before)[0])}
    # Rewording an opening is not cutting it: most of its words must be gone.
    if first_words and len(first_words & kept) / len(first_words) < _CUT_MAX_WORDS_KEPT:
        observed.add(CUT_FIRST_SENTENCE)
    words_before = len(before.split())
    if words_before >= _SHORTER_MIN_WORDS and len(after.split()) <= words_before * _SHORTER_RATIO:
        observed.add(WRITE_SHORTER)
    for word in set(_WORD.findall(before)):
        pattern = f"{AVOID_WORD_PREFIX}{word}"
        opportunities.add(pattern)
        if word not in kept:
            observed.add(pattern)
    return StyleSignals(observed, opportunities)


def should_promote_style(seen: int, opportunities: int, config: MemoryConfig) -> bool:
    """Promote only a repeated habit: enough separate edits, and consistent when it could apply."""
    return (
        seen >= config.style_min_edits
        and opportunities > 0
        and seen / opportunities >= config.style_min_consistency
    )


def style_rule_text(pattern: str) -> str:
    if pattern == CUT_FIRST_SENTENCE:
        return "Start with the substance. Do not open with an introductory sentence."
    if pattern == WRITE_SHORTER:
        return "Keep drafts noticeably shorter."
    return f"Never use the word '{pattern.removeprefix(AVOID_WORD_PREFIX)}'."


def promote_outcomes(
    records: Iterable[dict[str, Any]],
    config: MemoryConfig,
) -> dict[str, dict[str, Any]]:
    """Find evidence that repeatedly earned replies, per job category.

    Only final marketplace statuses count, and only submitted proposals are in the sample.
    """
    tally: defaultdict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for record in records:
        for project in record["projects"]:
            counts = tally[(record["category"], project)]
            counts[0] += 1
            counts[1] += record["status"] in POSITIVE_OUTCOMES
    insights: dict[str, dict[str, Any]] = {}
    for (category, project), (samples, positive) in sorted(tally.items()):
        if samples >= config.outcome_min_samples and (
            positive / samples >= config.outcome_min_reply_rate
        ):
            insights[f"{category}|{project}"] = {
                "category": category,
                "project": project,
                "samples": samples,
                "positive": positive,
                "insight": (
                    f"Evidence from '{project}' got a reply in {positive} of {samples} past "
                    f"{category} proposals."
                ),
            }
    return insights
