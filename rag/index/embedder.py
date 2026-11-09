"""Open embedding models, run locally on CPU through ONNX."""

from collections.abc import Sequence
from typing import Protocol

from fastembed import TextEmbedding


class Embedder(Protocol):
    name: str
    dimension: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedEmbedder:
    def __init__(self, model_name: str, query_prefix: str = "", document_prefix: str = "") -> None:
        self.name = model_name
        self._model = TextEmbedding(model_name)
        self._query_prefix = query_prefix
        self._document_prefix = document_prefix
        self.dimension = self._model.embedding_size

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        prefixed = [f"{self._document_prefix}{text}" for text in texts]
        return [vector.tolist() for vector in self._model.embed(prefixed)]

    def embed_query(self, text: str) -> list[float]:
        vector = next(iter(self._model.embed([f"{self._query_prefix}{text}"])))
        return [float(value) for value in vector]
