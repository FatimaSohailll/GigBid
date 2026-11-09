"""C6: durability across real process deaths, with a real Postgres checkpointer."""

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from tests.support.graph_stack import GraphStack, start_stack

DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL is not set")
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[GraphStack]:
    started = start_stack(tmp_path)
    yield started
    started.stop()


def run_process(
    stack: GraphStack,
    command: str,
    thread: str,
    **extra_env: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "tests/integration/bid_process.py"), command, thread],
        cwd=ROOT,
        env={
            **os.environ,
            **stack.env,
            "PYTHONPATH": str(ROOT),
            "TEST_DATABASE_URL": str(DATABASE_URL),
            "LANGGRAPH_STRICT_MSGPACK": "true",
            **extra_env,
        },
        capture_output=True,
        text=True,
        timeout=120,
    )


def get(stack: GraphStack, path: str) -> list[dict] | dict:
    response = httpx.get(
        f"{stack.simulator.url}{path}",
        headers={"Authorization": "Bearer sim-ai"},
    )
    return response.json()


def test_pending_approval_survives_process_death(stack: GraphStack) -> None:
    thread = f"crash-{uuid.uuid4().hex}"
    started = run_process(stack, "start", thread)
    assert "STATUS=awaiting_approval" in started.stdout, started.stderr
    assert get(stack, "/v1/proposals") == []

    # The first process has exited. A new one, with no shared memory, resumes the thread.
    approved = run_process(stack, "approve", thread)
    assert "STATUS=submitted" in approved.stdout, approved.stderr
    assert get(stack, "/v1/connects/balance")["spent"] == 8
    assert len(get(stack, "/v1/proposals")) == 1


def test_crash_after_submit_before_checkpoint_still_submits_once(stack: GraphStack) -> None:
    thread = f"crash-{uuid.uuid4().hex}"
    run_process(stack, "start", thread)

    crashed = run_process(stack, "approve", thread, CRASH_AFTER_SUBMIT="1")
    assert crashed.returncode == 9, crashed.stderr
    assert get(stack, "/v1/connects/balance")["spent"] == 8  # the marketplace already applied it

    resumed = run_process(stack, "continue", thread)
    assert "STATUS=submitted" in resumed.stdout, resumed.stderr
    assert get(stack, "/v1/connects/balance")["spent"] == 8
    proposals = get(stack, "/v1/proposals")
    assert [proposal["status"] for proposal in proposals] == ["submitted"]
    replays = [row for row in stack.store.audit_rows() if row["tool"] == "submit_proposal"]
    assert len(replays) == 2 and '"replayed": true' in replays[1]["result"]
