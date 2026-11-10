import json
from pathlib import Path

import pytest

from devsim.datagen.generate import generate
from evals.metrics import ndcg_at_k, recall_at_k, reciprocal_rank
from evals.runners.rag_eval import evaluate_whole_job
from rag.index.embedder import FastEmbedEmbedder
from rag.ingest.chunkers import chunk_words
from rag.ingest.loaders import load_portfolio, split_front_matter
from rag.ingest.pipeline import ingest_documents, remove_item
from rag.retriever import VARIANTS, build_retriever
from rag.types import EvidenceFilters, RagComponents
from tests.support.rag_fakes import document, memory_components


@pytest.fixture
def components(tmp_path: Path) -> RagComponents:
    return memory_components(tmp_path)


@pytest.fixture(scope="module")
def persona_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("persona")
    generate(out, history=5, per_persona=8, stream=4)
    return out / "personas" / "ai_ml_engineer"


def test_chunk_words_overlaps_and_covers_all_words() -> None:
    words = [f"w{i}" for i in range(25)]
    chunks = chunk_words(" ".join(words), 10, 2)
    assert chunks[0].split() == words[:10]
    assert chunks[1].split()[:2] == words[8:10]
    assert chunks[-1].split()[-1] == "w24"
    assert {word for chunk in chunks for word in chunk.split()} == set(words)


def test_chunk_words_handles_short_and_empty_text() -> None:
    assert chunk_words("one two", 10, 2) == ["one two"]
    assert chunk_words("   ", 10, 2) == []


def test_front_matter_is_separated() -> None:
    meta, body = split_front_matter("---\ntitle: T\nskills: [A, B]\n---\n\nBody\n")
    assert meta == {"title": "T", "skills": ["A", "B"]}
    assert body == "Body\n"
    assert split_front_matter("no header") == ({}, "no header")


def test_loader_reads_every_item_type_with_metadata(persona_root: Path) -> None:
    documents = load_portfolio(persona_root)
    by_type = {doc.type for doc in documents}
    assert by_type == {"cv", "case_study", "readme", "code", "review", "proposal"}
    assert all(doc.text.strip() for doc in documents)
    cv = next(doc for doc in documents if doc.type == "cv")
    assert "Amara Vale" in cv.text
    case = next(doc for doc in documents if doc.item_id == "case:ai_ml_engineer:churn-radar")
    assert not case.text.startswith("---")
    assert (case.project, case.client_industry) == ("churn-radar", "telecom")
    assert "XGBoost" in case.skills
    proposal = next(doc for doc in documents if doc.type == "proposal")
    assert proposal.outcome in {"won", "replied", "ignored"}
    code = next(doc for doc in documents if doc.item_id == "code:ai_ml_engineer:churn-radar")
    assert "shap" in code.skills


def test_ingest_is_incremental(components: RagComponents) -> None:
    first = [document("a", "alpha text"), document("b", "beta text")]
    assert ingest_documents(first, "t1", components).added == 2
    embedded = components.embedder.documents_embedded  # type: ignore[attr-defined]

    second = ingest_documents(first, "t1", components)
    assert second.unchanged == 2 and second.chunks_embedded == 0
    assert components.embedder.documents_embedded == embedded  # type: ignore[attr-defined]

    changed = [document("a", "alpha changed"), document("b", "beta text")]
    third = ingest_documents(changed, "t1", components)
    assert (third.updated, third.unchanged, third.chunks_embedded) == (1, 1, 1)


def test_full_sync_removes_missing_items_but_a_single_upload_does_not(
    components: RagComponents,
) -> None:
    ingest_documents([document("a", "alpha"), document("b", "beta")], "t1", components)
    upload = ingest_documents([document("c", "gamma")], "t1", components)
    assert upload.removed == 0 and len(components.corpus.hashes("t1")) == 3
    sync = ingest_documents([document("c", "gamma")], "t1", components, remove_missing=True)
    assert sync.removed == 2 and set(components.corpus.hashes("t1")) == {"c"}
    retriever = build_retriever(components)
    assert [item.evidence_id for item in retriever.retrieve("alpha", "t1", 5)] == ["c"]


def test_updating_an_item_replaces_its_old_chunks(components: RagComponents) -> None:
    long_text = " ".join(f"word{i}" for i in range(800))
    ingest_documents([document("a", long_text)], "t1", components)
    ingest_documents([document("a", "short replacement text")], "t1", components)
    hits = build_retriever(components).retrieve("word100", "t1", 5)
    assert [item.excerpt for item in hits] == ["short replacement text"]


def test_v0_ranks_by_similarity_and_returns_required_fields(components: RagComponents) -> None:
    ingest_documents(
        [
            document("kafka", "streaming pipeline kafka consumers", date="2025", outcome="won"),
            document("salad", "recipe tomato salad dressing"),
        ],
        "t1",
        components,
    )
    items = build_retriever(components).retrieve("kafka streaming", "t1", 1)
    assert [item.evidence_id for item in items] == ["kafka"]
    assert (items[0].type, items[0].source, items[0].date, items[0].outcome) == (
        "case_study",
        "kafka.md",
        "2025",
        "won",
    )


def test_results_merge_chunks_into_items(components: RagComponents) -> None:
    text = " ".join(["kafka streaming"] * 400)
    ingest_documents([document("a", text), document("b", "kafka notes")], "t1", components)
    items = build_retriever(components).retrieve("kafka streaming", "t1", 5)
    assert sorted(item.evidence_id for item in items) == ["a", "b"]


