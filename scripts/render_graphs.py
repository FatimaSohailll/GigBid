"""Render graphs from the code that runs them: python scripts/render_graphs.py"""

from pathlib import Path
from typing import Any

from agent.config import get_config
from agent.graphs.bid_graph import build_bid_graph
from agent.runtime import AgentRuntime

OUTPUT = Path("docs/images/bid_graph.mmd")


def main() -> None:
    placeholder: Any = None
    graph = build_bid_graph(AgentRuntime(get_config(), placeholder, placeholder))
    OUTPUT.write_bytes(graph.get_graph().draw_mermaid().encode())
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
