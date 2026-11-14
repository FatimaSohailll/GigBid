"""Application logs: personal data masked, and every line tagged with the GigBid trace id.

Masking happens when a log record is created, so it covers GigBid's own loggers and every
library's (uvicorn, arq, httpx, the MCP SDK) without each of them opting in. Traces are masked
separately, by the LangSmith client (agent/tracing.py).
"""

import logging
from typing import Any

from agent.guardrails import mask_pii
from agent.marketplace_port import current_trace_id

FORMAT = "%(asctime)s %(levelname)s %(name)s [trace %(trace_id)s] %(message)s"
_installed = False


def _masked(value: Any) -> Any:
    return mask_pii(value) if isinstance(value, str | dict | list) else value


def install_log_masking() -> None:
    """Wrap the record factory once per process. Safe to call more than once."""
    global _installed
    if _installed:
        return
    original = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = original(*args, **kwargs)
        if isinstance(record.msg, str):
            record.msg = mask_pii(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_masked(item) for item in record.args)
        elif isinstance(record.args, dict):
            record.args = mask_pii(record.args)
        record.trace_id = current_trace_id.get() or "-"
        return record

    logging.setLogRecordFactory(factory)
    _installed = True


def configure_logging(level: int = logging.INFO) -> None:
    """Masking plus one line format with the trace id. For processes GigBid starts itself."""
    install_log_masking()
    logging.basicConfig(level=level, format=FORMAT)
