"""Generate one persona's portfolio corpus.

The corpus is built to defeat naive RAG, as the assignment describes: each project is described
in three different ways under different names, some skills are only implied or only visible in
code, prose and code are mixed, and proposal outcomes depend on more than wording.
"""

import json
import random
from pathlib import Path
from typing import Any

from devsim.datagen.catalog import Persona, Project
from devsim.datagen.pdf import text_to_pdf

PROPOSALS_PER_PERSONA = 150
_OUTCOME_ODDS = {  # (won, replied) probabilities by proposal style
    "specific": (0.22, 0.42),
    "generic": (0.03, 0.12),
}
_GENERIC_OPENINGS = (
    "Hello, I am very interested in your project and I am sure I can do a great job.",
    "Dear client, I read your job post and I am the perfect fit for this work.",
    "Hi there! I am a hardworking professional with a passion for delivering quality.",
)
_REVIEW_OPENERS = (
    "Delivered exactly what we needed.",
    "Great communicator and very reliable.",
    "Would hire again.",
    "Solid work under a tight schedule.",
)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def _module_name(project: Project) -> str:
    return project.slug.replace("-", "_")


def _code(persona: Persona, project: Project) -> tuple[str, str]:
    """Return (relative path, source). Hidden skills appear here and nowhere else."""
    name = _module_name(project)
    libraries = project.hidden
    if persona.language == "python":
        imports = "\n".join(f"import {library.replace('-', '_')}" for library in libraries)
        return f"src/{name}.py", (
            f'"""Entry point for {project.codename}."""\n\n{imports}\n\n\n'
            f"def run_{name}(batch: list[dict]) -> list[dict]:\n"
            f"    client = {libraries[0].replace('-', '_')}.connect()\n"
            f"    return [client.process(row, tracker={libraries[1].replace('-', '_')}) "
            "for row in batch]\n"
        )
    if persona.language == "typescript":
        imports = "\n".join(
            f'import * as lib{index} from "{library}";' for index, library in enumerate(libraries)
        )
        return f"src/{name}.ts", (
            f"// Entry point for {project.codename}.\n{imports}\n\n"
            f"export async function run{name.title().replace('_', '')}"
            "(input: unknown): Promise<unknown> {\n"
            "  const prepared = await lib0.prepare(input);\n"
            "  return lib1.execute(prepared);\n}\n"
        )
    tooling = ", ".join(libraries)
    return f"models/{name}.sql", (
        f"-- Model for {project.codename}. Tooling: {tooling}\n"
        "with source_rows as (\n    select * from raw_events\n),\n\n"
        "ranked as (\n    select *, row_number() over (\n"
        "        partition by entity_id order by updated_at desc\n    ) as version\n"
        "    from source_rows\n)\n\nselect * from ranked where version = 1\n"
    )


def _case_study(persona: Persona, project: Project, rng: random.Random, code: str) -> str:
    front_matter = (
        "---\n"
        f"title: {project.title}\n"
        f"persona: {persona.persona_id}\n"
        f"client_industry: {project.industry}\n"
        f"date: {project.year}-{rng.randint(1, 12):02d}-15\n"
        f"skills: [{', '.join(project.explicit)}]\n"
        "---\n"
    )
    body = (
        f"\n# {project.title}\n\n## The situation\n\n{project.problem}\n\n"
        f"## What I did\n\n{project.approach}\n\n"
        f"Stack: {', '.join(project.explicit)}.\n\n"
        f"## Outcome\n\n{project.result}\n"
    )
    # About a third of case studies mix a short implementation note into the prose. It must
    # not quote the library imports, which stay exclusive to the repository code.
    if rng.random() < 0.34:
        blocked = ("import", "lib", "tooling", *(name.lower() for name in project.hidden))
        kept = [line for line in code.splitlines() if not any(b in line.lower() for b in blocked)]
        body += "\n## Implementation note\n\n```\n" + "\n".join(kept)[:300] + "\n```\n"
    return front_matter + body


def _readme(project: Project) -> str:
    return (
        f"# {project.codename}\n\n{project.readme}\n\n"
        f"## Requirements\n\n{chr(10).join(f'- {skill}' for skill in project.explicit)}\n\n"
        "## Usage\n\nSee the entry point in this repository. Configuration is read from the "
        "environment.\n"
    )


def _cv_text(persona: Persona) -> str:
    skills = sorted({skill for project in persona.projects for skill in project.explicit})
    lines = [
        persona.display_name,
        persona.headline,
        f"{persona.years} years of freelance experience",
        "",
        "SKILLS",
        ", ".join(skills),
        "",
        "SELECTED WORK",
    ]
    lines += [f"- {project.cv}" for project in persona.projects]
    return "\n".join(lines)


