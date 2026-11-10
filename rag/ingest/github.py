"""Read a repository's README and code through the official GitHub MCP server.

Only the read-only tools on the github_ingest allowlist are used. Repository text is untrusted data
and reaches the agents delimited, like any other evidence.
"""

import json
from pathlib import PurePosixPath
from typing import Any, Protocol

from agent.config import RagConfig
from rag.ingest.loaders import PortfolioDocument

_AGENT = "github_ingest"
_TOOL = "get_file_contents"
_SKIPPED_DIRECTORIES = {".git", ".github", "node_modules", "vendor", "dist", "build", "__pycache__"}
_LANGUAGE_BY_SUFFIX = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".sql": "SQL",
}


class TextGateway(Protocol):
    async def call_text(self, agent: str, tool: str, arguments: dict[str, Any]) -> list[str]: ...


def _is_readme(name: str) -> bool:
    return name.lower().startswith("readme")


def _wanted(entry: dict[str, Any], settings: RagConfig) -> bool:
    if _is_readme(entry["name"]):
        return True  # large READMEs are common and are the most useful file; chunking handles them
    suffix = PurePosixPath(entry["name"]).suffix.lower()
    return (
        suffix in settings.github_code_extensions
        and entry.get("size", 0) <= settings.github_max_file_bytes
    )


async def _listing(gateway: TextGateway, owner: str, repo: str, path: str) -> list[dict[str, Any]]:
    blocks = await gateway.call_text(_AGENT, _TOOL, {"owner": owner, "repo": repo, "path": path})
    parsed = json.loads(blocks[-1])
    return parsed if isinstance(parsed, list) else []


async def _candidates(
    gateway: TextGateway,
    owner: str,
    repo: str,
    settings: RagConfig,
) -> list[dict[str, Any]]:
    """Walk the tree breadth first, keeping READMEs ahead of code and shallow before deep."""
    found: list[dict[str, Any]] = []
    level = ["/"]
    for _ in range(settings.github_max_depth):
        following: list[str] = []
        for directory in level:
            for entry in await _listing(gateway, owner, repo, directory):
                if entry["type"] == "dir" and entry["name"] not in _SKIPPED_DIRECTORIES:
                    following.append(entry["path"] + "/")
                elif entry["type"] == "file" and _wanted(entry, settings):
                    found.append(entry)
        level = following
    return sorted(found, key=lambda e: (not _is_readme(e["name"]), e["path"].count("/"), e["path"]))


async def read_repository(
    gateway: TextGateway,
    owner: str,
    repo: str,
    settings: RagConfig,
) -> list[PortfolioDocument]:
    documents = []
    for entry in (await _candidates(gateway, owner, repo, settings))[: settings.github_max_files]:
        path = entry["path"]
        blocks = await gateway.call_text(
            _AGENT, _TOOL, {"owner": owner, "repo": repo, "path": path}
        )
        kind = "readme" if _is_readme(entry["name"]) else "code"
        language = _LANGUAGE_BY_SUFFIX.get(PurePosixPath(path).suffix.lower())
        documents.append(
            PortfolioDocument(
                item_id=f"{kind}:github:{owner}/{repo}:{path}",
                type=kind,
                title=f"{repo}/{path}",
                text=blocks[-1],
                source=f"github.com/{owner}/{repo}/{path}",
                project=repo,
                skills=[language] if language else [],
            )
        )
    return documents
