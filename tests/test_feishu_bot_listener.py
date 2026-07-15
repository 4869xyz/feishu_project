"""Tests for listener-level replies around table-link export outcomes."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from clients.feishu_table_export import WikiTablePermissionError
from feishu_bot_listener import (
    CredentialSafeFormatter,
    SingleInstanceError,
    WIKI_PERMISSION_REPLY,
    _configure_logging,
    _create_channel,
    _single_instance_lock,
    handle_message,
)


class StubAttachmentDownloader:
    """Return no attachment so the listener reaches the link exporter."""

    def download_from_message(self, message: object) -> None:
        return None


class PermissionDeniedExporter:
    """Make the Wiki export branch deterministic without an HTTP client."""

    def export_from_message(self, message: object) -> None:
        raise WikiTablePermissionError


class RecordingChannel:
    """Capture replies sent by the listener."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], dict[str, str]]] = []

    async def send(
        self, chat_id: str, content: dict[str, str], options: dict[str, str]
    ) -> None:
        self.calls.append((chat_id, content, options))


def test_wiki_permission_reply_is_explicit() -> None:
    """Users receive the prescribed two-layer Wiki permission guidance."""

    channel = RecordingChannel()
    message = SimpleNamespace(chat_id="oc_chat", message_id="om_message")

    asyncio.run(
        handle_message(
            channel,
            StubAttachmentDownloader(),
            PermissionDeniedExporter(),
            message,
            asyncio.Lock(),
        )
    )

    assert channel.calls == [
        (
            "oc_chat",
            {"text": WIKI_PERMISSION_REPLY},
            {"reply_to": "om_message"},
        )
    ]


def test_channel_policy_requires_direct_bot_mention_in_groups() -> None:
    """Group messages are admitted only when they mention this bot directly."""

    settings = SimpleNamespace(app_id="cli_test", app_secret="test-secret")

    policy = _create_channel(settings).get_policy()

    assert policy.dm_policy == "open"
    assert policy.group_policy == "open"
    assert policy.require_mention is True
    assert policy.respond_to_mention_all is False


def test_credential_safe_formatter_redacts_websocket_credentials() -> None:
    """Connection URLs never expose temporary access keys or tickets."""

    formatter = CredentialSafeFormatter("%(message)s")
    record = logging.LogRecord(
        "Lark",
        logging.INFO,
        __file__,
        1,
        "connected to wss://example/ws?access_key=secret-key&ticket=secret-ticket&aid=1",
        (),
        None,
    )

    formatted = formatter.format(record)

    assert "secret-key" not in formatted
    assert "secret-ticket" not in formatted
    assert "access_key=***" in formatted
    assert "ticket=***" in formatted


def test_logging_uses_one_root_pipeline_for_lark(project_tmp_dir) -> None:
    """The SDK logger has no duplicate handler and suppresses URL-bearing INFO."""

    root_logger = logging.getLogger()
    lark_logger = logging.getLogger("Lark")
    httpx_logger = logging.getLogger("httpx")
    old_root_handlers = list(root_logger.handlers)
    old_root_level = root_logger.level
    old_lark_handlers = list(lark_logger.handlers)
    old_lark_level = lark_logger.level
    old_lark_propagate = lark_logger.propagate
    old_httpx_level = httpx_logger.level
    settings = SimpleNamespace(log_level="DEBUG", log_dir=project_tmp_dir)

    try:
        _configure_logging(settings)

        assert lark_logger.handlers == []
        assert lark_logger.propagate is True
        assert lark_logger.level == logging.WARNING
        assert httpx_logger.level == logging.WARNING
    finally:
        configured_handlers = list(root_logger.handlers)
        root_logger.handlers = old_root_handlers
        root_logger.setLevel(old_root_level)
        for handler in configured_handlers:
            handler.close()
        lark_logger.handlers = old_lark_handlers
        lark_logger.setLevel(old_lark_level)
        lark_logger.propagate = old_lark_propagate
        httpx_logger.setLevel(old_httpx_level)


def test_single_instance_lock_rejects_second_owner(project_tmp_dir) -> None:
    """A second listener cannot start while the first holds the lock."""

    lock_path = project_tmp_dir / "listener.lock"

    with _single_instance_lock(lock_path):
        with pytest.raises(SingleInstanceError, match="已经在运行"):
            with _single_instance_lock(lock_path):
                pytest.fail("second listener unexpectedly acquired the lock")

    with _single_instance_lock(lock_path):
        pass
