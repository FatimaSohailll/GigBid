"""Read-only check of the live Upwork adapter for the users who connected Upwork in GigBid.

  uv run --locked --env-file .env python -m scripts.upwork_live_check [query] [--shapes]

It decrypts the user's token from the vault, then, through the same adapter the Marketplace server
uses: finds the freelancer account, searches jobs (two at most) and lists message rooms. It never
calls a write tool. Output is a summary; nothing is saved.
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from agent.config import get_config
from agent.upwork_oauth import PROVIDER, OAuthSettings
from apps.api.auth.security import TokenVault
from apps.api.db import Repository, open_pool
from mcp_servers.marketplace.adapters.gigsim import PerUserRateLimiter
from mcp_servers.marketplace.adapters.upwork_live import (
    McpUpworkTools,
    UpworkDraftStore,
    UpworkLiveAdapter,
)
from mcp_servers.marketplace.upwork_tokens import VaultTokens


def shape(value: Any, depth: int = 0) -> Any:
    """Field names and types only, never values."""
    if depth > 6:
        return "..."
    if isinstance(value, dict):
        return {key: shape(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [shape(value[0], depth + 1), f"({len(value)} items)"] if value else []
    return type(value).__name__


async def show_shapes(tools: McpUpworkTools, job_ids: list[str]) -> None:
    accounts = (await tools.call("list_accounts", {}))["accounts"]
    org = next(item["org_uid"] for item in accounts if item.get("role") == "TALENT")
    for job_id in job_ids:
        detail = await tools.call(
            "find_jobs", {"action": "get", "org_uid": org, "params": {"id": job_id}}
        )
        terms = detail["data"]["marketplaceJobPosting"].get("contractTerms", {})
        print(f"  {job_id} contractTerms: {json.dumps(shape(terms))}")
        print(f"  {job_id} connects_balance: {detail.get('connects_balance')!r}")
    rooms = await tools.call(
        "get_messages", {"action": "list_rooms", "org_uid": org, "params": {"limit": 5}}
    )
    print(f"  list_rooms: {json.dumps(shape(rooms))[:800]}")


async def check(query: str, shapes: bool) -> None:
    settings = OAuthSettings.from_env(os.environ)
    if settings is None:
        raise SystemExit("Upwork sign-in is not configured in .env")
    pool = open_pool(os.environ["DATABASE_URL"])
    await pool.open()
    try:
        repository = Repository(pool)
        vault = TokenVault(os.environ["TOKEN_ENCRYPTION_KEY"], repository)
        users = await repository._all(
            "select user_id from oauth_tokens where provider=%s", (PROVIDER,)
        )
        if not users:
            raise SystemExit("No GigBid user has connected Upwork yet.")

        async def get_vault() -> TokenVault:
            return vault

        tokens = VaultTokens(get_vault, settings)
        config = get_config().marketplace
        limiter = PerUserRateLimiter(config.max_requests_per_minute, 0)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
            drafts = UpworkDraftStore(Path(scratch) / "drafts.sqlite3")
            for number, row in enumerate(users, start=1):
                tools = McpUpworkTools(
                    settings.mcp_url,
                    tokens.for_user(row["user_id"]),
                    config.request_timeout_seconds,
                )
                port = UpworkLiveAdapter(tools, row["user_id"], drafts, limiter)
                if await vault.load(row["user_id"], PROVIDER) is None:
                    print(f"User {number}: skipped, stored under another key (old test data)")
                    continue
                print(f"User {number}: token decrypted, connecting to Upwork as GigBid")
                page = await port.search_jobs(query, [], 2, None)
                print(f"  search_jobs('{query}'): {len(page.items)} jobs")
                for job in page.items:
                    print(
                        f"    {job.job_id} | {job.title[:60]} | {job.category} | "
                        f"budget {job.budget.min:g}-{job.budget.max:g} | "
                        f"{job.connects_cost} Connects | {len(job.description)} chars"
                    )
                balance = await port.get_connects_balance()
                print(f"  get_connects_balance: {balance.balance}")
                rooms = await port.list_conversations()
                print(f"  list_conversations: {len(rooms)} rooms")
                if shapes:
                    await show_shapes(tools, [job.job_id for job in page.items])
            drafts.close()
    finally:
        await pool.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    words = [item for item in sys.argv[1:] if item != "--shapes"]
    asyncio.run(check(words[0] if words else "python", "--shapes" in sys.argv))
