"""V2: BM25 plus dense retrieval, combined with reciprocal rank fusion."""

from rag.types import RagComponents, Retriever
from rag.variants.composed import ComposedRetriever, Stages


def build(components: RagComponents) -> Retriever:
    return ComposedRetriever(components, Stages(hybrid=True))
