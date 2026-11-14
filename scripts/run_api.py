"""Run the API locally, on Windows too: uv run --locked --env-file .env python -m scripts.run_api

Postgres's async driver needs the selector event loop on Windows, which `uvicorn --reload` does
not set, so the server is started here instead.
"""

import asyncio
import os
import sys

import uvicorn


def main() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    from agent.logging_setup import configure_logging
    from apps.api.main import app

    configure_logging()

    host = os.environ.get("API_HOST", "127.0.0.1")  # 0.0.0.0 inside a container
    port = int(os.environ.get("API_PORT", "8000"))
    config = uvicorn.Config(app, host=host, port=port, loop="none")
    asyncio.run(uvicorn.Server(config).serve())


if __name__ == "__main__":
    main()
