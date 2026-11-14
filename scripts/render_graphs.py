"""Render graphs from the code that runs them: python -m scripts.render_graphs"""

from pathlib import Path
from typing import Any

from agent.config import get_config
from agent.graphs.bid_graph import build_bid_graph
from agent.graphs.conversation_graph import build_conversation_graph
from agent.runtime import AgentRuntime

PAGE = Path("docs/GRAPHS.md")


def main() -> None:
    placeholder: Any = None
    runtime = AgentRuntime(get_config(), placeholder, placeholder, placeholder)
    sections = [
        "# GigBid graphs\n",
        "Generated from the compiled graphs by `python -m scripts.render_graphs`. Do not edit.\n",
    ]
    for name, title, build in (
        ("bid_graph", "Bid graph: one thread per job", build_bid_graph),
        (
            "conversation_graph",
            "Conversation graph: one thread per conversation",
            build_conversation_graph,
        ),
    ):
        diagram = build(runtime).get_graph().draw_mermaid()
        output = Path("docs/images") / f"{name}.mmd"
        output.write_bytes(diagram.encode())
        sections.append(f"## {title}\n\n```mermaid\n{diagram.strip()}\n```\n")
        print(f"Wrote {output}")
    PAGE.write_bytes("\n".join(sections).encode())
    print(f"Wrote {PAGE}")


if __name__ == "__main__":
    main()
