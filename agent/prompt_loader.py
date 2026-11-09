"""Load versioned prompts from agent/prompts/<node>.v<number>.md."""

import re
from pathlib import Path

PROMPT_DIR = Path(__file__).with_name("prompts")


def load_prompt(node: str, version: int | None = None, directory: Path = PROMPT_DIR) -> str:
    """Return a prompt. Without a version, the highest version available is used."""
    if version is None:
        pattern = re.compile(rf"^{re.escape(node)}\.v(\d+)\.md$")
        versions = [
            int(match.group(1))
            for path in directory.iterdir()
            if (match := pattern.match(path.name))
        ]
        if not versions:
            raise FileNotFoundError(f"No prompt found for node '{node}' in {directory}")
        version = max(versions)
    path = directory / f"{node}.v{version}.md"
    if not path.is_file():
        raise FileNotFoundError(f"No prompt found at {path}")
    return path.read_text(encoding="utf-8")
