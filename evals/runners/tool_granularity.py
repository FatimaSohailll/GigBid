"""R&D question A: does tool granularity change agent behaviour?

Usage: python -m evals.runners.tool_granularity [--limit 3]

Every tool set uses the same model, settings, system prompt and tasks from
evals/datasets/tool_study/config.yaml. Tools are not executed; each call gets a fixed reply.
Finished tasks are kept, so a stopped run can continue.
"""

import argparse
import asyncio
import json
import os
import platform
import time
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

import httpx
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from agent.prompt_loader import load_prompt
from evals.harness import append_row
from evals.runners.rag_eval import RESULTS_DIR, write_result
from evals.tool_study import (
    TOOL_SETS,
    Call,
    ToolSet,
    load_study_config,
    load_tasks,
    score_task,
)

ROWS = RESULTS_DIR / "tool_granularity_rows.jsonl"
_NO_MORE_DATA = '{"note":"No further data."}'


def build_model(settings: dict[str, Any]) -> Any:
    return init_chat_model(
        settings["name"],
        model_provider=settings["provider"],
        temperature=settings["temperature"],
        **settings.get("options", {}),
    )


def model_identity(settings: dict[str, Any]) -> dict[str, Any]:
    """The exact build that ran, since a name such as qwen3:8b can point at a newer one later."""
    if settings["provider"] != "ollama":
        return {"provider": settings["provider"], "model": settings["name"]}
    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    models = httpx.get(f"{base}/api/tags", timeout=10).json()["models"]
    match = next(item for item in models if item["name"] == settings["name"])
    return {
        "provider": "ollama",
        "model": match["name"],
        "digest": match["digest"],
        "details": match["details"],
        "ollama_version": httpx.get(f"{base}/api/version", timeout=10).json()["version"],
    }


def _usage(reply: AIMessage) -> tuple[int, int]:
    usage: dict[str, Any] = dict(reply.usage_metadata or {})
    return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))


async def run_task(model: Any, tool_set: ToolSet, task: dict[str, Any]) -> dict[str, Any]:
    """Let the model work through one task, replying to each call with the task's fixed result."""
    bound = model.bind_tools(tool_set.definitions)
    messages: list[BaseMessage] = [
        SystemMessage(content=load_prompt("tool_study")),
        HumanMessage(content=task["instruction"]),
    ]
    steps = task["steps"]
    calls: list[Call] = []
    input_tokens = output_tokens = 0
    started = time.perf_counter()
    for _ in range(len(steps) + 1):
        reply: AIMessage = await bound.ainvoke(messages)
        used_in, used_out = _usage(reply)
        input_tokens += used_in
        output_tokens += used_out
        messages.append(reply)
        if not reply.tool_calls:
            break
        for tool_call in reply.tool_calls:
            position = len(calls)
            calls.append(Call(tool_call["name"], tool_call["args"]))
            result = steps[position]["observation"] if position < len(steps) else _NO_MORE_DATA
            messages.append(ToolMessage(content=result, tool_call_id=tool_call["id"] or ""))
    return {
        "tool_set": tool_set.name,
        "task_id": task["id"],
        "category": task["category"],
        **score_task(tool_set, task, calls),
        "calls": [{"tool": call.tool, "args": call.args} for call in calls],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_seconds": round(time.perf_counter() - started, 2),
    }


def _rate(hits: float, total: float) -> float | None:
    return round(hits / total, 4) if total else None


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    steps = sum(row["expected_steps"] for row in rows)
    latency = sum(row["latency_seconds"] for row in rows)
    output = sum(row["output_tokens"] for row in rows)
    return {
        "tasks": len(rows),
        "selection_accuracy": _rate(sum(row["selection_correct"] for row in rows), len(rows)),
        "step_selection_accuracy": _rate(sum(row["steps_selected"] for row in rows), steps),
        "argument_accuracy": _rate(
            sum(row["arguments_correct"] for row in rows),
            sum(row["arguments_checked"] for row in rows),
        ),
        "task_success": _rate(sum(row["task_correct"] for row in rows), len(rows)),
        "tokens_per_task": round(
            mean(row["input_tokens"] + row["output_tokens"] for row in rows), 1
        ),
        "input_tokens_per_task": round(mean(row["input_tokens"] for row in rows), 1),
        "output_tokens_per_task": round(mean(row["output_tokens"] for row in rows), 1),
        "output_tokens_per_second": round(output / latency, 2) if latency else None,
    }


def report(rows: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    by_set: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_set[row["tool_set"]].append(row)
    results: dict[str, Any] = {}
    for name, group in sorted(by_set.items()):
        categories: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in group:
            categories[row["category"]].append(row)
        results[name] = {
            "tool_count": len(ToolSet(name).definitions),
            "overall": summarise(group),
            "by_category": {key: summarise(value) for key, value in sorted(categories.items())},
        }
    return {
        "hypothesis": " ".join(config["hypothesis"].split()),
        "model": config["model"],
        "model_identity": model_identity(config["model"]),
        "machine": {"platform": platform.platform(), "processor": platform.processor()},
        "limitations": config["limitations"],
        "results": results,
    }


def _finished(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return {(row["tool_set"], row["task_id"]): row for row in rows}


async def run(limit: int | None, rows_path: Path = ROWS) -> dict[str, Any]:
    load_dotenv()
    config = load_study_config()
    model = build_model(config["model"])
    tasks = load_tasks()[:limit]
    finished = _finished(rows_path)
    rows: list[dict[str, Any]] = []
    for name in TOOL_SETS:
        tool_set = ToolSet(name)
        for task in tasks:
            key = (name, task["id"])
            if key not in finished:
                finished[key] = await run_task(model, tool_set, task)
                append_row(rows_path, finished[key])
                print(name, task["id"], finished[key]["task_correct"], flush=True)
            rows.append(finished[key])
    result = report(rows, config)
    write_result("tool_granularity.json", result)
    return result


if __name__ == "__main__":
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--limit", type=int, default=None, help="Run only the first N tasks")
    arguments = options.parse_args()
    outcome = asyncio.run(run(arguments.limit))
    print(
        json.dumps({key: value["overall"] for key, value in outcome["results"].items()}, indent=2)
    )
