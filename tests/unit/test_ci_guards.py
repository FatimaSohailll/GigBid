from pathlib import Path

import pytest

from scripts.ci import guards


def write(root: Path, relative: str, content: str) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return relative


# Assembled at runtime so that this test file does not itself contain secret-shaped text.
FAKE_SECRETS = {
    "private key": "-----BEGIN RSA " + "PRIVATE KEY-----",
    "AWS access key": "AKIA" + "ABCDEFGHIJKLMNOP",
    "GitHub token": "ghp_" + "a" * 36,
    "API key (sk-)": "sk-" + "a1B2" * 6,
    "Groq key": "gsk_" + "a1B2" * 6,
    "LangSmith key": "lsv2_pt_" + "a1B2" * 6,
    "Langfuse key": "pk-lf-" + "a1B2" * 6,
    "Slack token": "xoxb-" + "1234567890",
}


@pytest.mark.parametrize(("label", "secret"), FAKE_SECRETS.items())
def test_secret_shapes_are_detected_without_printing_them(
    tmp_path: Path,
    label: str,
    secret: str,
) -> None:
    relative = write(tmp_path, "config/settings.py", f'first = 1\nkey = "{secret}"\n')
    violations = guards.check_secrets(tmp_path, [relative])
    assert [violation.message for violation in violations] == [f"possible {label}"]
    assert violations[0].line == 2
    assert secret not in str(violations[0])


def test_ordinary_text_is_not_a_secret(tmp_path: Path) -> None:
    relative = write(tmp_path, "notes.py", 'token = "dev-token-ai"\ntask = "sk-short"\n')
    assert guards.check_secrets(tmp_path, [relative]) == []


@pytest.mark.parametrize("name", [".env", ".env.local", "infra/.env.production"])
def test_environment_files_are_rejected(tmp_path: Path, name: str) -> None:
    relative = write(tmp_path, name, "A=1\n")
    assert guards.check_secrets(tmp_path, [relative])[0].message.startswith("environment files")


def test_env_example_rejects_real_values_for_secret_variables(tmp_path: Path) -> None:
    content = "APP_SECRET_KEY=hunter2\nOPENAI_API_KEY=\nAPP_ENV=development\nA_TOKEN=replace_me\n"
    relative = write(tmp_path, ".env.example", content)
    violations = guards.check_secrets(tmp_path, [relative])
    assert [violation.line for violation in violations] == [1]


def test_the_real_env_example_passes() -> None:
    assert guards.check_secrets(guards.REPO_ROOT, [".env.example"]) == []


def test_missing_and_binary_files_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "image.bin").write_bytes(b"\xff\xfe\x00")
    assert guards.check_secrets(tmp_path, ["image.bin", "deleted.py"]) == []


def test_gigsim_passes_when_it_was_never_provided(tmp_path: Path) -> None:
    assert guards.check_gigsim(tmp_path) == []


def test_gigsim_without_manifest_fails(tmp_path: Path) -> None:
    write(tmp_path, "gigsim/app.py", "x = 1\n")
    assert "record-gigsim" in guards.check_gigsim(tmp_path)[0].message


def recorded_gigsim(root: Path) -> None:
    write(root, "gigsim/app.py", "x = 1\n")
    write(root, "gigsim/data/jobs.json", "[]\n")
    write(root, guards.GIGSIM_MANIFEST, "")
    guards.record_gigsim(root)


def test_unchanged_gigsim_passes_even_with_windows_line_endings(tmp_path: Path) -> None:
    recorded_gigsim(tmp_path)
    (tmp_path / "gigsim/app.py").write_bytes(b"x = 1\r\n")
    assert guards.check_gigsim(tmp_path) == []


def test_generated_python_cache_is_ignored(tmp_path: Path) -> None:
    recorded_gigsim(tmp_path)
    write(tmp_path, "gigsim/__pycache__/app.pyc", "cache")
    assert guards.check_gigsim(tmp_path) == []


def test_modified_gigsim_file_fails(tmp_path: Path) -> None:
    recorded_gigsim(tmp_path)
    write(tmp_path, "gigsim/app.py", "x = 2\n")
    assert [v.message for v in guards.check_gigsim(tmp_path)] == [
        "modified; gigsim/ must not change"
    ]


def test_added_and_removed_gigsim_files_fail(tmp_path: Path) -> None:
    recorded_gigsim(tmp_path)
    write(tmp_path, "gigsim/extra.py", "y = 1\n")
    (tmp_path / "gigsim/data/jobs.json").unlink()
    assert sorted(v.message for v in guards.check_gigsim(tmp_path)) == [
        "added to gigsim/",
        "removed from gigsim/",
    ]


def test_removed_gigsim_directory_fails(tmp_path: Path) -> None:
    write(tmp_path, guards.GIGSIM_MANIFEST, "{}\n")
    assert "removed" in guards.check_gigsim(tmp_path)[0].message


@pytest.mark.parametrize(
    "source",
    [
        'message = SystemMessage("Extract the job requirements.")\n',
        'message = SystemMessage(content=f"Extract {x}")\n',
        'template = ChatPromptTemplate.from_template("Summarise {job}")\n',
        'messages = [{"role": "system", "content": "Extract the job requirements."}]\n',
        'WRITER_PROMPT = "Write a cover letter that cites the evidence for each claim."\n',
        'instructions: str = "Write a cover letter that cites the evidence for each claim."\n',
        'prompt = f"Summarise {job}"\n',
        'text = "You are a careful assistant who extracts job requirements."\n',
    ],
)
def test_inline_prompts_are_rejected(tmp_path: Path, source: str) -> None:
    write(tmp_path, "agent/nodes/writer.py", source)
    violations = guards.check_prompts(tmp_path)
    assert len(violations) == 1
    assert violations[0].path == "agent/nodes/writer.py"
    assert violations[0].line == 1


@pytest.mark.parametrize(
    "source",
    [
        '"""You are a module docstring that talks about the writer node at length."""\n',
        "message = SystemMessage(content=load_prompt('writer'))\n",
        'messages = [{"role": "system", "content": loaded}]\n',
        'prompt_path = "writer.v1.md"\n',
        'error = "The configured model could not be reached after several retries."\n',
    ],
)
def test_legitimate_code_is_not_flagged(tmp_path: Path, source: str) -> None:
    write(tmp_path, "agent/nodes/writer.py", source)
    assert guards.check_prompts(tmp_path) == []


def test_unparseable_python_is_reported(tmp_path: Path) -> None:
    write(tmp_path, "rag/broken.py", "def (:\n")
    assert guards.check_prompts(tmp_path)[0].message == "cannot be parsed"


def test_prompt_files_must_be_versioned(tmp_path: Path) -> None:
    write(tmp_path, "agent/prompts/README.md", "docs\n")
    write(tmp_path, "agent/prompts/writer.v1.md", "text\n")
    write(tmp_path, "agent/prompts/critic.md", "text\n")
    assert [v.path for v in guards.check_prompts(tmp_path)] == ["agent/prompts/critic.md"]


def test_main_returns_failure_for_violations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    write(tmp_path, "agent/x.py", 'prompt = f"Summarise {job}"\n')
    monkeypatch.setattr(guards, "REPO_ROOT", tmp_path)
    assert guards.main(["prompts"]) == 1


def test_main_returns_success_when_clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    write(tmp_path, "agent/x.py", "value = 1\n")
    monkeypatch.setattr(guards, "REPO_ROOT", tmp_path)
    assert guards.main(["prompts"]) == 0
