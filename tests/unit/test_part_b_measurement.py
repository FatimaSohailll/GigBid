import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from agent.config import get_config
from agent.handoffs import ClaimSupport, EvidenceItem, JobSpec, PriceQuote, ProposalDraft
from agent.nodes import writer
from agent.runtime import AgentRuntime
from evals.runners.ablation import hypothesis_check
from evals.runners.rag_eval import (
    check_gold,
    evaluate_per_requirement,
    evaluate_whole_job,
    select_variant,
)
from rag.index.reranker import CachingReranker
from rag.ingest.github import read_repository
from rag.ingest.loaders import UploadMetadata, load_upload
from rag.ingest.pipeline import ingest_documents, provision_tenant
from rag.verify import ClaimJudgement, recheck_final_text
from tests.support.rag_fakes import document, memory_components

EVIDENCE = [
    EvidenceItem(
        evidence_id="case:a",
        type="case_study",
        source="a.md",
        excerpt="Built a churn model in Python.",
    )
]


class AlwaysSupported:
    def judge(self, claim: str, evidence: list[EvidenceItem]) -> ClaimJudgement:
        return ClaimJudgement(
            support=ClaimSupport.SUPPORTED, evidence_ids=[evidence[0].evidence_id], explanation="ok"
        )


class NeverSupported:
    def judge(self, claim: str, evidence: list[EvidenceItem]) -> ClaimJudgement:
        return ClaimJudgement(support=ClaimSupport.UNSUPPORTED, explanation="no")


def draft(letter: str) -> ProposalDraft:
    return ProposalDraft(job_id="j", cover_letter=letter)


def test_final_check_counts_unsupported_claims_in_the_sent_text() -> None:
    check = recheck_final_text(
        draft("I built a churn model for 7 banks."), EVIDENCE, AlwaysSupported()
    )
    assert check.total_claims == 1
    assert check.unsupported == 1
    assert check.unsupported_rate == 1


def test_final_check_uses_the_judge_and_can_pass() -> None:
    letter = "I built a churn model in Python."
    assert recheck_final_text(draft(letter), EVIDENCE, AlwaysSupported()).unsupported == 0
    assert recheck_final_text(draft(letter), EVIDENCE, NeverSupported()).unsupported == 1


class FakeRetriever:
    def __init__(self, ranked: dict[str, list[str]]) -> None:
        self.ranked = ranked
        self.calls: list[str] = []

    def retrieve(self, query: str, tenant: str, k: int, filters: Any = None) -> list[EvidenceItem]:
        self.calls.append(query)
        return [
            EvidenceItem(evidence_id=item, type="code", source="s", excerpt="x")
            for item in self.ranked.get(query, [])[:k]
        ]


def gold(job_id: str = "j1", **values: Any) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "persona": "p",
        "job_type": "good_fit",
        "relevant_evidence": ["e1"],
        "requirements": [
            {"skill": "kafka", "evidence": ["e1"]},
            {"skill": "unknown", "evidence": []},
        ],
        **values,
    }


def test_per_requirement_scores_each_requirement_and_reuses_repeated_queries() -> None:
    retriever = FakeRetriever({"kafka": ["e9", "e1"]})
    result = evaluate_per_requirement(retriever, [gold(), gold("j2")], fit_k=1)
    assert result["overall"]["count"] == 2
    assert result["overall"]["recall_at_5"] == 1
    assert result["overall"]["hit_at_fit_k"] == 0
    assert retriever.calls.count("kafka") == 1
    assert result["distinct_queries"] == 2


def test_refusing_a_requirement_with_no_evidence_is_measured() -> None:
    result = evaluate_per_requirement(FakeRetriever({"kafka": ["e1"]}), [gold()], fit_k=3)
    assert result["no_evidence_refusal_rate"] == 1
    result = evaluate_per_requirement(
        FakeRetriever({"kafka": ["e1"], "unknown": ["e2"]}), [gold()], fit_k=3
    )
    assert result["no_evidence_refusal_rate"] == 0


