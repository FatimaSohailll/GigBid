"""Evaluation metrics. Retrieval metrics work at item level."""

import math
import statistics
from collections import Counter
from collections.abc import Collection, Iterable, Sequence


def precision_recall_f1(pairs: Iterable[tuple[bool, bool]]) -> dict[str, float]:
    """Score (gold, predicted) pairs. An undefined value is reported as 0."""
    counts = Counter(pairs)
    true_positive = counts[(True, True)]
    predicted = true_positive + counts[(False, True)]
    actual = true_positive + counts[(True, False)]
    precision = true_positive / predicted if predicted else 0.0
    recall = true_positive / actual if actual else 0.0
    total = precision + recall
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(2 * precision * recall / total if total else 0.0, 4),
    }


def cohen_kappa(first: Sequence[int], second: Sequence[int]) -> float:
    """Agreement between two raters beyond what chance would produce."""
    if len(first) != len(second) or not first:
        raise ValueError("Cohen's kappa needs two non-empty ratings of equal length")
    size = len(first)
    observed = sum(a == b for a, b in zip(first, second, strict=True)) / size
    first_counts, second_counts = Counter(first), Counter(second)
    expected = sum(first_counts[label] * second_counts[label] for label in first_counts) / size**2
    return 1.0 if expected == 1 else round((observed - expected) / (1 - expected), 4)


def percentile(values: Sequence[float], point: int) -> float:
    return statistics.quantiles(values, n=100)[point - 1] if len(values) > 1 else values[0]


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
