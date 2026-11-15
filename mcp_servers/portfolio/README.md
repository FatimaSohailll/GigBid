# GigBid Portfolio MCP server

A read-only MCP server that lets an AI assistant prove what you have done. Point it at your
portfolio, and Claude Desktop, Cursor or any MCP host can search it for evidence of a requirement.

## What it offers

| Kind | Name | Use |
|---|---|---|
| Tool | `search_evidence(requirement, k, filters)` | Evidence items with type, source, date and outcome |
| Tool | `get_case_study(id)` | Full text of one case study |
| Tool | `list_skills()` | Every skill the portfolio can prove, with counts |
| Resource | `portfolio://case/{id}` | A case study |
| Resource | `portfolio://proposal/{id}` | A past proposal with its outcome |
| Prompt | Answer a screening question from my portfolio | Drafts an answer that cites evidence |
| Prompt | Prove a job requirement from my portfolio | Finds and summarises proof |

Everything runs on your machine. The embedding model, `nomic-ai/nomic-embed-text-v1.5` (quantised
ONNX), downloads once on first start. Retrieval uses dense and BM25 search fused together, with
outcome-aware ranking.

## Install with Docker

The published image, from the GitHub Container Registry:

```bash
docker pull ghcr.io/fatimasohailll/gigbid-portfolio-mcp:latest
```

Run it with your portfolio folder mounted read-only and a volume for the index:

```bash
docker run -i --rm -v /path/to/my-portfolio:/portfolio:ro -v gigbid-portfolio-data:/data ghcr.io/fatimasohailll/gigbid-portfolio-mcp:latest
```

In the Claude Desktop config below, use `ghcr.io/fatimasohailll/gigbid-portfolio-mcp:latest` as
the image name.

### Or build it yourself

From a clone of this repository:

```bash
docker build -f infra/docker/portfolio.Dockerfile -t gigbid-portfolio-mcp .
```

Run it with your portfolio folder mounted read-only and a volume for the index:

```bash
docker run -i --rm -v /path/to/my-portfolio:/portfolio:ro -v gigbid-portfolio-data:/data gigbid-portfolio-mcp
```

The first start indexes everything; later starts re-embed only what changed.

### Claude Desktop

Add this to `claude_desktop_config.json`, replacing the folder path:

```json
{
  "mcpServers": {
    "portfolio": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-v", "/path/to/my-portfolio:/portfolio:ro",
        "-v", "gigbid-portfolio-data:/data",
        "gigbid-portfolio-mcp"
      ]
    }
  }
}
```

## Portfolio folder format

Put an `index.json` at the top of the folder, listing each item:

```json
[
  {"item_id": "cv:me", "type": "cv", "path": "cv.pdf"},
  {
    "item_id": "case:me:churn-model",
    "type": "case_study",
    "path": "case_studies/churn-model.md",
    "project": "churn-model",
    "skills": ["Python", "XGBoost"],
    "date": "2025",
    "client_industry": "telecom"
  }
]
```

- `type`: one of `cv`, `case_study`, `readme`, `code`, `review` or `proposal`.
- `path`: relative to the folder. Supported files are PDF for the CV, Markdown or text for case
  studies, READMEs and code, and JSON lists for reviews and proposals.
- Proposals may carry an `outcome`: `won`, `replied` or `ignored`.
- `item_id` must start with the type's prefix: `cv`, `case`, `readme`, `code`, `review` or
  `proposal`.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `PORTFOLIO_DIR` | `/portfolio` | Folder holding `index.json` |
| `PORTFOLIO_TENANT` | `me` | Name the index is stored under |
| `RAG_DATA_DIR` | `/data/index` | Where the index is kept |

Inside GigBid the same server runs over HTTP with per-user bearer tokens; see `server.py`.
