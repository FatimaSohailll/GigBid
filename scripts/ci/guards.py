"""Repository rules that CI must enforce (assignment, "Rules your CI must enforce").

Usage: python scripts/ci/guards.py [secrets|gigsim|prompts|numbers|all|record-gigsim]
"""

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GIGSIM_DIR = "gigsim"
GIGSIM_MANIFEST = "scripts/ci/gigsim.manifest.json"
PROMPT_DIR = "agent/prompts"
PROMPT_FILE = re.compile(r"^[a-z0-9_]+\.v[0-9]+\.md$")
PROMPT_SCAN_DIRS = ("agent", "apps/api", "rag", "mcp_servers", "worker", "evals/runners")
IGNORED_PARTS = {"__pycache__", ".venv", ".pytest_cache", "node_modules"}

SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    "API key (sk-)": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "Groq key": re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"),
    "LangSmith key": re.compile(r"\blsv2_[a-z]{2}_[A-Za-z0-9_]{20,}\b"),
    "Langfuse key": re.compile(r"\bpk-lf-[A-Za-z0-9-]{20,}"),
    "Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
}
SECRET_VARIABLE = re.compile(r"(SECRET|TOKEN|PASSWORD|API_KEY|ENCRYPTION_KEY)")
PLACEHOLDER_VALUE = re.compile(r"^(?:replace_me(?:=[A-Za-z0-9_]+)?)?$")

PROMPT_CALLS = {
    "SystemMessage",
    "HumanMessage",
    "AIMessage",
    "PromptTemplate",
    "ChatPromptTemplate",
    "from_template",
    "from_messages",
}
PROMPT_VARIABLE = re.compile(r"prompt|instruction", re.IGNORECASE)
PERSONA_TEXT = re.compile(r"\byou are an? \w+", re.IGNORECASE)
MIN_PROMPT_LENGTH = 40

RESULTS_DIR = "evals/results"
REPORT_FILES = ("README.md", "docs/REPORT.md")
RESULT_BLOCK = re.compile(r"<!-- results:start -->(.*?)<!-- results:end -->", re.DOTALL)
NUMBER = re.compile(r"(?<![\w.@-])\d+(?:\.\d+)?(?![\w.])")


@dataclass(frozen=True)
class Violation:
    path: str
    message: str
    line: int | None = None

    def __str__(self) -> str:
        location = f"{self.path}:{self.line}" if self.line is not None else self.path
        return f"{location}: {self.message}"


def repository_files(root: Path) -> list[str]:
    """Tracked files plus new files that are not ignored, so problems appear before a commit."""
    output = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    return [item for item in output.decode("utf-8").split("\0") if item]


# ---- No secrets in the repo ----------------------------------------------------------------


def _is_env_file(name: str) -> bool:
    return (name == ".env" or name.startswith(".env.")) and name != ".env.example"


def _env_example_violations(path: str, text: str) -> Iterator[Violation]:
    for number, line in enumerate(text.splitlines(), start=1):
        name, separator, value = line.partition("=")
        if line.startswith("#") or not separator:
            continue
        if SECRET_VARIABLE.search(name) and not PLACEHOLDER_VALUE.match(value.strip()):
            yield Violation(path, f"{name} must be empty or replace_me", number)


def check_secrets(root: Path, files: Iterable[str]) -> list[Violation]:
    violations: list[Violation] = []
    for relative in files:
        if _is_env_file(Path(relative).name):
            violations.append(Violation(relative, "environment files must not be committed"))
            continue
        path = root / relative
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if Path(relative).name == ".env.example":
            violations.extend(_env_example_violations(relative, text))
        for label, pattern in SECRET_PATTERNS.items():
            for match in pattern.finditer(text):
                # The matched text is never printed, so a CI log cannot leak the secret.
                line = text.count("\n", 0, match.start()) + 1
                violations.append(Violation(relative, f"possible {label}", line))
    return violations


# ---- Nothing under gigsim/ changed ---------------------------------------------------------


def _gigsim_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted((root / GIGSIM_DIR).rglob("*")):
        if not path.is_file() or IGNORED_PARTS & set(path.parts) or path.suffix == ".pyc":
            continue
        # Normalised so Windows checkouts and Linux CI produce the same hash.
        content = path.read_bytes().replace(b"\r\n", b"\n")
        hashes[path.relative_to(root).as_posix()] = hashlib.sha256(content).hexdigest()
    return hashes


def check_gigsim(root: Path) -> list[Violation]:
    """Compare gigsim/ with the manifest recorded when the provided bundle was imported."""
    manifest_path = root / GIGSIM_MANIFEST
    present = (root / GIGSIM_DIR).is_dir()
    if not present and not manifest_path.exists():
        print("gigsim: the provided GigSim is not in this repository; nothing to compare.")
        return []
    if not present:
        return [Violation(GIGSIM_DIR, "directory was removed but a manifest exists")]
    if not manifest_path.exists():
        return [Violation(GIGSIM_MANIFEST, "missing; run: guards.py record-gigsim")]

    expected: dict[str, str] = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = _gigsim_hashes(root)
    violations = [
        Violation(name, "added to gigsim/") for name in sorted(set(actual) - set(expected))
    ]
    violations += [
        Violation(name, "removed from gigsim/") for name in sorted(set(expected) - set(actual))
    ]
    violations += [
        Violation(name, "modified; gigsim/ must not change")
        for name in sorted(set(actual) & set(expected))
        if actual[name] != expected[name]
    ]
    return violations


