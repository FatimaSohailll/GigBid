"""Types shared by the index, ingestion and retrieval variants."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict, Field

from agent.config import RagConfig
from agent.handoffs import EvidenceItem

if TYPE_CHECKING:
    from rag.index.corpus import CorpusStore
    from rag.index.embedder import Embedder
    from rag.index.vector import VectorIndex


class EvidenceFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    types: list[str] = Field(default_factory=list, max_length=10)
    skills: list[str] = Field(default_factory=list, max_length=20)
    outcomes: list[str] = Field(default_factory=list, max_length=5)
    client_industries: list[str] = Field(default_factory=list, max_length=20)
    project: str | None = Field(default=None, max_length=128)
    min_year: int | None = Field(default=None, ge=1990, le=2100)


@dataclass(frozen=True)
class RagComponents:
    corpus: "CorpusStore"
    index: "VectorIndex"
    embedder: "Embedder"
    config: RagConfig

    def close(self) -> None:
        self.index.close()
        self.corpus.close()


class Retriever(Protocol):
    def retrieve(
        self,
        query: str,
        tenant: str,
        k: int,
        filters: EvidenceFilters | None = None,
    ) -> list[EvidenceItem]: ...
