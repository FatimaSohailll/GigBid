"""Render graphs from the code that runs them: python -m scripts.render_graphs"""

from pathlib import Path
from typing import Any

from agent.config import get_config
from agent.graphs.bid_graph import build_bid_graph
from agent.graphs.conversation_graph import build_conversation_graph
from agent.runtime import AgentRuntime


def main() -> None:
    placeholder: Any = None
    runtime = AgentRuntime(get_config(), placeholder, placeholder, placeholder)
    for name, build in (
        ("bid_graph", build_bid_graph),
        ("conversation_graph", build_conversation_graph),
    ):
        output = Path("docs/images") / f"{name}.mmd"
        output.write_bytes(build(runtime).get_graph().draw_mermaid().encode())
        print(f"Wrote {output}")


if __name__ == "__main__":
    main()
