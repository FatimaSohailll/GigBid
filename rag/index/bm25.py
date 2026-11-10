"""Okapi BM25 over the same chunks as the vector index, built per tenant from the corpus."""

import math
import re
from collections import Counter, defaultdict
from typing import Any

from rag.index.corpus import CorpusStore
from rag.index.vector import ChunkHit, chunk_payload
from rag.ingest.chunkers import chunk_words
from rag.types import EvidenceFilters

_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#._-]*")
_K1 = 1.5
_B = 0.75


def tokenize(text: str) -> list[str]:
    """Keeps names like next.js, c++ and great_expectations whole."""
    return [token.rstrip("._-") for token in _TOKEN.findall(text.lower())]


def matches_filters(payload: dict[str, Any], filters: EvidenceFilters | None) -> bool:
    if filters is None:
        return True
    wanted_skills = {skill.lower() for skill in filters.skills}
    return (
        (not filters.types or payload["type"] in filters.types)
        and (not wanted_skills or bool(wanted_skills & set(payload["skills"])))
        and (not filters.outcomes or payload["outcome"] in filters.outcomes)
        and (
            not filters.client_industries or payload["client_industry"] in filters.client_industries
        )
        and (filters.project is None or payload["project"] == filters.project)
        and (
            filters.min_year is None
            or (payload["year"] is not None and payload["year"] >= filters.min_year)
        )
    )


class Bm25Index:
    def __init__(self, chunks: list[tuple[str, dict[str, Any]]]) -> None:
        self._chunks = chunks
        self._frequencies = [Counter(tokenize(text)) for text, _ in chunks]
        self._lengths = [sum(counts.values()) for counts in self._frequencies]
        self._average = sum(self._lengths) / len(chunks) if chunks else 0.0
        self._postings: defaultdict[str, list[int]] = defaultdict(list)
        for position, counts in enumerate(self._frequencies):
            for token in counts:
                self._postings[token].append(position)

    def search(
        self,
        query: str,
        limit: int,
        filters: EvidenceFilters | None = None,
    ) -> list[ChunkHit]:
        scores: defaultdict[int, float] = defaultdict(float)
        total = len(self._chunks)
        for token in set(tokenize(query)):
            postings = self._postings.get(token, [])
            if not postings:
                continue
            idf = math.log(1 + (total - len(postings) + 0.5) / (len(postings) + 0.5))
            for position in postings:
                frequency = self._frequencies[position][token]
                norm = 1 - _B + _B * self._lengths[position] / self._average
                scores[position] += idf * frequency * (_K1 + 1) / (frequency + _K1 * norm)
        ranked = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
        hits: list[ChunkHit] = []
        for position, score in ranked:
            text, payload = self._chunks[position]
            if matches_filters(payload, filters):
                hits.append(ChunkHit(payload["item_id"], score, text, payload))
                if len(hits) == limit:
                    break
        return hits


class Bm25Cache:
    """Rebuilds a tenant's index only when its corpus has changed."""

    def __init__(self, corpus: CorpusStore, chunk_size: int, overlap: int) -> None:
        self._corpus = corpus
        self._chunk_size = chunk_size
        self._overlap = overlap
        self._indexes: dict[str, tuple[int, Bm25Index]] = {}

    def index_for(self, tenant: str) -> Bm25Index:
        version = hash(tuple(sorted(self._corpus.hashes(tenant).values())))
        cached = self._indexes.get(tenant)
        if cached is not None and cached[0] == version:
            return cached[1]
        chunks = [
            # Same chunk numbers as the vector index, so fusion can match the two rankings.
            (text, {**chunk_payload(tenant, document), "chunk": number, "text": text})
            for document in self._corpus.documents(tenant)
            for number, text in enumerate(
                chunk_words(document.text, self._chunk_size, self._overlap)
            )
        ]
        index = Bm25Index(chunks)
        self._indexes[tenant] = (version, index)
        return index
