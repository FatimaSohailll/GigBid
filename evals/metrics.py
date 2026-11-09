"""Retrieval metrics at item level."""

import math
from collections.abc import Collection, Sequence


def recall_at_k(ranked: Sequence[str], relevant: Collection[str], k: int) -> float:
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & set(relevant)) / len(set(relevant))


def reciprocal_rank(ranked: Sequence[str], relevant: Collection[str]) -> float:
    for position, item in enumerate(ranked, start=1):
        if item in relevant:
            return 1 / position
    return 0.0


def ndcg_at_k(ranked: Sequence[str], relevant: Collection[str], k: int) -> float:
    gain = sum(
        1 / math.log2(position + 1)
        for position, item in enumerate(ranked[:k], start=1)
        if item in relevant
    )
    ideal = sum(1 / math.log2(position + 1) for position in range(1, min(k, len(relevant)) + 1))
    return gain / ideal if ideal else 0.0
