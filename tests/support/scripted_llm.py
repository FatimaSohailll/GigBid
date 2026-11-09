"""Scripted model answers. Everything else in these graph runs is real."""

import re
from collections import Counter, defaultdict
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from agent.llm import LlmResult

Responder = Callable[[str, type[BaseModel]], BaseModel]
GOOD_LETTER = (
    "Your churn baseline needs a reproducible comparison that your retention team can trust. "
    "I built rolling usage aggregates for a telecom operator and tuned a gradient boosted model "
    "with scikit-learn [case:ai:churn]. The retention team reached 31% more leavers at the same "
    "call volume [case:ai:churn]. For your project I would compare logistic regression with a "
    "random forest, report precision and recall, and document the limitations clearly. I would "
    "begin by reviewing your customer data and agreeing how results should be judged."
)


def _ids(data: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\[((?:case|readme|code|review):[^\]]+)\]", data)))


def default_answers() -> dict[str, Responder]:
    from agent.handoffs import ClaimSupport, JobSpec, ScreeningAnswer
    from rag.verify import ClaimJudgement

    def analyst(_: str, schema: type[BaseModel]) -> BaseModel:
        assert schema is JobSpec
        return JobSpec(job_id="model", title="model", required_skills=["Python"])

    def risk(_: str, schema: type[BaseModel]) -> BaseModel:
        return schema.model_validate({"score": 0.1, "flags": [], "explanation": "Looks normal."})

    def fit(data: str, schema: type[BaseModel]) -> BaseModel:
        names = re.findall(r"^Requirement: (.+)$", data, re.MULTILINE)
        ids = _ids(data)
        return schema.model_validate(
            {
                "matches": [
                    {
                        "requirement": name,
                        "status": "covered" if ids else "missing",
                        "evidence_ids": ids[:1],
                        "reason": "Shown in the portfolio.",
                    }
                    for name in names
                ],
                "reasons": ["Matches the portfolio."],
            }
        )

    def writer(data: str, schema: type[BaseModel]) -> BaseModel:
        questions = re.findall(r"Screening questions: \[(.*?)\]", data)
        answers = []
        if questions and questions[0]:
            answers = [ScreeningAnswer(question="q", answer="I would use precision-recall curves.")]
        return schema.model_validate({"cover_letter": GOOD_LETTER, "screening_answers": answers})

    def critic(_: str, schema: type[BaseModel]) -> BaseModel:
        return schema.model_validate({"grounded": True, "personalised": True, "reasons": []})

    def compliance(_: str, schema: type[BaseModel]) -> BaseModel:
        return schema.model_validate({"violations": [], "block": False, "reason": ""})

    def verifier(data: str, _: type[BaseModel]) -> BaseModel:
        return ClaimJudgement(
            support=ClaimSupport.SUPPORTED, evidence_ids=_ids(data), explanation="Stated."
        )

    return {
        "analyst": analyst,
        "risk_assessor": risk,
        "fit_matcher": fit,
        "writer": writer,
        "critic": critic,
        "compliance": compliance,
        "verifier": verifier,
    }


def _intent(_: str, schema: type[BaseModel]) -> BaseModel:
    return schema.model_validate(
        {
            "conversation_id": "model",
            "intent": "question",
            "confidence": 0.9,
            "explanation": "The client asked something.",
        }
    )


def _reply(_: str, schema: type[BaseModel]) -> BaseModel:
    return schema.model_validate({"body": "Thanks for asking. Monday works well for a start."})


def _rewrite(_: str, schema: type[BaseModel]) -> BaseModel:
    return schema.model_validate(
        {"text": "I tuned a gradient boosted model with scikit-learn for a telecom operator."}
    )


class ScriptedLlm:
    def __init__(self, tokens_per_call: int = 10) -> None:
        self.answers = {
            **default_answers(),
            "intent": _intent,
            "client_liaison": _reply,
            "rewrite": _rewrite,
        }
        self.calls: Counter[str] = Counter()
        self.seen: defaultdict[str, list[str]] = defaultdict(list)
        self.tokens_per_call = tokens_per_call

    async def structured(
        self,
        node: str,
        instructions: str,
        data: str,
        schema: type[Any],
    ) -> LlmResult[Any]:
        self.calls[node] += 1
        self.seen[node].append(data)
        return LlmResult(self.answers[node](data, schema), self.tokens_per_call, "scripted")
