"""Portfolio tools. Read-only, and always scoped to the caller's tenant."""

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, TypeVar

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from agent.handoffs import EvidenceItem
from mcp_servers.tool_descriptions import TOOL_DESCRIPTIONS
from rag.index.corpus import SkillSummary
from rag.ingest.loaders import PortfolioDocument
from rag.types import EvidenceFilters, RagComponents, Retriever

T = TypeVar("T")
TenantResolver = Callable[[Context[Any, Any, Any]], str]

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
ItemId = Annotated[str, Field(min_length=1, max_length=200)]


class EvidenceList(BaseModel):
    items: list[EvidenceItem]


class SkillList(BaseModel):
    items: list[SkillSummary]


class CaseStudy(BaseModel):
    id: str
    title: str
    content: str
    source: str
    project: str | None
    skills: list[str]
    date: str | None
    client_industry: str | None


@dataclass
class PortfolioBackend:
    """Runs blocking retrieval off the event loop, one call at a time."""

    components: RagComponents
    retriever: Retriever
    lock: threading.Lock

    async def run(self, function: Callable[[], T]) -> T:
        def locked() -> T:
            with self.lock:
                return function()

        return await asyncio.to_thread(locked)

    async def item(self, tenant: str, prefix: str, identifier: str) -> PortfolioDocument:
        """Fetch by full id or short id. Another tenant's id is simply not found."""
        full_id = identifier if ":" in identifier else f"{prefix}:{tenant}:{identifier}"
        document = await self.run(lambda: self.components.corpus.get(tenant, full_id))
        if document is None or document.type != _TYPE_BY_PREFIX[prefix]:
            raise ToolError(f"not_found: '{identifier}' is not in this portfolio")
        return document


_TYPE_BY_PREFIX = {"case": "case_study", "proposal": "proposal"}


def _case_study(document: PortfolioDocument) -> CaseStudy:
    return CaseStudy(
        id=document.item_id,
        title=document.title,
        content=document.text,
        source=document.source,
        project=document.project,
        skills=document.skills,
        date=document.date,
        client_industry=document.client_industry,
    )


def register_tools(mcp: FastMCP, backend: PortfolioBackend, tenant_of: TenantResolver) -> None:
    @mcp.tool(annotations=READ_ONLY, description=TOOL_DESCRIPTIONS["search_evidence"])
    async def search_evidence(
        requirement: Annotated[str, Field(min_length=1, max_length=4000)],
        ctx: Context[Any, Any, Any],
        k: Annotated[int, Field(ge=1, le=20)] = 5,
        filters: EvidenceFilters | None = None,
    ) -> EvidenceList:
        """Find portfolio evidence for a requirement. Returns type, source, date and outcome."""
        tenant = tenant_of(ctx)
        items = await backend.run(
            lambda: backend.retriever.retrieve(requirement, tenant, k, filters)
        )
        return EvidenceList(items=items)

    @mcp.tool(annotations=READ_ONLY, description=TOOL_DESCRIPTIONS["get_case_study"])
    async def get_case_study(id: ItemId, ctx: Context[Any, Any, Any]) -> CaseStudy:
        """Get the full text of one case study by its id."""
        return _case_study(await backend.item(tenant_of(ctx), "case", id))

    @mcp.tool(annotations=READ_ONLY, description=TOOL_DESCRIPTIONS["list_skills"])
    async def list_skills(ctx: Context[Any, Any, Any]) -> SkillList:
        """List every skill the portfolio can evidence, with how many items show it."""
        tenant = tenant_of(ctx)
        return SkillList(items=await backend.run(lambda: backend.components.corpus.skills(tenant)))
