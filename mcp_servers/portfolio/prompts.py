"""Prompt templates a freelancer would pick from an MCP host."""

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from agent.prompt_loader import load_prompt


def register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt(title="Answer a screening question from my portfolio")
    def answer_screening_question(
        question: Annotated[str, Field(description="The client's screening question")],
    ) -> str:
        """Draft a truthful answer to a screening question, grounded in portfolio evidence."""
        return load_prompt("portfolio_answer_screening").replace("{question}", question)

    @mcp.prompt(title="Prove a job requirement from my portfolio")
    def prove_requirement(
        requirement: Annotated[str, Field(description="The requirement to prove")],
    ) -> str:
        """Find the strongest evidence for one requirement, or report the gap."""
        return load_prompt("portfolio_prove_requirement").replace("{requirement}", requirement)