def test_whole_job_uses_the_full_post_as_one_query() -> None:
    query = "T\nD"
    retriever = FakeRetriever({query: ["e1"]})
    result = evaluate_whole_job(retriever, [gold()], {"j1": {"title": "T", "description": "D"}})
    assert result["overall"]["recall_at_5"] == 1
    assert retriever.calls == [query]


def test_gold_labels_must_match_the_corpus(tmp_path: Path) -> None:
    components = memory_components(tmp_path)
    ingest_documents([document("e1", "kafka")], "p", components)
    assert check_gold([gold()], {"j1": {}}, components)["labelled_requirements"] == 1
    with pytest.raises(ValueError, match="unknown evidence"):
        check_gold([gold(relevant_evidence=["missing"])], {"j1": {}}, components)
    with pytest.raises(ValueError, match="unknown jobs"):
        check_gold([gold()], {}, components)


def scored(variant: str, whole: float, per_requirement: float) -> dict[str, Any]:
    return {
        "variant": variant,
        "whole_job": {"overall": {"recall_at_5": whole}},
        "per_requirement": {"overall": {"recall_at_5": per_requirement, "ndcg_at_10": 0, "mrr": 0}},
    }


def test_deployment_is_chosen_on_per_requirement_results() -> None:
    assert select_variant([scored("a", 0.9, 0.1), scored("b", 0.1, 0.5)]) == "b"


def test_hypothesis_is_judged_on_jobs_with_many_requirements() -> None:
    def result(value: float) -> dict[str, Any]:
        return {"whole_job": {"by_requirement_count": {"4_or_more": {"recall_at_5": value}}}}

    assert hypothesis_check(result(0.1), result(0.3))["supported"] is True
    assert hypothesis_check(result(0.1), result(0.2))["supported"] is False


class CountingReranker:
    name = "m"

    def __init__(self) -> None:
        self.calls = 0

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        self.calls += 1
        return [float(len(text)) for text in documents]


def test_reranker_scores_are_saved_and_only_missing_ones_computed(tmp_path: Path) -> None:
    inner = CountingReranker()
    cached = CachingReranker(inner, tmp_path / "cache.sqlite3")
    assert cached.score("q", ["a", "bb"]) == [1.0, 2.0]
    assert cached.score("q", ["bb", "a"]) == [2.0, 1.0]
    assert inner.calls == 1
    cached.score("q", ["a", "ccc"])
    assert inner.calls == 2


def test_upload_keeps_metadata_and_front_matter_takes_precedence(tmp_path: Path) -> None:
    path = tmp_path / "bid.md"
    path.write_text("---\ntitle: Real title\nskills: [dbt]\n---\nBody.", encoding="utf-8")
    item = load_upload(
        path,
        "u1",
        "Form title",
        UploadMetadata(
            item_type="proposal", skills=["Airflow"], outcome="won", client_industry="tax"
        ),
    )
    assert item.item_id == "proposal:upload:u1"
    assert (item.title, item.skills, item.outcome) == ("Real title", ["dbt"], "won")
    assert item.client_industry == "tax"


def test_copied_chunks_remember_their_persona(tmp_path: Path) -> None:
    components = memory_components(tmp_path)
    ingest_documents([document("a", "kafka")], "persona", components)
    provision_tenant("persona", "user1", components)
    hits = components.index.search(components.embedder.embed_query("kafka"), "user1", 1, None)
    assert hits[0].payload["tenant"] == "user1"
    assert hits[0].payload["persona"] == "persona"