def record_gigsim(root: Path) -> None:
    """Run once, immediately after importing the unmodified provided bundle."""
    manifest = json.dumps(_gigsim_hashes(root), indent=2, sort_keys=True)
    (root / GIGSIM_MANIFEST).write_text(manifest + "\n", encoding="utf-8")


# ---- Every prompt lives in agent/prompts/ ----------------------------------------------------


def _docstring_nodes(tree: ast.AST) -> set[ast.AST]:
    found: set[ast.AST] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(first.value)
    return found


def _is_text_literal(node: ast.AST | None) -> bool:
    if isinstance(node, ast.JoinedStr):
        return True
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return node.func.id if isinstance(node.func, ast.Name) else ""


def _chat_role_dict(node: ast.Dict) -> bool:
    fields = {
        key.value: value
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    role = fields.get("role")
    return (
        isinstance(role, ast.Constant)
        and role.value in {"system", "user", "assistant"}
        and _is_text_literal(fields.get("content"))
    )


def _assigned_prompt(node: ast.Assign | ast.AnnAssign) -> bool:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    named = any(isinstance(t, ast.Name) and PROMPT_VARIABLE.search(t.id) for t in targets)
    value = node.value
    if isinstance(value, ast.JoinedStr):
        return named
    return (
        named
        and isinstance(value, ast.Constant)
        and isinstance(value.value, str)
        and len(value.value) >= MIN_PROMPT_LENGTH
    )


def _python_prompt_violations(relative: str, source: str) -> Iterator[Violation]:
    tree = ast.parse(source, filename=relative)
    docstrings = _docstring_nodes(tree)
    message = f"inline prompt; move it to {PROMPT_DIR}/"
    for node in ast.walk(tree):
        inline = False
        if isinstance(node, ast.Call) and _call_name(node) in PROMPT_CALLS:
            arguments = [*node.args, *(keyword.value for keyword in node.keywords)]
            inline = any(_is_text_literal(argument) for argument in arguments)
        elif isinstance(node, ast.Dict):
            inline = _chat_role_dict(node)
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            inline = _assigned_prompt(node)
        elif isinstance(node, ast.Constant) and node not in docstrings:
            inline = (
                isinstance(node.value, str)
                and len(node.value) >= MIN_PROMPT_LENGTH
                and PERSONA_TEXT.search(node.value) is not None
            )
        if inline:
            yield Violation(relative, message, getattr(node, "lineno", None))


def check_prompts(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for directory in PROMPT_SCAN_DIRS:
        for path in sorted((root / directory).rglob("*.py")):
            if IGNORED_PARTS & set(path.parts):
                continue
            relative = path.relative_to(root).as_posix()
            try:
                violations.extend(
                    _python_prompt_violations(relative, path.read_text(encoding="utf-8"))
                )
            except SyntaxError as error:
                violations.append(Violation(relative, "cannot be parsed", error.lineno))

    prompt_root = root / PROMPT_DIR
    if prompt_root.is_dir():
        for path in sorted(prompt_root.iterdir()):
            if path.name != "README.md" and not PROMPT_FILE.match(path.name):
                relative = path.relative_to(root).as_posix()
                violations.append(Violation(relative, "must be named <node>.v<number>.md"))
    return violations


# ---- Every reported number traces to evals/results/ ------------------------------------------


def _result_numbers(root: Path) -> set[str]:
    numbers: set[str] = set()
    for path in sorted((root / RESULTS_DIR).glob("*")):
        if path.suffix in {".json", ".csv"}:
            numbers |= set(NUMBER.findall(path.read_text(encoding="utf-8")))
    return numbers


def check_numbers(root: Path) -> list[Violation]:
    """Numbers inside the results markers of a report must appear in a results file."""
    violations: list[Violation] = []
    known = _result_numbers(root)
    for relative in REPORT_FILES:
        path = root / relative
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for block in RESULT_BLOCK.finditer(text):
            for match in NUMBER.finditer(block.group(1)):
                if match.group() not in known:
                    line = text.count("\n", 0, block.start(1) + match.start()) + 1
                    message = f"{match.group()} is not in any file under {RESULTS_DIR}/"
                    violations.append(Violation(relative, message, line))
    return violations


# ---- Command line --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "check",
        choices=["secrets", "gigsim", "prompts", "numbers", "all", "record-gigsim"],
    )
    arguments = parser.parse_args(argv)

    if arguments.check == "record-gigsim":
        record_gigsim(REPO_ROOT)
        print(f"Recorded {GIGSIM_MANIFEST}")
        return 0

    violations: list[Violation] = []
    if arguments.check in {"secrets", "all"}:
        violations += check_secrets(REPO_ROOT, repository_files(REPO_ROOT))
    if arguments.check in {"gigsim", "all"}:
        violations += check_gigsim(REPO_ROOT)
    if arguments.check in {"prompts", "all"}:
        violations += check_prompts(REPO_ROOT)
    if arguments.check in {"numbers", "all"}:
        violations += check_numbers(REPO_ROOT)

    for violation in violations:
        print(violation)
    print(f"guards: {len(violations)} violation(s)")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