def _review(project: Project, rng: random.Random) -> dict[str, Any]:
    return {
        "rating": rng.choice((4.5, 4.8, 5.0)),
        "comment": f"{rng.choice(_REVIEW_OPENERS)} {project.result}",
    }


def _proposal(persona: Persona, index: int, rng: random.Random) -> dict[str, Any]:
    project = rng.choice(persona.projects)
    style = "specific" if rng.random() < 0.45 else "generic"
    won, replied = _OUTCOME_ODDS[style]
    if style == "specific":
        opening = f"Your post describes a problem I solved recently: {project.problem}"
        text = f"{opening} {project.approach} {project.result}"
        evidence = [f"case:{persona.persona_id}:{project.slug}"]
    else:
        # Generic letters still list real skills, so their wording overlaps with job posts
        # even though they rarely get a reply.
        text = (
            f"{rng.choice(_GENERIC_OPENINGS)} I have strong experience with "
            f"{', '.join(project.explicit)} and I always meet deadlines."
        )
        evidence = []
    draw = rng.random()
    outcome = "won" if draw < won else "replied" if draw < won + replied else "ignored"
    return {
        "proposal_id": f"proposal:{persona.persona_id}:{index:03d}",
        "persona": persona.persona_id,
        "job_title": f"{project.title} for a {project.industry} client",
        "job_category": persona.category,
        "client_industry": project.industry,
        "date": f"{rng.randint(2023, 2026)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
        "project": project.slug,
        "skills": list(project.explicit),
        "bid_usd": round(rng.uniform(400, 4000), 2),
        "text": text,
        "evidence": evidence,
        "outcome": outcome,
    }


def write_persona(root: Path, persona: Persona, seed: int) -> None:
    """Write the corpus and an index holding the metadata that ingestion needs."""
    rng = random.Random(f"{seed}:corpus:{persona.persona_id}")
    target = root / persona.persona_id
    index: list[dict[str, Any]] = []

    write_json(
        target / "profile.json",
        {
            "persona": persona.persona_id,
            "display_name": persona.display_name,
            "headline": persona.headline,
            "category": persona.category,
            "hourly_rate_usd": persona.hourly_rate,
            "minimum_project_usd": persona.floor_usd,
            "official_gigsim": False,
        },
    )
    (target / "cv.pdf").parent.mkdir(parents=True, exist_ok=True)
    (target / "cv.pdf").write_bytes(text_to_pdf(_cv_text(persona)))
    index.append({"item_id": f"cv:{persona.persona_id}", "type": "cv", "path": "cv.pdf"})

    reviews = []
    for project in persona.projects:
        base = f"{persona.persona_id}:{project.slug}"
        meta = {
            "project": project.slug,
            "client_industry": project.industry,
            "date": str(project.year),
        }
        code_path, code = _code(persona, project)
        repository = f"repos/{project.codename}"

        write_text(
            target / "case_studies" / f"{project.slug}.md", _case_study(persona, project, rng, code)
        )
        write_text(target / repository / "README.md", _readme(project))
        write_text(target / repository / code_path, code)

        index += [
            {
                "item_id": f"case:{base}",
                "type": "case_study",
                "path": f"case_studies/{project.slug}.md",
                "skills": list(project.explicit),
                **meta,
            },
            {
                "item_id": f"readme:{base}",
                "type": "readme",
                "path": f"{repository}/README.md",
                "skills": list(project.explicit),
                **meta,
            },
            {
                "item_id": f"code:{base}",
                "type": "code",
                "path": f"{repository}/{code_path}",
                "skills": list(project.hidden),
                **meta,
            },
        ]
        review_id = f"review:{base}"
        reviews.append({"item_id": review_id, **meta, **_review(project, rng)})
        index.append({"item_id": review_id, "type": "review", "path": "reviews.json", **meta})

    write_json(target / "reviews.json", reviews)
    proposals = [_proposal(persona, i, rng) for i in range(1, PROPOSALS_PER_PERSONA + 1)]
    write_json(target / "proposals.json", proposals)
    index += [
        {
            "item_id": proposal["proposal_id"],
            "type": "proposal",
            "path": "proposals.json",
            "project": proposal["project"],
            "skills": proposal["skills"],
            "client_industry": proposal["client_industry"],
            "date": proposal["date"],
            "outcome": proposal["outcome"],
        }
        for proposal in proposals
    ]
    write_json(target / "index.json", index)
