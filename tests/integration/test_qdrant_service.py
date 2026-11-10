"""The Qdrant service lets separate processes share the index, which embedded Qdrant cannot."""

import os
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from rag.index.corpus import CorpusStore
from rag.index.vector import VectorIndex
from rag.ingest.loaders import PortfolioDocument
from tests.support.rag_fakes import document

URL = os.environ.get("TEST_QDRANT_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_QDRANT_URL is not set")
_DIMENSION = 3
_VECTOR = [1.0, 0.0, 0.0]


def connect() -> QdrantClient:
    assert URL is not None
    deadline = time.monotonic() + 30
    while True:
        client = QdrantClient(url=URL)
        try:
            client.get_collections()
            return client
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


@pytest.fixture
def collection() -> Iterator[str]:
    name = f"test_{uuid.uuid4().hex}"
    yield name
    connect().delete_collection(name)


def stored(item: PortfolioDocument) -> tuple[list[str], list[list[float]]]:
    return [item.text], [_VECTOR]


def test_two_clients_share_one_index_and_tenants_stay_separate(collection: str) -> None:
    worker = VectorIndex(connect(), collection, _DIMENSION)
    portfolio = VectorIndex(connect(), collection, _DIMENSION)
    item = document("case:a", "kafka")

    worker.replace_item("persona", item, *stored(item))
    assert [hit.item_id for hit in portfolio.search(_VECTOR, "persona", 5, None)] == ["case:a"]
    assert portfolio.search(_VECTOR, "user1", 5, None) == []

    worker.copy_tenant("persona", "user1")
    assert [hit.item_id for hit in portfolio.search(_VECTOR, "user1", 5, None)] == ["case:a"]
    portfolio.delete_item("user1", "case:a")
    assert portfolio.search(_VECTOR, "user1", 5, None) == []
    assert portfolio.search(_VECTOR, "persona", 5, None) != []


def test_corpus_copy_keeps_tenants_separate(tmp_path: Path) -> None:
    corpus = CorpusStore(tmp_path / "corpus.sqlite3")
    corpus.upsert("persona", document("case:a", "kafka"), "hash")
    corpus.copy_tenant("persona", "user1")
    corpus.delete("user1", "case:a")
    assert corpus.hashes("persona") == {"case:a": "hash"}
