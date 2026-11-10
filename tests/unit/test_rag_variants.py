from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from rag.index.bm25 import Bm25Cache, Bm25Index, tokenize
from rag.index.vector import ChunkHit
from rag.ingest.pipeline import ingest_documents
from rag.retriever import VARIANTS, build_retriever
from rag.types import EvidenceFilters, RagComponents
from rag.variants.composed import decompose_query, parse_stages, reciprocal_rank_fusion
from tests.support.rag_fakes import document, memory_components


class FakeReranker:
    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        return [5.0 if "best" in text else -5.0 for text in documents]


@pytest.fixture
def components(tmp_path: Path) -> RagComponents:
    return replace(memory_components(tmp_path), reranker=FakeReranker())


def ingest(components: RagComponents, *items: object) -> None:
    ingest_documents(list(items), "t1", components)  # type: ignore[arg-type]


def ids(components: RagComponents, variant: str, query: str, k: int = 5) -> list[str]:
    retriever = build_retriever(components, variant)
    return [item.evidence_id for item in retriever.retrieve(query, "t1", k)]


def test_tokenizer_keeps_technical_names_whole() -> None:
    assert tokenize("Next.js, C++ and great_expectations.") == [
        "next.js",
        "c++",
        "and",
        "great_expectations",
    ]


def test_bm25_ranks_rare_exact_terms_first() -> None:
    def payload(item: str) -> dict[str, object]:
        return {"item_id": item, "chunk": 0}

    index = Bm25Index(
        [("celery task queues", payload("a")), ("task lists and task notes", payload("b"))]
    )
    assert [hit.item_id for hit in index.search("celery task", 5)] == ["a", "b"]


def test_decomposition_uses_listed_requirements() -> None:
    query = "Title\nThe work requires: PyTorch, Neo4j and Anomaly Detection. Please apply."
    assert decompose_query(query, 8) == ["PyTorch", "Neo4j", "Anomaly Detection"]


def test_decomposition_falls_back_to_sentences_and_keeps_short_queries() -> None:
    vague = "We lose customers every month. Nobody knows why this keeps happening."
    assert len(decompose_query(vague, 8)) == 2
    assert decompose_query("Kubernetes", 8) == ["Kubernetes"]


def test_rrf_rewards_agreement_between_rankings() -> None:
    def hit(item: str) -> ChunkHit:
        return ChunkHit(item, 1.0, item, {"chunk": 0})

    fused = reciprocal_rank_fusion([[hit("a"), hit("b")], [hit("c"), hit("b")]], 60)
    assert fused[0].item_id == "b"


def test_bm25_and_vector_chunks_share_ids_so_fusion_can_match_them(
    components: RagComponents,
) -> None:
    ingest(components, document("a", "celery task queues"))
    settings = components.config
    cache = Bm25Cache(components.corpus, settings.chunk_size_words, settings.chunk_overlap_words)
    dense = components.index.search(components.embedder.embed_query("celery"), "t1", 1, None)
    sparse = cache.index_for("t1").search("celery", 1)
    fused = reciprocal_rank_fusion([dense, sparse], settings.rrf_k)
    assert len(fused) == 1
    assert fused[0].score == pytest.approx(2 / (settings.rrf_k + 1))


def test_every_variant_is_registered_and_retrieves(components: RagComponents) -> None:
    ingest(components, document("a", "kafka streaming consumers"))
    for variant in VARIANTS:
        assert ids(components, variant, "kafka streaming") == ["a"], variant


def test_hybrid_finds_an_exact_term(components: RagComponents) -> None:
    ingest(components, document("target", "celery"), document("other", "gardening tools"))
    assert ids(components, "v2_hybrid", "celery", 1) == ["target"]


def test_rerank_puts_the_best_chunk_first(components: RagComponents) -> None:
    ingest(components, document("a", "kafka words"), document("b", "best kafka words"))
    assert ids(components, "v3_rerank", "kafka words")[0] == "b"


def test_rerank_without_a_model_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="reranker"):
        build_retriever(memory_components(tmp_path), "v3_rerank")


def test_outcome_boost_demotes_ignored_proposals(components: RagComponents) -> None:
    ingest(
        components,
        document("ignored", "same shared words", outcome="ignored"),
        document("won", "same shared words", outcome="won"),
    )
    assert ids(components, "v4_outcome", "same shared words")[0] == "won"


def test_corrective_refuses_when_nothing_is_relevant(components: RagComponents) -> None:
    ingest(components, document("a", "gardening tools for roses"))
    assert ids(components, "v8_corrective", "The work requires: Kubernetes, Terraform.") == []


def test_corrective_keeps_supported_requirements(components: RagComponents) -> None:
    ingest(components, document("a", "kubernetes clusters"), document("b", "terraform modules"))
    found = ids(components, "v8_corrective", "The work requires: Kubernetes, Terraform.")
    assert set(found) == {"a", "b"}


def test_combinations_parse_and_reject_unknown_stages() -> None:
    assert parse_stages("decompose+rerank").rerank is True
    with pytest.raises(ValueError, match="magic"):
        parse_stages("hybrid+magic")


def test_hybrid_respects_filters_and_tenants(components: RagComponents) -> None:
    ingest(components, document("a", "celery", type="review"), document("b", "celery"))
    ingest_documents([document("other", "celery")], "t2", components)
    items = build_retriever(components, "v2_hybrid").retrieve(
        "celery", "t1", 5, EvidenceFilters(types=["review"])
    )
    assert [item.evidence_id for item in items] == ["a"]
