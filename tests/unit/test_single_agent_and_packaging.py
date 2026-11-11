import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from agent.config import get_config
from agent.prompt_loader import load_prompt
from agent.single_agent import draft_proposal
from mcp_servers.portfolio.local import index_portfolio
from tests.support.rag_fakes import memory_components


class ScriptedChat(BaseChatModel):
    replies: list[AIMessage]

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedChat":
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])


def call(name: str, args: dict[str, Any], number: int) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": f"c{number}"}],
        usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
    )


@tool
def get_job(job_id: str) -> str:
    """Get one job."""
    return json.dumps({"job_id": job_id, "title": "Churn model"})


@tool
def search_evidence(requirement: str) -> str:
    """Search the portfolio."""
    return json.dumps(
        {
            "items": [
                {
                    "evidence_id": "case:me:churn",
                    "type": "case_study",
                    "source": "churn.md",
                    "excerpt": "Built a churn model.",
                }
            ]
        }
    )


async def test_the_agent_drafts_with_tools_and_keeps_the_evidence_it_saw() -> None:
    model = ScriptedChat(
        replies=[
            call("get_job", {"job_id": "j1"}, 1),
            call("search_evidence", {"requirement": "churn"}, 2),
            AIMessage(
                content="Your churn problem needs ranking. I built a churn model [case:me:churn].",
                usage_metadata={"input_tokens": 300, "output_tokens": 40, "total_tokens": 340},
            ),
        ]
    )
    draft = await draft_proposal(model, [get_job, search_evidence], "Write.", "j1", 12)
    assert draft.tool_calls == ["get_job", "search_evidence"]
    assert [item.evidence_id for item in draft.evidence] == ["case:me:churn"]
    assert draft.cover_letter.startswith("Your churn problem")
    assert (draft.input_tokens, draft.output_tokens) == (500, 60)


def test_single_agent_has_a_prompt_model_and_read_only_tools() -> None:
    config = get_config()
    assert "search_evidence" in load_prompt("single_agent")
    assert config.model_for("single_agent")[0].model
    tools = set(config.tools_for("single_agent"))
    assert {"get_job", "search_evidence"} <= tools
    assert not tools & {"submit_proposal", "send_message", "create_proposal_draft"}


def test_local_server_indexes_a_documented_portfolio_folder(tmp_path: Path) -> None:
    folder = tmp_path / "portfolio"
    (folder / "case_studies").mkdir(parents=True)
    (folder / "case_studies" / "churn.md").write_text(
        "---\ntitle: Churn model\n---\nBuilt a churn model in Python.", encoding="utf-8"
    )
    (folder / "index.json").write_text(
        json.dumps(
            [
                {
                    "item_id": "case:me:churn",
                    "type": "case_study",
                    "path": "case_studies/churn.md",
                    "project": "churn",
                    "skills": ["Python"],
                }
            ]
        ),
        encoding="utf-8",
    )
    components = memory_components(tmp_path)
    assert index_portfolio(folder, "me", components).added == 1
    assert index_portfolio(folder, "me", components).unchanged == 1
    assert components.corpus.get("me", "case:me:churn") is not None


def test_a_folder_without_an_index_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match=r"index\.json"):
        index_portfolio(tmp_path, "me", memory_components(tmp_path))
