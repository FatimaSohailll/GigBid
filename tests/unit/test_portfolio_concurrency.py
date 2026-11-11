import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from rag.index.corpus import CorpusStore
from tests.support.rag_fakes import document
from worker import tasks
from worker.tasks import WorkerServices


def test_one_corpus_connection_survives_many_threads(tmp_path: Path) -> None:
    store = CorpusStore(tmp_path / "corpus.sqlite3")

    def work(number: int) -> None:
        for step in range(40):
            store.upsert("t", document(f"item-{number}-{step}", "text"), "hash")
            store.hashes("t")
            store.documents("t")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(work, range(8)))
    assert len(store.hashes("t")) == 8 * 40


async def test_concurrent_runs_copy_a_users_portfolio_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copies: list[str] = []

    def provision(template: str, tenant: str, _: Any) -> None:
        copies.append(tenant)

    monkeypatch.setattr(tasks, "provision_tenant", provision)
    services = WorkerServices(None, None, lambda _: None, None, lambda: None)  # type: ignore[arg-type]
    user = {"id": "u1", "persona": "ai_ml_engineer"}
    await asyncio.gather(*(services.ensure_portfolio(user) for _ in range(5)))
    assert copies == ["u1"]
