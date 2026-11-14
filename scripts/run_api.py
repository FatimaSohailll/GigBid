"""Run the API locally, on Windows too: uv run --locked --env-file .env python -m scripts.run_api

Postgres's async driver needs the selector event loop on Windows, which `uvicorn --reload` does
not set, so the server is started here instead.
"""

import asyncio
import sys

import uvicorn


def main() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    from apps.api.main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=8000, loop="none")
    asyncio.run(uvicorn.Server(config).serve())


if __name__ == "__main__":
    main()
