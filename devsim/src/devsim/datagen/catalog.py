"""Typed access to the persona catalog."""

import re
from dataclasses import dataclass
from importlib import resources
from typing import Any

import yaml


def mentions(term: str, text: str) -> bool:
    """Case-insensitive whole-term match, so that 'RAG' does not match 'storage'."""
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.IGNORECASE) is not None


@dataclass(frozen=True)
class Project:
    slug: str
    codename: str
    title: str
    industry: str
    year: int
    explicit: tuple[str, ...]
    implied: tuple[str, ...]
    hidden: tuple[str, ...]
    problem: str
    approach: str
    result: str
    readme: str
    cv: str

    @property
    def all_skills(self) -> tuple[str, ...]:
        return self.explicit + self.implied + self.hidden


@dataclass(frozen=True)
class Persona:
    persona_id: str
    display_name: str
    headline: str
    category: str
    id_prefix: str
    language: str
    hourly_rate: int
    floor_usd: int
    years: int
    projects: tuple[Project, ...]

    @property
    def skills(self) -> set[str]:
        return {skill.lower() for project in self.projects for skill in project.all_skills}

    def evidence_for(self, skill: str) -> list[str]:
        """Item ids that prove a skill, according to where the corpus actually shows it."""
        wanted = skill.lower()
        evidence: list[str] = []
        for project in self.projects:
            kind = f"{self.persona_id}:{project.slug}"
            if wanted in {value.lower() for value in project.explicit}:
                evidence += [f"case:{kind}", f"readme:{kind}"]
            elif wanted in {value.lower() for value in project.implied}:
                evidence.append(f"case:{kind}")
            elif wanted in {value.lower() for value in project.hidden}:
                evidence.append(f"code:{kind}")
        return evidence


def _project(raw: dict[str, Any]) -> Project:
    values = dict(raw)
    for key in ("explicit", "implied", "hidden"):
        values[key] = tuple(str(item) for item in values[key])
    return Project(**values)


def load_catalog() -> tuple[Persona, ...]:
    text = resources.files("devsim.datagen").joinpath("catalog.yaml").read_text("utf-8")
    personas = []
    for persona_id, raw in yaml.safe_load(text).items():
        values = dict(raw)
        projects = tuple(_project(item) for item in values.pop("projects"))
        personas.append(Persona(persona_id=persona_id, projects=projects, **values))
    return tuple(personas)