class FakeGithub:
    def __init__(self) -> None:
        self.paths: list[str] = []
        self.tree: dict[str, list[dict[str, Any]]] = {
            "/": [
                {"type": "file", "name": "app.py", "path": "app.py", "size": 10},
                {"type": "file", "name": "README.md", "path": "README.md", "size": 10},
                {"type": "file", "name": "big.py", "path": "big.py", "size": 999_999},
                {"type": "file", "name": "logo.png", "path": "logo.png", "size": 10},
                {"type": "dir", "name": "node_modules", "path": "node_modules"},
                {"type": "dir", "name": "src", "path": "src"},
            ],
            "src/": [{"type": "file", "name": "core.go", "path": "src/core.go", "size": 10}],
        }

    async def call_text(self, agent: str, tool: str, arguments: dict[str, Any]) -> list[str]:
        assert (agent, tool) == ("github_ingest", "get_file_contents")
        path = arguments["path"]
        self.paths.append(path)
        if path in self.tree:
            return [json.dumps(self.tree[path])]
        return ["successfully downloaded text file", f"contents of {path}"]


async def test_repository_reading_keeps_readmes_first_and_skips_junk() -> None:
    gateway = FakeGithub()
    items = await read_repository(gateway, "me", "repo", get_config().rag)
    assert [item.item_id for item in items] == [
        "readme:github:me/repo:README.md",
        "code:github:me/repo:app.py",
        "code:github:me/repo:src/core.go",
    ]
    assert items[1].text == "contents of app.py"
    assert items[1].skills == ["Python"]
    assert items[2].project == "repo"
    assert "node_modules/" not in gateway.paths


async def test_repository_reading_respects_the_file_limit() -> None:
    settings = get_config().rag.model_copy(update={"github_max_files": 1})
    items = await read_repository(FakeGithub(), "me", "repo", settings)
    assert [item.type for item in items] == ["readme"]


class FakeTools:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call(self, agent: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"agent": agent, "tool": tool, **arguments})
        return {"items": [{"excerpt": "Your churn problem is really a data problem."}]}


class FakeMemory:
    async def style_rules(self, _: str) -> list[str]:
        return []

    async def outcome_insights(self, _: str, __: str) -> list[str]:
        return []


class FakeLlm:
    def __init__(self) -> None:
        self.data = ""

    async def structured(self, node: str, instructions: str, data: str, schema: Any) -> Any:
        self.data = data
        from agent.llm import LlmResult

        return LlmResult(schema(cover_letter="Letter."), 1, "m")


def writer_state() -> dict[str, Any]:
    return {
        "job": {
            "job_id": "j",
            "title": "T",
            "description": "D",
            "category": "ml",
            "budget": {"min": 1, "max": 2},
        },
        "job_spec": JobSpec(job_id="j", title="T", required_skills=["Python"]),
        "price_quote": PriceQuote(
            job_id="j", amount=100, floor=1, connects_cost=1, connects_worth_it=True, rationale="r"
        ),
        "user_id": "u",
        "evidence": [],
    }


async def run_writer(variant: str) -> tuple[FakeTools, FakeLlm]:
    base = get_config()
    config = base.model_copy(update={"rag": base.rag.model_copy(update={"variant": variant})})
    tools, llm = FakeTools(), FakeLlm()
    await writer.run(writer_state(), AgentRuntime(config, llm, tools, FakeMemory()))  # type: ignore[arg-type]
    return tools, llm


async def test_outcome_aware_variants_show_winning_phrasing_separately() -> None:
    tools, llm = await run_writer("v4_outcome")
    assert tools.calls[0]["filters"] == {"types": ["proposal"], "outcomes": ["won", "replied"]}
    assert "<untrusted_phrasing_examples>" in llm.data
    assert "churn problem" in llm.data


async def test_other_variants_do_not_fetch_phrasing() -> None:
    tools, llm = await run_writer("v2_hybrid")
    assert tools.calls == []
    assert "phrasing_examples" not in llm.data


def test_combinations_with_outcome_count_as_outcome_aware() -> None:
    base = get_config()

    def aware(variant: str) -> bool:
        return base.model_copy(
            update={"rag": base.rag.model_copy(update={"variant": variant})}
        ).outcome_aware

    assert aware("combo:hybrid+outcome") is True
    assert aware("combo:hybrid+rerank") is False
