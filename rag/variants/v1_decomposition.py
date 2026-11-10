"""V1: one retrieval per extracted requirement, then merge."""

from rag.types import RagComponents, Retriever
from rag.variants.composed import ComposedRetriever, Stages


def build(components: RagComponents) -> Retriever:
    return ComposedRetriever(components, Stages(decompose=True))
