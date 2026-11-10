"""V8: corrective retrieval as a LangGraph loop: grade, re-query when weak, refuse when none."""

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from agent.handoffs import EvidenceItem
from rag.index.bm25 import tokenize
from rag.types import EvidenceFilters, RagComponents, Retriever
from rag.variants.composed import ComposedRetriever, Stages, decompose_query


class _State(TypedDict, total=False):
    query: str
    tenant: str
    k: int
    filters: EvidenceFilters | None
    attempt: int
    items: list[EvidenceItem]
    uncovered: list[str]


def _covered(requirement: str, items: list[EvidenceItem]) -> bool:
    terms = set(tokenize(requirement))
    return any(terms <= set(tokenize(item.excerpt)) for item in items)


class CorrectiveRetriever:
    def __init__(self, components: RagComponents) -> None:
        self._config = components.config
        self._base = ComposedRetriever(components, Stages(hybrid=True))
        graph = StateGraph(_State)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("grade", self._grade)
        graph.add_node("requery", self._requery)
        graph.add_node("refuse", lambda _: {"items": []})
        graph.add_edge(START, "retrieve")
        graph.add_edge("retrieve", "grade")
        graph.add_conditional_edges("grade", self._route, ["requery", "refuse", END])
        graph.add_edge("requery", "grade")
        graph.add_edge("refuse", END)
        self._graph = graph.compile()

    def _retrieve(self, state: _State) -> dict[str, Any]:
        items = self._base.retrieve(
            state["query"], state["tenant"], state["k"], state.get("filters")
        )
        return {"items": items, "attempt": 0}

    def _grade(self, state: _State) -> dict[str, Any]:
        requirements = decompose_query(state["query"], self._config.max_subqueries)
        return {"uncovered": [r for r in requirements if not _covered(r, state["items"])]}

    def _coverage(self, state: _State) -> float:
        total = len(decompose_query(state["query"], self._config.max_subqueries))
        return 1 - len(state["uncovered"]) / total

    def _route(self, state: _State) -> str:
        if self._coverage(state) >= self._config.corrective_min_coverage:
            return END
        return "requery" if state["attempt"] < self._config.corrective_max_retries else "refuse"

    def _requery(self, state: _State) -> dict[str, Any]:
        found = {item.evidence_id: item for item in state["items"]}
        for requirement in state["uncovered"]:
            for item in self._base.retrieve(
                requirement, state["tenant"], state["k"], state.get("filters")
            ):
                found.setdefault(item.evidence_id, item)
        return {"items": list(found.values())[: state["k"]], "attempt": state["attempt"] + 1}

    def retrieve(
        self,
        query: str,
        tenant: str,
        k: int,
        filters: EvidenceFilters | None = None,
    ) -> list[EvidenceItem]:
        result = self._graph.invoke({"query": query, "tenant": tenant, "k": k, "filters": filters})
        return list(result["items"])


def build(components: RagComponents) -> Retriever:
    return CorrectiveRetriever(components)
