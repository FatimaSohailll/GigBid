"""Load portfolio items: PDF CVs, Markdown case studies, READMEs, code, reviews and proposals."""

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader


class PortfolioDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    type: str
    title: str
    text: str
    source: str
    project: str | None = None
    skills: list[str] = Field(default_factory=list)
    date: str | None = None
    client_industry: str | None = None
    outcome: str | None = None


def read_pdf_text(path: Path) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(path).pages)


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Separate YAML front matter from a Markdown body."""
    if not text.startswith("---\n"):
        return {}, text
    header, separator, body = text[4:].partition("\n---\n")
    if not separator:
        return {}, text
    parsed = yaml.safe_load(header)
    return (parsed if isinstance(parsed, dict) else {}), body.lstrip("\n")


def _read_json_items(path: Path, id_field: str) -> dict[str, dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {row[id_field]: row for row in rows}


def load_upload(path: Path, upload_id: str, title: str) -> PortfolioDocument:
    """Read one file the user added through the web interface."""
    if path.suffix.lower() == ".pdf":
        text = read_pdf_text(path)
    else:
        front_matter, text = split_front_matter(path.read_text(encoding="utf-8"))
        title = str(front_matter.get("title", title))
    return PortfolioDocument(
        item_id=f"case:upload:{upload_id}",
        type="case_study",
        title=title,
        text=text,
        source=path.name,
        project=upload_id,
    )


def load_portfolio(root: Path) -> list[PortfolioDocument]:
    """Read every item listed in a persona's index.json."""
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    collections: dict[str, dict[str, dict[str, Any]]] = {}
    documents = []
    for entry in index:
        item_type = entry["type"]
        source = entry["path"]
        path = root / source
        title = entry.get("project") or entry["item_id"]
        if item_type == "cv":
            text = read_pdf_text(path)
            title = "Curriculum vitae"
        elif item_type in {"review", "proposal"}:
            id_field = "item_id" if item_type == "review" else "proposal_id"
            rows = collections.setdefault(source, _read_json_items(path, id_field))
            row = rows[entry["item_id"]]
            text = row["comment"] if item_type == "review" else row["text"]
            title = row.get("job_title", title)
        else:
            raw = path.read_text(encoding="utf-8")
            front_matter, text = split_front_matter(raw)
            title = str(front_matter.get("title", title))
        documents.append(
            PortfolioDocument(
                item_id=entry["item_id"],
                type=item_type,
                title=title,
                text=text,
                source=source,
                project=entry.get("project"),
                skills=entry.get("skills", []),
                date=entry.get("date"),
                client_industry=entry.get("client_industry"),
                outcome=entry.get("outcome"),
            )
        )
    return documents
