"""Prepare a fresh stack for a demo or a reviewer: python -m scripts.seed_demo

1. Generate the DevSim data (jobs, clients, persona portfolios) unless it already exists.
2. Index the persona portfolios, so the Portfolio server has evidence to return.
3. Create the reviewer login, preloaded with a persona, from DEMO_EMAIL and DEMO_PASSWORD.

Safe to run again: each step skips what is already done.
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

DATA = Path(os.environ.get("DEVSIM_DATA_DIR", "data/processed/devsim"))


def generate_data() -> None:
    if (DATA / "MANIFEST.json").exists():
        print(f"DevSim data already in {DATA}", flush=True)
        return
    print(f"Generating DevSim data in {DATA}", flush=True)
    subprocess.run([sys.executable, "-m", "devsim.datagen", "--out", str(DATA)], check=True)


def index_portfolios() -> None:
    print("Indexing persona portfolios (only changed items are embedded)", flush=True)
    subprocess.run(
        [sys.executable, "-m", "rag.ingest.pipeline", "--personas", str(DATA / "personas")],
        check=True,
    )


async def ensure_database(url: str) -> None:
    """Create the stack's own database on first start; Postgres creates only POSTGRES_DB itself."""
    import psycopg
    from psycopg import sql

    url = url.replace("postgresql+psycopg://", "postgresql://", 1)
    name = url.rsplit("/", 1)[1].split("?", 1)[0]
    server = url.rsplit("/", 1)[0] + "/postgres"
    async with await psycopg.AsyncConnection.connect(server, autocommit=True) as connection:
        found = await connection.execute("select 1 from pg_database where datname=%s", (name,))
        if await found.fetchone() is None:
            await connection.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
            print(f"Created database {name}", flush=True)


async def reviewer_login() -> None:
    from apps.api.auth.security import hash_password
    from apps.api.db import Repository, open_pool

    email, password = os.environ.get("DEMO_EMAIL"), os.environ.get("DEMO_PASSWORD")
    if not email or not password:
        print("DEMO_EMAIL and DEMO_PASSWORD are not set; no reviewer login created.", flush=True)
        return
    pool = open_pool(os.environ["DATABASE_URL"])
    await pool.open()
    try:
        repository = Repository(pool)
        await repository.setup()
        if await repository.user_by_email(email):
            print(f"Reviewer login {email} already exists.", flush=True)
            return
        persona = os.environ.get("DEMO_PERSONA", "ai_ml_engineer")
        await repository.create_user(email, hash_password(password), persona)
        print(f"Created reviewer login {email} with persona {persona}.", flush=True)
    finally:
        await pool.close()


def main() -> None:
    generate_data()
    index_portfolios()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(ensure_database(os.environ["DATABASE_URL"]))
    asyncio.run(reviewer_login())


if __name__ == "__main__":
    main()
