"""V4: boost evidence from proposals that won or got replies, and demote the ignored."""

from rag.types import RagComponents, Retriever
from rag.variants.composed import ComposedRetriever, Stages


def build(components: RagComponents) -> Retriever:
    return ComposedRetriever(components, Stages(outcome=True))
