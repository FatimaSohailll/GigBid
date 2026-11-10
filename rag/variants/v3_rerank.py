"""V3: dense retrieval followed by cross-encoder reranking."""

from rag.types import RagComponents, Retriever
from rag.variants.composed import ComposedRetriever, Stages


def build(components: RagComponents) -> Retriever:
    return ComposedRetriever(components, Stages(rerank=True))
