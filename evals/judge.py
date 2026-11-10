"""The rubric judge for proposal quality. Calibrate it with evals/runners/judge_calibration.py."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.guardrails import delimit_untrusted
from agent.llm import LlmGateway
from agent.prompt_loader import load_prompt


class RubricScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specificity: int = Field(ge=1, le=5)
    evidence: int = Field(ge=1, le=5)
    client_fit: int = Field(ge=1, le=5)
    clarity: int = Field(ge=1, le=5)
    overall: int = Field(ge=1, le=5)
    explanation: str


async def score_proposal(
    llm: LlmGateway,
    job: dict[str, Any],
    cover_letter: str,
) -> RubricScore:
    post = delimit_untrusted("job_post", job["title"] + "\n" + job["description"])
    data = post + "\n\n" + delimit_untrusted("proposal", cover_letter)
    result = await llm.structured("judge", load_prompt("judge"), data, RubricScore)
    return result.value
