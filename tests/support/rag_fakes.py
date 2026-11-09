"""A deterministic embedder for fast tests of the pipeline's logic.

It never replaces the real model when measuring retrieval quality. evals/ always uses the
configured open embedding model.
"""

import hashlib
import math
import re
from collections.abc import Sequence
from pathlib import Path

from qdrant_client import QdrantClient

from agent.config import get_config
from rag.index.corpus import CorpusStore
from rag.index.vector import VectorIndex
from rag.ingest.loaders import PortfolioDocument
from rag.types import RagComponents

DIMENSION = 256


class HashingEmbedder:
    name = "test-hashing"
    dimension = DIMENSION

    def __init__(self) -> None:
        self.documents_embedded = 0

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * DIMENSION
        for word in re.findall(r"\w+", text.lower()):
            digest = hashlib.sha256(word.encode()).digest()
            vector[int.from_bytes(digest[:4], "big") % DIMENSION] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.documents_embedded += len(texts)
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def memory_components(path: Path) -> RagComponents:
    embedder = HashingEmbedder()
    config = get_config().rag
    return RagComponents(
        corpus=CorpusStore(path / "corpus.sqlite3"),
        index=VectorIndex(QdrantClient(":memory:"), config.collection, embedder.dimension),
        embedder=embedder,
        config=config,
    )


def document(item_id: str, text: str, **fields: object) -> PortfolioDocument:
    values: dict[str, object] = {
        "item_id": item_id,
        "type": "case_study",
        "title": item_id,
        "text": text,
        "source": f"{item_id}.md",
        **fields,
    }
    return PortfolioDocument.model_validate(values)
