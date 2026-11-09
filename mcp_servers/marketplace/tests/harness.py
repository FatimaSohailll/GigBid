"""Shared helpers: tests run against real DevSim, reached through ASGI or real sockets."""

import socket
import threading
import time
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI

from agent.config import get_config
from devsim.app import create_app
from devsim.config import FaultConfig, RateLimitConfig, Settings, SimConfig
from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter, PerUserRateLimiter

TOKENS = {"sim-ai": "ai_ml_engineer", "sim-web": "fullstack_developer"}


def devsim_app(faults: FaultConfig | None = None) -> FastAPI:
    settings = Settings(
        config=SimConfig(
            start_time="2026-11-01T00:00:00Z",
            rate_limit=RateLimitConfig(requests_per_minute=100_000),
            faults=faults or FaultConfig(),
        ),
        api_tokens=TOKENS,
        admin_token="sim-admin",
    )
    return create_app(settings)


async def no_sleep(_: float) -> None:
    return None


def adapter(app: FastAPI, token: str = "sim-ai") -> GigSimAdapter:
    config = get_config().marketplace
    return GigSimAdapter(
        "http://devsim",
        token,
        TOKENS[token],
        config,
        PerUserRateLimiter(100_000, config.max_rate_limit_wait_seconds),
        transport=httpx.ASGITransport(app=app),
        sleep=no_sleep,
    )


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class LiveServer:
    """Runs an ASGI app on a real socket in a background thread."""

    def __init__(self, app: Any, port: int) -> None:
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self._app = app
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        config = uvicorn.Config(self._app, host="127.0.0.1", port=self.port, log_level="error")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 15
        while not self._server.started:
            if time.monotonic() > deadline:
                raise TimeoutError("Server did not start")
            time.sleep(0.02)

    def stop(self) -> None:
        assert self._server is not None and self._thread is not None
        self._server.should_exit = True
        self._thread.join(timeout=10)
