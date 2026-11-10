"""Incremental indexing: only new or changed items are embedded again.

Usage: python -m rag.ingest.pipeline --personas data/processed/devsim/personas
"""

import argparse
import hashlib
from dataclasses import dataclass
from pathlib import Path

from rag.ingest.chunkers import chunk_words
from rag.ingest.loaders import PortfolioDocument, load_portfolio
from rag.types import RagComponents


@dataclass(frozen=True)
class IngestReport:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    chunks_embedded: int = 0


def _content_hash(document: PortfolioDocument, components: RagComponents) -> str:
    rag = components.config
    settings = f"{rag.chunk_size_words}:{rag.chunk_overlap_words}:{components.embedder.name}"
    return hashlib.sha256(f"{settings}|{document.model_dump_json()}".encode()).hexdigest()


def ingest_documents(
    documents: list[PortfolioDocument],
    tenant: str,
    components: RagComponents,
    remove_missing: bool = False,
) -> IngestReport:
    """Index documents for one tenant.

    The corpus row is written last, so an interrupted run re-processes that item next time.
    Pass remove_missing=True for a full sync. A single upload leaves other items alone.
    """
    known = components.corpus.hashes(tenant)
    rag = components.config
    added = updated = unchanged = embedded = 0

    for document in documents:
        content_hash = _content_hash(document, components)
        previous = known.get(document.item_id)
        if previous == content_hash:
            unchanged += 1
            continue
        chunks = chunk_words(document.text, rag.chunk_size_words, rag.chunk_overlap_words)
        vectors = components.embedder.embed_documents(chunks)
        components.index.replace_item(tenant, document, chunks, vectors)
        components.corpus.upsert(tenant, document, content_hash)
        embedded += len(chunks)
        if previous is None:
            added += 1
        else:
            updated += 1

    removed = 0
    if remove_missing:
        current = {document.item_id for document in documents}
        for item_id in sorted(set(known) - current):
            remove_item(tenant, item_id, components)
            removed += 1
    return IngestReport(added, updated, unchanged, removed, embedded)


def provision_tenant(template: str, tenant: str, components: RagComponents) -> None:
    """Give a user a private copy of a persona's portfolio, once.

    Vectors are copied first: an interrupted copy leaves the corpus empty, so it simply runs again.
    Each user can then add or delete evidence without affecting anyone else.
    """
    if components.corpus.hashes(tenant):
        return
    components.index.copy_tenant(template, tenant)
    components.corpus.copy_tenant(template, tenant)


def remove_item(tenant: str, item_id: str, components: RagComponents) -> None:
    components.index.delete_item(tenant, item_id)
    components.corpus.delete(tenant, item_id)


def main() -> None:
    from rag.retriever import open_components

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--personas", type=Path, default=Path("data/processed/devsim/personas"))
    arguments = parser.parse_args()
    components = open_components()
    for persona in sorted(path for path in arguments.personas.iterdir() if path.is_dir()):
        report = ingest_documents(
            load_portfolio(persona),
            persona.name,
            components,
            remove_missing=True,
        )
        print(f"{persona.name}: {report}")
    components.close()


if __name__ == "__main__":
    main()
