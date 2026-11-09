"""Long-term memory in a LangGraph store, scoped by user.

Ledgers hold the raw observations a promotion rule counts. The style and outcome namespaces hold
only what a rule promoted, and those are the only namespaces any agent reads.
"""

from typing import Any

from langgraph.store.base import BaseStore

from agent.config import MemoryConfig
from agent.memory.promotion_rules import (
    extract_style_signals,
    promote_outcomes,
    should_promote_style,
    style_rule_text,
)

_STYLE = "style"
_STYLE_LEDGER = "style_ledger"
_OUTCOME = "outcome"
_OUTCOME_LEDGER = "outcome_ledger"
_LIMIT = 10_000


class LongTermMemory:
    def __init__(self, store: BaseStore, config: MemoryConfig) -> None:
        self._store = store
        self._config = config

    async def _values(self, user_id: str, name: str) -> list[dict[str, Any]]:
        items = await self._store.asearch((user_id, name), limit=_LIMIT)
        return [item.value for item in items]

    async def record_edits(
        self,
        user_id: str,
        source: str,
        edits: list[dict[str, Any]],
    ) -> None:
        """Count one draft's human edits, then apply the promotion rule.

        A draft counts once however many times it was edited or replayed.
        """
        observed: set[str] = set()
        opportunities: set[str] = set()
        for edit in edits:
            signals = extract_style_signals(edit["before"], edit["after"])
            observed |= signals.observed
            opportunities |= signals.opportunities

        for pattern in opportunities:
            namespace = (user_id, _STYLE_LEDGER)
            existing = await self._store.aget(namespace, pattern)
            entry = (
                dict(existing.value)
                if existing
                else {"pattern": pattern, "seen": [], "opportunities": []}
            )
            if source in entry["opportunities"]:
                continue
            entry["opportunities"] = [*entry["opportunities"], source]
            if pattern in observed:
                entry["seen"] = [*entry["seen"], source]
            await self._store.aput(namespace, pattern, entry)
            await self._apply_style_rule(user_id, entry)

    async def _apply_style_rule(self, user_id: str, entry: dict[str, Any]) -> None:
        pattern = entry["pattern"]
        seen, opportunities = len(entry["seen"]), len(entry["opportunities"])
        namespace = (user_id, _STYLE)
        if should_promote_style(seen, opportunities, self._config):
            await self._store.aput(
                namespace,
                pattern,
                {
                    "pattern": pattern,
                    "rule": style_rule_text(pattern),
                    "seen": seen,
                    "opportunities": opportunities,
                },
            )
        elif await self._store.aget(namespace, pattern):
            await self._store.adelete(namespace, pattern)

    async def style_items(self, user_id: str) -> list[dict[str, Any]]:
        return await self._values(user_id, _STYLE)

    async def style_rules(self, user_id: str) -> list[str]:
        return sorted(item["rule"] for item in await self.style_items(user_id))

    async def delete_style_rule(self, user_id: str, pattern: str) -> None:
        """Forget a rule and its counts, so it must be earned again from new edits."""
        await self._store.adelete((user_id, _STYLE), pattern)
        await self._store.adelete((user_id, _STYLE_LEDGER), pattern)

    async def record_submission(
        self,
        user_id: str,
        proposal_id: str,
        category: str,
        evidence_ids: list[str],
    ) -> None:
        namespace = (user_id, _OUTCOME_LEDGER)
        if await self._store.aget(namespace, proposal_id) is None:
            projects = sorted({item.rsplit(":", 1)[-1] for item in evidence_ids})
            await self._store.aput(
                namespace,
                proposal_id,
                {"category": category, "projects": projects, "status": "submitted"},
            )

    async def record_outcome(self, user_id: str, proposal_id: str, status: str) -> None:
        """Record a status reported by the marketplace, then apply the promotion rule."""
        namespace = (user_id, _OUTCOME_LEDGER)
        existing = await self._store.aget(namespace, proposal_id)
        if existing is None:
            return
        await self._store.aput(namespace, proposal_id, {**existing.value, "status": status})
        await self._refresh_outcomes(user_id)

    async def _refresh_outcomes(self, user_id: str) -> None:
        namespace = (user_id, _OUTCOME)
        promoted = promote_outcomes(await self._values(user_id, _OUTCOME_LEDGER), self._config)
        current = {item.key for item in await self._store.asearch(namespace, limit=_LIMIT)}
        for key in current - set(promoted):
            await self._store.adelete(namespace, key)
        for key, value in promoted.items():
            await self._store.aput(namespace, key, value)

    async def outcome_items(self, user_id: str) -> list[dict[str, Any]]:
        return await self._values(user_id, _OUTCOME)

    async def outcome_insights(self, user_id: str, category: str) -> list[str]:
        items = await self.outcome_items(user_id)
        return sorted(item["insight"] for item in items if item["category"] == category)

    async def delete_outcome_insight(self, user_id: str, key: str) -> None:
        await self._store.adelete((user_id, _OUTCOME), key)
