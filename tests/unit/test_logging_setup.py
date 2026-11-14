import logging

import pytest

from agent.logging_setup import install_log_masking
from agent.marketplace_port import current_trace_id


@pytest.fixture(autouse=True)
def masking() -> None:
    install_log_masking()


def test_personal_data_is_masked_in_the_message_and_its_arguments(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("gigbid.test")
    with caplog.at_level(logging.INFO):
        logger.info("Reply from %s, call +1 415 555 0100", "client@example.com")
        logger.info("Card 4111 1111 1111 1111 in a draft")
    text = caplog.text
    assert "client@example.com" not in text and "[email]" in text
    assert "555 0100" not in text and "[phone]" in text
    assert "4111 1111 1111 1111" not in text and "[payment]" in text


def test_a_library_logger_is_masked_too(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        logging.getLogger("httpx").info("POST https://example.test?to=jane.doe@example.com")
    assert "jane.doe@example.com" not in caplog.text


def test_each_record_carries_the_current_trace_id(caplog: pytest.LogCaptureFixture) -> None:
    token = current_trace_id.set("a" * 32)
    try:
        with caplog.at_level(logging.INFO):
            logging.getLogger("gigbid.test").info("inside a request")
    finally:
        current_trace_id.reset(token)
    assert caplog.records[-1].trace_id == "a" * 32
