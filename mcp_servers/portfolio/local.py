"""Run the Portfolio server over stdio for one person's own portfolio (Claude Desktop, Cursor).

Indexes the folder at PORTFOLIO_DIR on start, then serves it read-only. Only changed items are
embedded again on later starts. Settings: PORTFOLIO_DIR (default /portfolio), PORTFOLIO_TENANT
(default "me"), RAG_DATA_DIR (where the index is kept).
"""

import os
import sys
from pathlib import Path

from mcp_servers.portfolio.server import build_server
from rag.ingest.loaders import load_portfolio
from rag.ingest.pipeline import IngestReport, ingest_documents
from rag.retriever import open_components
from rag.types import RagComponents


def index_portfolio(folder: Path, tenant: str, components: RagComponents) -> IngestReport:
    if not (folder / "index.json").is_file():
        raise SystemExit(f"{folder} has no index.json; see mcp_servers/portfolio/README.md")
    return ingest_documents(load_portfolio(folder), tenant, components, remove_missing=True)


def main() -> None:
    folder = Path(os.environ.get("PORTFOLIO_DIR", "/portfolio"))
    tenant = os.environ.get("PORTFOLIO_TENANT", "me")
    components = open_components()
    report = index_portfolio(folder, tenant, components)
    # stdout carries the MCP protocol, so progress goes to stderr.
    print(f"Indexed {folder}: {report}", file=sys.stderr, flush=True)
    build_server(components, lambda _: tenant).run(transport="stdio")


if __name__ == "__main__":
    main()
