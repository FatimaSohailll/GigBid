"""Build the retrieval variant named in config."""

import os
from collections.abc import Callable, Mapping
from pathlib import Path

from qdrant_client import QdrantClient

from agent.config import AppConfig, get_config
from rag.index.corpus import CorpusStore
from rag.index.embedder import Embedder, FastEmbedEmbedder
from rag.index.vector import VectorIndex
from rag.types import RagComponents, Retriever
from rag.variants import v0_naive

VARIANTS: dict[str, Callable[[RagComponents], Retriever]] = {
    "v0_naive": v0_naive.build,
}


def build_retriever(components: RagComponents, variant: str | None = None) -> Retriever:
    name = variant or components.config.variant
    if name not in VARIANTS:
        raise ValueError(f"Unknown retrieval variant '{name}'. Available: {sorted(VARIANTS)}")
    return VARIANTS[name](components)


def open_components(
    config: AppConfig | None = None,
    environ: Mapping[str, str] | None = None,
    embedder: Embedder | None = None,
) -> RagComponents:
    """Open the stores. QDRANT_URL selects a server; otherwise Qdrant runs embedded on disk."""
    app = config or get_config()
    env = os.environ if environ is None else environ
    data_dir = Path(env.get("RAG_DATA_DIR", "data/processed/rag"))
    data_dir.mkdir(parents=True, exist_ok=True)
    model = embedder or FastEmbedEmbedder(
        app.models.embeddings,
        app.rag.embedding_query_prefix,
        app.rag.embedding_document_prefix,
    )
    url = env.get("QDRANT_URL")
    client = QdrantClient(url=url) if url else QdrantClient(path=str(data_dir / "qdrant"))
    return RagComponents(
        corpus=CorpusStore(data_dir / "corpus.sqlite3"),
        index=VectorIndex(client, app.rag.collection, model.dimension),
        embedder=model,
        config=app.rag,
    )
