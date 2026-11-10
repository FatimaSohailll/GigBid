"""The three tool configurations for R&D question A, and how a model's calls are scored."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DATA_DIR = Path("evals/datasets/tool_study")
TOOL_SETS = ("fine", "coarse", "rewritten")
_WRITES = {
    "create_proposal_draft",
    "submit_proposal",
    "send_message",
    "create_interview_hold",
    "create_email_draft",
}


@dataclass(frozen=True)
class Call:
    tool: str
    args: dict[str, Any]


def _yaml(name: str, data_dir: Path) -> Any:
    return yaml.safe_load((data_dir / name).read_text(encoding="utf-8"))


def load_tasks(data_dir: Path = DATA_DIR) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = _yaml("tasks.yaml", data_dir)
    return tasks


def load_study_config(data_dir: Path = DATA_DIR) -> dict[str, Any]:
    config: dict[str, Any] = _yaml("config.yaml", data_dir)
    return config


def _function(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


def _property(kind: str) -> dict[str, Any]:
    return {"type": kind, "items": {"type": "string"}} if kind == "array" else {"type": kind}


class ToolSet:
    """The definitions a model sees, and the translation of its calls back to canonical form."""

    def __init__(self, name: str, data_dir: Path = DATA_DIR) -> None:
        if name not in TOOL_SETS:
            raise ValueError(f"Unknown tool set '{name}'")
        self.name = name
        self._data_dir = data_dir
        self._fine: dict[str, Any] = _yaml("fine_tools.yaml", data_dir) if name == "fine" else {}
        self._coarse: dict[str, Any] = (
            _yaml("coarse_tools.yaml", data_dir) if name == "coarse" else {}
        )
        self.definitions = getattr(self, f"_{name}_definitions")()

    def _rewritten_definitions(self) -> list[dict[str, Any]]:
        current = json.loads((self._data_dir / "current_tools.json").read_text(encoding="utf-8"))
        rewritten = _yaml("rewritten_descriptions.yaml", self._data_dir)
        return [
            _function(name, " ".join(rewritten[name].split()), current[name]["parameters"])
            for name in sorted(current)
        ]

    def _fine_definitions(self) -> list[dict[str, Any]]:
        return [
            _function(
                name,
                spec["description"],
                {
                    "type": "object",
                    "properties": {
                        key: _property(kind) for key, kind in (spec.get("params") or {}).items()
                    },
                    "required": spec.get("required", []),
                },
            )
            for name, spec in sorted(self._fine.items())
        ]

    def _coarse_definitions(self) -> list[dict[str, Any]]:
        return [
            _function(
                name,
                spec["description"],
                {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": spec["actions"]},
                        "params": {"type": "object"},
                    },
                    "required": ["action", "params"],
                },
            )
            for name, spec in sorted(self._coarse.items())
        ]

    def canonical(self, call: Call) -> Call:
        """Express a model's call in the current tool names, so arguments compare directly."""
        if self.name == "coarse":
            params = call.args.get("params")
            args = dict(params) if isinstance(params, dict) else {}
            return Call(str(call.args.get("action")), _flatten(args))
        if self.name == "fine" and call.tool in self._fine:
            spec = self._fine[call.tool]
            args = {**call.args, **_copy(spec.get("fixed") or {})}
            for key, parent in (spec.get("nest") or {}).items():
                if key in args:
                    args.setdefault(parent, {})[key] = args.pop(key)
            return Call(spec["canonical"], args)
        return Call(call.tool, _flatten(call.args))

    def selected_correctly(self, call: Call, step: Mapping[str, Any]) -> bool:
        if self.name == "coarse":
            action = call.args.get("action")
            owner = next((n for n, s in self._coarse.items() if step["tool"] in s["actions"]), "")
            return bool(call.tool == owner and action == step["tool"])
        if self.name == "fine":
            return bool(call.tool == step.get("fine", step["tool"]))
        return bool(call.tool == step["tool"])


def _copy(value: dict[str, Any]) -> dict[str, Any]:
    copied: dict[str, Any] = json.loads(json.dumps(value))
    return copied


def _flatten(args: dict[str, Any]) -> dict[str, Any]:
    """Write tools wrap their fields in "request"; compare the fields themselves."""
    request = args.get("request")
    if isinstance(request, dict):
        return {**{k: v for k, v in args.items() if k != "request"}, **request}
    return dict(args)


def value_matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict) and set(expected) == {"contains"}:
        return isinstance(actual, str) and expected["contains"].lower() in actual.lower()
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            value_matches(value, actual.get(key)) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and {str(v).lower() for v in expected} == {
            str(v).lower() for v in actual
        }
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if isinstance(expected, int | float) and isinstance(actual, int | float | str):
        try:
            return float(actual) == float(expected)
        except ValueError:
            return False
    return isinstance(actual, str) and str(expected).lower() == actual.lower()


def arguments_correct(canonical: Call, step: Mapping[str, Any]) -> bool:
    if not all(value_matches(v, canonical.args.get(k)) for k, v in step["args"].items()):
        return False
    return step["tool"] not in _WRITES or bool(canonical.args.get("idempotency_key"))


def score_task(tool_set: ToolSet, task: Mapping[str, Any], calls: list[Call]) -> dict[str, Any]:
    """Score the ordered calls. A task counts only if the sequence is exactly right."""
    steps = task["steps"]
    selected = [
        tool_set.selected_correctly(call, step) for call, step in zip(calls, steps, strict=False)
    ]
    arguments = [
        arguments_correct(tool_set.canonical(call), step)
        for call, step, right in zip(calls, steps, selected, strict=False)
        if right
    ]
    selection_ok = len(calls) == len(steps) and all(selected)
    return {
        "expected_steps": len(steps),
        "predicted_calls": len(calls),
        "steps_selected": sum(selected),
        "arguments_checked": len(arguments),
        "arguments_correct": sum(arguments),
        "selection_correct": selection_ok,
        "task_correct": selection_ok and all(arguments),
    }
