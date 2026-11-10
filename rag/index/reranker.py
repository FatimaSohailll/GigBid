"""Open cross-encoder reranking, run locally on CPU. The model loads on first use."""

import hashlib
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from fastembed.rerank.cross_encoder import TextCrossEncoder


class Reranker(Protocol):
    def score(self, query: str, documents: Sequence[str]) -> list[float]: ...


class FastEmbedReranker:
    def __init__(self, model_name: str) -> None:
        self.name = model_name
        self._model: TextCrossEncoder | None = None

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        if self._model is None:
            self._model = TextCrossEncoder(self.name)
        return [float(value) for value in self._model.rerank(query, list(documents))]


class CachingReranker:
    """Saves scores by model, query and text. Used for evaluation, where queries repeat."""

    def __init__(self, inner: Reranker, path: Path | str) -> None:
        self._inner = inner
        self.name = str(getattr(inner, "name", "unnamed"))
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.execute("create table if not exists scores (key text primary key, value real)")

    def _key(self, query: str, document: str) -> str:
        return hashlib.sha256(f"{self.name}\0{query}\0{document}".encode()).hexdigest()

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        keys = [self._key(query, document) for document in documents]
        found: dict[str, float] = {}
        for key in keys:
            row = self._db.execute("select value from scores where key=?", (key,)).fetchone()
            if row is not None:
                found[key] = row[0]
        missing = [(key, doc) for key, doc in zip(keys, documents, strict=True) if key not in found]
        if missing:
            scores = self._inner.score(query, [document for _, document in missing])
            for (key, _), value in zip(missing, scores, strict=True):
                found[key] = value
                self._db.execute("insert or replace into scores values (?, ?)", (key, value))
        return [found[key] for key in keys]
