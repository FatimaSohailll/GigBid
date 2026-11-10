from collections import Counter
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from agent.marketplace_port import SubmissionMode
from agent.mcp_client import github_connection, upwork_connection
from evals.runners.tool_granularity import run_task, summarise
from evals.tool_study import (
    TOOL_SETS,
    Call,
    ToolSet,
    load_study_config,
    load_tasks,
    score_task,
    value_matches,
)
from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter
from mcp_servers.tool_descriptions import TOOL_DESCRIPTIONS


def task(tool: str = "get_job", fine: str | None = None, **args: Any) -> dict[str, Any]:
    step: dict[str, Any] = {"tool": tool, "args": args, "observation": "{}"}
    if fine:
        step["fine"] = fine
    return {"id": "t", "category": "read", "instruction": "x", "steps": [step]}


def test_there_are_forty_tasks_with_unique_ids() -> None:
    tasks = load_tasks()
    assert len(tasks) == 40
    assert len({item["id"] for item in tasks}) == 40
    assert Counter(item["category"] for item in tasks) == {
        "read": 12,
        "near_miss": 8,
        "write": 8,
        "multi_step": 8,
        "no_tool": 4,
    }


def test_every_expected_tool_exists_in_every_set() -> None:
    fine = {t["function"]["name"] for t in ToolSet("fine").definitions}
    rewritten = {t["function"]["name"] for t in ToolSet("rewritten").definitions}
    actions = {
        action
        for t in ToolSet("coarse").definitions
        for action in t["function"]["parameters"]["properties"]["action"]["enum"]
    }
    for item in load_tasks():
        for step in item["steps"]:
            assert step.get("fine", step["tool"]) in fine, item["id"]
            assert step["tool"] in rewritten, item["id"]
            assert step["tool"] in actions, item["id"]


def test_set_sizes_differ_as_designed() -> None:
    sizes = {name: len(ToolSet(name).definitions) for name in TOOL_SETS}
    assert sizes["coarse"] == 4
    assert sizes["rewritten"] == 18
    assert sizes["fine"] > 30


def test_the_experiment_config_fixes_one_model_at_temperature_zero() -> None:
    model = load_study_config()["model"]
    assert model["temperature"] == 0
    assert model["name"] and model["provider"]


def test_rewritten_descriptions_keep_the_approval_rule() -> None:
    tools = {t["function"]["name"]: t["function"] for t in ToolSet("rewritten").definitions}
    assert "approval" in tools["submit_proposal"]["description"]
    assert "untrusted" in tools["get_job"]["description"]


@pytest.mark.parametrize(
    ("expected", "actual", "matches"),
    [
        ({"contains": "churn"}, "Churn models", True),
        ({"contains": "churn"}, "retention", False),
        (["Kafka"], ["kafka"], True),
        (["Kafka"], ["kafka", "dbt"], False),
        (1200, "1200.0", True),
        (True, False, False),
        ("job-1", "JOB-1", True),
        ({"types": ["review"]}, {"types": ["review"], "skills": []}, True),
    ],
)
def test_value_matching(expected: Any, actual: Any, matches: bool) -> None:
    assert value_matches(expected, actual) is matches


def test_fine_tool_must_be_the_exact_narrow_tool() -> None:
    tools = ToolSet("fine")
    item = task(fine="get_job_budget", job_id="j1")
    right = score_task(tools, item, [Call("get_job_budget", {"job_id": "j1"})])
    wrong = score_task(tools, item, [Call("get_job", {"job_id": "j1"})])
    assert right["task_correct"] is True
    assert wrong["selection_correct"] is False


def test_fine_preview_tool_translates_to_a_dry_run() -> None:
    item = task("submit_proposal", fine="preview_submit_proposal", proposal_id="p", dry_run=True)
    call = Call("preview_submit_proposal", {"proposal_id": "p", "idempotency_key": "abcdefgh"})
    assert score_task(ToolSet("fine"), item, [call])["task_correct"] is True


def test_fine_outcome_parameter_nests_under_filters() -> None:
    item = task(
        "search_evidence",
        fine="search_past_proposals",
        requirement={"contains": "dash"},
        filters={"types": ["proposal"], "outcomes": ["won"]},
    )
    call = Call("search_past_proposals", {"requirement": "dashboards", "outcomes": ["won"]})
    assert score_task(ToolSet("fine"), item, [call])["task_correct"] is True


def test_coarse_needs_the_right_tool_and_action() -> None:
    tools = ToolSet("coarse")
    item = task(job_id="j1")
    good = Call("marketplace_read", {"action": "get_job", "params": {"job_id": "j1"}})
    wrong_action = Call("marketplace_read", {"action": "get_proposal", "params": {}})
    assert score_task(tools, item, [good])["task_correct"] is True
    assert score_task(tools, item, [wrong_action])["selection_correct"] is False


