"""The full text and metadata of each portfolio item, always scoped by tenant."""

import sqlite3
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel

from rag.ingest.loaders import PortfolioDocument

_SCHEMA = """
create table if not exists items (
    tenant text not null,
    item_id text not null,
    content_hash text not null,
    document text not null,
    primary key (tenant, item_id)
);
"""


class SkillSummary(BaseModel):
    skill: str
    item_count: int
    item_types: list[str]


class CorpusStore:
    def __init__(self, path: Path | str) -> None:
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def hashes(self, tenant: str) -> dict[str, str]:
        rows = self._db.execute("select item_id, content_hash from items where tenant=?", (tenant,))
        return dict(rows.fetchall())

    def upsert(self, tenant: str, document: PortfolioDocument, content_hash: str) -> None:
        self._db.execute(
            "insert or replace into items values (?, ?, ?, ?)",
            (tenant, document.item_id, content_hash, document.model_dump_json()),
        )

    def delete(self, tenant: str, item_id: str) -> None:
        self._db.execute("delete from items where tenant=? and item_id=?", (tenant, item_id))

    def get(self, tenant: str, item_id: str) -> PortfolioDocument | None:
        row = self._db.execute(
            "select document from items where tenant=? and item_id=?",
            (tenant, item_id),
        ).fetchone()
        return PortfolioDocument.model_validate_json(row[0]) if row else None

    def documents(self, tenant: str) -> list[PortfolioDocument]:
        rows = self._db.execute(
            "select document from items where tenant=? order by item_id",
            (tenant,),
        )
        return [PortfolioDocument.model_validate_json(row[0]) for row in rows]

    def skills(self, tenant: str) -> list[SkillSummary]:
        """Every skill the portfolio can evidence, with how many items show it."""
        names: dict[str, str] = {}
        items: defaultdict[str, set[str]] = defaultdict(set)
        types: defaultdict[str, set[str]] = defaultdict(set)
        for document in self.documents(tenant):
            for skill in document.skills:
                key = skill.lower()
                names.setdefault(key, skill)
                items[key].add(document.item_id)
                types[key].add(document.type)
        return [
            SkillSummary(
                skill=names[key], item_count=len(items[key]), item_types=sorted(types[key])
            )
            for key in sorted(names)
        ]
