"""One retrieval pipeline whose stages switch on and off.

The numbered variants and the ablation study all use it, so a comparison changes one thing only.
"""

import math
import re
from dataclasses import dataclass
from itertools import chain

from agent.handoffs import EvidenceItem
from rag.index.bm25 import Bm25Cache
from rag.index.vector import ChunkHit
from rag.types import EvidenceFilters, RagComponents
from rag.variants.v0_naive import merge_hits_by_item

_ENUMERATION = re.compile(
    r"(?:requires?|required|must have|experience (?:with|in)|skills?|looking for)\s*:?\s*"
    r"(?P<items>[^.\n]+)",
    re.IGNORECASE,
)
_SEPARATOR = re.compile(r",|;|\band\b|/(?=\s)", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_MIN_SENTENCE_WORDS = 4


@dataclass(frozen=True)
class Stages:
    decompose: bool = False
    hybrid: bool = False
    rerank: bool = False
    outcome: bool = False


def decompose_query(query: str, limit: int) -> list[str]:
    """Split a job post into requirements, without calling a model.

    Listed requirements are used when the post has them. A vague post falls back to its
    sentences. A short query, such as one requirement from a tool call, stays whole.
    """
    listed = [
        part.strip(" .:-")
        for match in _ENUMERATION.finditer(query)
        for part in _SEPARATOR.split(match.group("items"))
    ]
    parts = [part for part in listed if part]
    if not parts:
        parts = [
            sentence.strip()
            for sentence in _SENTENCE.split(query)
            if len(sentence.split()) >= _MIN_SENTENCE_WORDS
        ]
    return list(dict.fromkeys(parts))[:limit] or [query]


def reciprocal_rank_fusion(rankings: list[list[ChunkHit]], constant: int) -> list[ChunkHit]:
    scores: dict[tuple[str, int], float] = {}
    hits: dict[tuple[str, int], ChunkHit] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            key = (hit.item_id, hit.payload["chunk"])
            scores[key] = scores.get(key, 0.0) + 1 / (constant + rank)
            hits[key] = hit
    ordered = sorted(scores, key=lambda key: (-scores[key], key))
    return [
        ChunkHit(hits[key].item_id, scores[key], hits[key].text, hits[key].payload)
        for key in ordered
    ]


class ComposedRetriever:
    def __init__(self, components: RagComponents, stages: Stages) -> None:
        if stages.rerank and components.reranker is None:
            raise ValueError("This retrieval variant needs a reranker")
        self._parts = components
        self._stages = stages
        settings = components.config
        self._bm25 = Bm25Cache(
            components.corpus, settings.chunk_size_words, settings.chunk_overlap_words
        )

    def _rankings(
        self,
        queries: list[str],
        tenant: str,
        filters: EvidenceFilters | None,
    ) -> list[list[ChunkHit]]:
        parts = self._parts
        pool = parts.config.fusion_candidates
        rankings = [
            parts.index.search(parts.embedder.embed_query(query), tenant, pool, filters)
            for query in queries
        ]
        if self._stages.hybrid:
            index = self._bm25.index_for(tenant)
            rankings += [index.search(query, pool, filters) for query in queries]
        return rankings

    def _reranked(self, query: str, hits: list[ChunkHit]) -> list[ChunkHit]:
        reranker = self._parts.reranker
        assert reranker is not None
        top = hits[: self._parts.config.rerank_candidates]
        scores = reranker.score(query, [hit.text for hit in top])
        rescored = [
            ChunkHit(hit.item_id, 1 / (1 + math.exp(-score)), hit.text, hit.payload)
            for hit, score in zip(top, scores, strict=True)
        ]
        return sorted(rescored, key=lambda hit: -hit.score)

    def _with_outcomes(self, hits: list[ChunkHit]) -> list[ChunkHit]:
        boosts = self._parts.config.outcome_boosts
        boosted = [
            ChunkHit(
                hit.item_id,
                hit.score * boosts.get(hit.payload.get("outcome") or "", 1.0),
                hit.text,
                hit.payload,
            )
            for hit in hits
        ]
        return sorted(boosted, key=lambda hit: -hit.score)

    def retrieve(
        self,
        query: str,
        tenant: str,
        k: int,
        filters: EvidenceFilters | None = None,
    ) -> list[EvidenceItem]:
        stages = self._stages
        limit = self._parts.config.max_subqueries
        queries = decompose_query(query, limit) if stages.decompose else [query]
        hits = reciprocal_rank_fusion(
            self._rankings(queries, tenant, filters), self._parts.config.rrf_k
        )
        if stages.rerank:
            hits = self._reranked(query, hits)
        if stages.outcome:
            hits = self._with_outcomes(hits)
        return merge_hits_by_item(hits, k)


def parse_stages(names: str) -> Stages:
    """Parse e.g. 'decompose+hybrid'. Unknown stage names are refused."""
    wanted = set(chain.from_iterable(part.split(",") for part in names.split("+")))
    known = {"decompose", "hybrid", "rerank", "outcome"}
    if wanted - known:
        raise ValueError(f"Unknown retrieval stages: {sorted(wanted - known)}")
    return Stages(**{name: True for name in wanted})