def test_tenants_are_isolated(components: RagComponents) -> None:
    ingest_documents([document("mine", "secret kafka work")], "t1", components)
    ingest_documents([document("yours", "other kafka work")], "t2", components)
    retriever = build_retriever(components)
    assert [item.evidence_id for item in retriever.retrieve("kafka", "t2", 5)] == ["yours"]
    assert components.corpus.get("t2", "mine") is None


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (EvidenceFilters(types=["review"]), {"b"}),
        (EvidenceFilters(skills=["KAFKA"]), {"a"}),
        (EvidenceFilters(outcomes=["won"]), {"a"}),
        (EvidenceFilters(client_industries=["media"]), {"b"}),
        (EvidenceFilters(project="p2"), {"b"}),
        (EvidenceFilters(min_year=2025), {"a"}),
        (EvidenceFilters(types=["review"], outcomes=["won"]), set()),
    ],
)
def test_filters(components: RagComponents, filters: EvidenceFilters, expected: set[str]) -> None:
    ingest_documents(
        [
            document(
                "a",
                "shared words",
                skills=["Kafka"],
                outcome="won",
                project="p1",
                date="2025-02-01",
                client_industry="retail",
            ),
            document(
                "b",
                "shared words",
                type="review",
                project="p2",
                date="2023",
                client_industry="media",
            ),
        ],
        "t1",
        components,
    )
    items = build_retriever(components).retrieve("shared words", "t1", 5, filters)
    assert {item.evidence_id for item in items} == expected


def test_retriever_factory_uses_config_and_rejects_unknown(components: RagComponents) -> None:
    assert components.config.variant in VARIANTS
    with pytest.raises(ValueError, match="Unknown retrieval variant"):
        build_retriever(components, "v99_magic")


def test_removed_item_is_gone_from_store_and_index(components: RagComponents) -> None:
    ingest_documents([document("a", "kafka work")], "t1", components)
    remove_item("t1", "a", components)
    assert components.corpus.get("t1", "a") is None
    assert build_retriever(components).retrieve("kafka", "t1", 5) == []


def test_skills_are_listed_with_counts(components: RagComponents) -> None:
    ingest_documents(
        [
            document("a", "x", skills=["Kafka", "SQL"]),
            document("b", "y", type="code", skills=["kafka"]),
        ],
        "t1",
        components,
    )
    summary = {row.skill: row for row in components.corpus.skills("t1")}
    assert summary["Kafka"].item_count == 2
    assert summary["Kafka"].item_types == ["case_study", "code"]
    assert components.corpus.skills("other") == []


def test_real_embedder_applies_prefixes_without_loading_a_model() -> None:
    class Model:
        def __init__(self) -> None:
            self.seen: list[str] = []

        def embed(self, texts: list[str]) -> list[object]:
            self.seen += texts
            return []

    embedder = FastEmbedEmbedder.__new__(FastEmbedEmbedder)
    model = Model()
    embedder._model = model  # type: ignore[assignment]
    embedder._query_prefix = "search_query: "
    embedder._document_prefix = "search_document: "
    embedder.embed_documents(["doc"])
    assert model.seen == ["search_document: doc"]


@pytest.mark.parametrize(
    ("ranked", "relevant", "expected"),
    [(["a", "x", "b"], ["a", "b"], 1.0), (["x", "y"], ["a"], 0.0), (["a", "x"], ["a", "b"], 0.5)],
)
def test_recall(ranked: list[str], relevant: list[str], expected: float) -> None:
    assert recall_at_k(ranked, relevant, 5) == expected


def test_recall_respects_k() -> None:
    assert recall_at_k(["x", "a"], ["a"], 1) == 0.0


def test_reciprocal_rank() -> None:
    assert reciprocal_rank(["x", "a"], {"a"}) == 0.5
    assert reciprocal_rank(["x"], {"a"}) == 0.0


def test_ndcg() -> None:
    assert ndcg_at_k(["a", "b"], ["a", "b"], 10) == pytest.approx(1.0)
    assert ndcg_at_k(["x", "a"], ["a"], 10) == pytest.approx(0.63093, abs=1e-4)
    assert ndcg_at_k([], [], 10) == 0.0


def test_evaluation_reports_overall_persona_and_job_type(
    components: RagComponents,
    persona_root: Path,
) -> None:
    ingest_documents(load_portfolio(persona_root), "ai_ml_engineer", components)
    data = persona_root.parent.parent
    (data / "only_one").mkdir(exist_ok=True)
    source = (data / "dev_set" / "ai_ml_engineer.jsonl").read_bytes()
    (data / "only_one" / "ai_ml_engineer.jsonl").write_bytes(source)

    jobs_file = data / "marketplace" / "jobs.json"
    result = evaluate_whole_job(
        build_retriever(components),
        [
            json.loads(line)
            for path in sorted((data / "only_one").glob("*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines()
        ],
        {job["job_id"]: job for job in json.loads(jobs_file.read_text(encoding="utf-8"))},
    )

    assert set(result["by_persona"]) == {"ai_ml_engineer"}
    assert result["overall"]["count"] > 0
    assert 0 <= result["overall"]["recall_at_5"] <= 1
    assert result["by_job_type"]
