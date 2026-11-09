"""V0: fixed chunks and top-k cosine similarity on the whole job post."""

from agent.handoffs import EvidenceItem
from rag.index.vector import ChunkHit
from rag.types import EvidenceFilters, RagComponents, Retriever


def merge_hits_by_item(hits: list[ChunkHit], k: int) -> list[EvidenceItem]:
    """Keep each item's best chunk, in rank order. Evidence is judged per item, not per chunk."""
    seen: set[str] = set()
    items: list[EvidenceItem] = []
    for hit in hits:
        if hit.item_id in seen:
            continue
        seen.add(hit.item_id)
        items.append(
            EvidenceItem(
                evidence_id=hit.item_id,
                type=hit.payload["type"],
                source=hit.payload["source"],
                excerpt=hit.text,
                date=hit.payload.get("date"),
                outcome=hit.payload.get("outcome"),
                score=hit.score,
            )
        )
        if len(items) == k:
            break
    return items


class NaiveDenseRetriever:
    def __init__(self, components: RagComponents) -> None:
        self._components = components

    def retrieve(
        self,
        query: str,
        tenant: str,
        k: int,
        filters: EvidenceFilters | None = None,
    ) -> list[EvidenceItem]:
        parts = self._components
        vector = parts.embedder.embed_query(query)
        limit = k * parts.config.candidate_multiplier
        return merge_hits_by_item(parts.index.search(vector, tenant, limit, filters), k)


def build(components: RagComponents) -> Retriever:
    return NaiveDenseRetriever(components)
