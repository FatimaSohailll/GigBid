"""Qdrant vector index. Every operation requires a tenant, so data cannot cross users."""

import uuid
from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient, models

from rag.ingest.loaders import PortfolioDocument
from rag.types import EvidenceFilters

_POINT_NAMESPACE = uuid.UUID("8f1c9a52-7f4e-4a53-9d2e-0c5a7f3e1b64")


@dataclass(frozen=True)
class ChunkHit:
    item_id: str
    score: float
    text: str
    payload: dict[str, Any]


def _year(date: str | None) -> int | None:
    return int(date[:4]) if date and date[:4].isdigit() else None


def chunk_payload(tenant: str, document: PortfolioDocument) -> dict[str, Any]:
    return {
        "tenant": tenant,
        "item_id": document.item_id,
        "type": document.type,
        "source": document.source,
        "project": document.project,
        "skills": [skill.lower() for skill in document.skills],
        "date": document.date,
        "year": _year(document.date),
        "client_industry": document.client_industry,
        "outcome": document.outcome,
    }


class VectorIndex:
    def __init__(self, client: QdrantClient, collection: str, dimension: int) -> None:
        self._client = client
        self._collection = collection
        if not client.collection_exists(collection):
            client.create_collection(
                collection,
                vectors_config=models.VectorParams(
                    size=dimension,
                    distance=models.Distance.COSINE,
                ),
            )

    def close(self) -> None:
        self._client.close()

    def _item_filter(self, tenant: str, item_id: str) -> models.Filter:
        return models.Filter(
            must=[
                models.FieldCondition(key="tenant", match=models.MatchValue(value=tenant)),
                models.FieldCondition(key="item_id", match=models.MatchValue(value=item_id)),
            ]
        )

    def delete_item(self, tenant: str, item_id: str) -> None:
        self._client.delete(
            self._collection,
            points_selector=models.FilterSelector(filter=self._item_filter(tenant, item_id)),
        )

    def replace_item(
        self,
        tenant: str,
        document: PortfolioDocument,
        chunks: list[str],
        vectors: list[list[float]],
    ) -> None:
        self.delete_item(tenant, document.item_id)
        if not chunks:
            return
        payload = chunk_payload(tenant, document)
        self._client.upsert(
            self._collection,
            points=[
                models.PointStruct(
                    id=str(uuid.uuid5(_POINT_NAMESPACE, f"{tenant}|{document.item_id}|{number}")),
                    vector=vector,
                    payload={**payload, "chunk": number, "text": text},
                )
                for number, (text, vector) in enumerate(zip(chunks, vectors, strict=True))
            ],
        )

    def search(
        self,
        vector: list[float],
        tenant: str,
        limit: int,
        filters: EvidenceFilters | None = None,
    ) -> list[ChunkHit]:
        must: list[models.Condition] = [
            models.FieldCondition(key="tenant", match=models.MatchValue(value=tenant))
        ]
        if filters is not None:
            for key, values in (
                ("type", filters.types),
                ("skills", [skill.lower() for skill in filters.skills]),
                ("outcome", filters.outcomes),
                ("client_industry", filters.client_industries),
            ):
                if values:
                    must.append(models.FieldCondition(key=key, match=models.MatchAny(any=values)))
            if filters.project:
                must.append(
                    models.FieldCondition(
                        key="project",
                        match=models.MatchValue(value=filters.project),
                    )
                )
            if filters.min_year is not None:
                must.append(
                    models.FieldCondition(key="year", range=models.Range(gte=filters.min_year))
                )
        points = self._client.query_points(
            self._collection,
            query=vector,
            query_filter=models.Filter(must=must),
            limit=limit,
            with_payload=True,
        ).points
        return [
            ChunkHit(
                item_id=point.payload["item_id"],
                score=point.score,
                text=point.payload["text"],
                payload=point.payload,
            )
            for point in points
            if point.payload is not None
        ]