def test_writes_must_carry_an_idempotency_key() -> None:
    tools = ToolSet("rewritten")
    item = task("submit_proposal", proposal_id="p")
    without = Call("submit_proposal", {"request": {"proposal_id": "p"}})
    with_key = Call("submit_proposal", {"request": {"proposal_id": "p", "idempotency_key": "k"}})
    assert score_task(tools, item, [without])["task_correct"] is False
    assert score_task(tools, item, [with_key])["task_correct"] is True


def test_calling_a_tool_on_a_no_tool_task_is_wrong() -> None:
    item = {"id": "x", "category": "no_tool", "instruction": "x", "steps": []}
    assert score_task(ToolSet("rewritten"), item, [])["task_correct"] is True
    extra = score_task(ToolSet("rewritten"), item, [Call("list_skills", {})])
    assert extra["selection_correct"] is False


def test_an_extra_call_fails_the_sequence() -> None:
    item = task(job_id="j1")
    calls = [Call("get_job", {"job_id": "j1"}), Call("get_job", {"job_id": "j1"})]
    assert score_task(ToolSet("rewritten"), item, calls)["selection_correct"] is False


class ScriptedModel:
    def __init__(self, replies: list[AIMessage]) -> None:
        self.replies = replies
        self.seen: list[list[Any]] = []

    def bind_tools(self, _: list[dict[str, Any]]) -> "ScriptedModel":
        return self

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self.seen.append(list(messages))
        return self.replies.pop(0)


def reply(*calls: tuple[str, dict[str, Any]]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": n, "args": a, "id": f"c{i}"} for i, (n, a) in enumerate(calls)],
        usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
    )


async def test_a_multi_step_task_receives_each_fixed_result() -> None:
    item = {
        "id": "m",
        "category": "multi_step",
        "instruction": "x",
        "steps": [
            {"tool": "get_job", "args": {"job_id": "j1"}, "observation": '{"client_id":"c9"}'},
            {"tool": "get_client_history", "args": {"client_id": "c9"}, "observation": "{}"},
        ],
    }
    model = ScriptedModel(
        [
            reply(("get_job", {"job_id": "j1"})),
            reply(("get_client_history", {"client_id": "c9"})),
            reply(),
        ]
    )
    row = await run_task(model, ToolSet("rewritten"), item)
    assert row["task_correct"] is True
    assert row["input_tokens"] == 300
    assert '{"client_id":"c9"}' in str(model.seen[1][-1].content)


def test_summary_separates_selection_from_arguments() -> None:
    row = {
        "expected_steps": 1,
        "steps_selected": 1,
        "arguments_checked": 1,
        "arguments_correct": 0,
        "selection_correct": True,
        "task_correct": False,
        "input_tokens": 100,
        "output_tokens": 10,
        "latency_seconds": 1.0,
    }
    summary = summarise([row])
    assert summary["selection_accuracy"] == 1
    assert summary["argument_accuracy"] == 0
    assert summary["task_success"] == 0
    assert summary["tokens_per_task"] == 110


def test_github_needs_both_settings_and_is_read_only() -> None:
    assert github_connection({"MCP_GITHUB_URL": "https://x/mcp"}) is None
    assert github_connection({"GITHUB_PERSONAL_ACCESS_TOKEN": "t"}) is None
    connection = github_connection(
        {"MCP_GITHUB_URL": "https://x/mcp", "GITHUB_PERSONAL_ACCESS_TOKEN": "t"}
    )
    assert connection is not None
    assert connection["github"]["headers"]["X-MCP-Readonly"] == "true"
    assert connection["github"]["headers"]["Authorization"] == "Bearer t"


def test_upwork_connects_as_one_user_only_when_configured() -> None:
    env = {"MCP_UPWORK_URL": "https://x/mcp"}
    assert upwork_connection("", env) is None
    assert upwork_connection("t", {}) is None
    connection = upwork_connection("t", env)
    assert connection is not None
    assert connection["upwork"]["headers"]["Authorization"] == "Bearer t"


def test_shipped_descriptions_are_exactly_what_was_measured() -> None:
    measured = {t["function"]["name"]: t["function"] for t in ToolSet("rewritten").definitions}
    assert TOOL_DESCRIPTIONS == {name: tool["description"] for name, tool in measured.items()}


def test_gigsim_submits_directly() -> None:
    assert GigSimAdapter.submission_mode is SubmissionMode.DIRECT
