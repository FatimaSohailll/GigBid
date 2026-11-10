"""Open cross-encoder reranking, run locally on CPU. The model loads on first use."""

from collections.abc import Sequence
from typing import Protocol

from fastembed.rerank.cross_encoder import TextCrossEncoder


class Reranker(Protocol):
    def score(self, query: str, documents: Sequence[str]) -> list[float]: ...


class FastEmbedReranker:
    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model: TextCrossEncoder | None = None

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        if self._model is None:
            self._model = TextCrossEncoder(self._model_name)
        return [float(value) for value in self._model.rerank(query, list(documents))]
