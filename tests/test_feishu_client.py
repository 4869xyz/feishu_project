"""Tests for Feishu authentication and message resource downloads."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
import requests

from clients.feishu_client import (
    FeishuAuthenticationError,
    FeishuClient,
    FeishuFileDownloadError,
    FeishuNetworkError,
    mask_token,
)
from config.settings import Settings


TEST_SECRET = "test-app-secret-never-log"


class FakeResponse:
    """Small requests.Response stand-in for deterministic client tests."""

    def __init__(
        self,
        payload: Any,
        *,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        text: str = "",
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})
        self.text = text
        self.closed = False

    @property
    def ok(self) -> bool:
        """Match requests' success predicate."""

        return 200 <= self.status_code < 400

    def json(self) -> Any:
        """Return the configured JSON payload or raise it if it is an exception."""

        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload

    def close(self) -> None:
        """Record that the response was closed before a retry."""

        self.closed = True


class FakeStreamingResponse(FakeResponse):
    """Response stand-in that yields deterministic binary chunks."""

    def __init__(self, chunks: list[bytes], **kwargs: Any) -> None:
        super().__init__({}, **kwargs)
        self.chunks = chunks

    def iter_content(self, chunk_size: int) -> list[bytes]:
        assert chunk_size > 0
        return self.chunks


def _settings(project_tmp_dir: Path) -> Settings:
    """Return complete settings with inert test identifiers."""

    return Settings(
        app_id="cli_test",
        app_secret=TEST_SECRET,
        log_dir=project_tmp_dir / "logs",
        inbox_dir=project_tmp_dir / "data" / "inbox",
        max_download_bytes=1024,
        log_level="INFO",
    )


def _client(
    project_tmp_dir: Path,
    session: Mock,
    *,
    clock: Any = None,
    max_retries: int = 0,
    sleep: Any = None,
) -> FeishuClient:
    """Build a client with retries disabled unless a test opts in."""

    kwargs: dict[str, Any] = {
        "session": session,
        "max_retries": max_retries,
        "sleep": sleep or Mock(),
    }
    if clock is not None:
        kwargs["clock"] = clock
    return FeishuClient(_settings(project_tmp_dir), **kwargs)


def test_get_tenant_access_token_success(project_tmp_dir: Path) -> None:
    """A valid authentication response returns its tenant token."""

    session = Mock(spec=requests.Session)
    session.request.return_value = FakeResponse(
        {"code": 0, "msg": "ok", "tenant_access_token": "t-token-1", "expire": 7200}
    )
    client = _client(project_tmp_dir, session)

    assert client.get_tenant_access_token() == "t-token-1"
    request = session.request.call_args.kwargs
    assert request["method"] == "POST"
    assert request["json"] == {
        "app_id": "cli_test",
        "app_secret": TEST_SECRET,
    }
    assert "Authorization" not in request["headers"]


def test_tenant_access_token_is_reused_while_valid(project_tmp_dir: Path) -> None:
    """Repeated calls reuse the in-process token before the refresh margin."""

    now = [1000.0]
    session = Mock(spec=requests.Session)
    session.request.return_value = FakeResponse(
        {"code": 0, "msg": "ok", "tenant_access_token": "t-cached", "expire": 7200}
    )
    client = _client(project_tmp_dir, session, clock=lambda: now[0])

    assert client.get_tenant_access_token() == "t-cached"
    now[0] += 600
    assert client.get_tenant_access_token() == "t-cached"
    assert session.request.call_count == 1


def test_download_message_resource_streams_atomically(project_tmp_dir: Path) -> None:
    """Message resources are streamed into a completed file only after success."""

    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        FakeResponse(
            {"code": 0, "tenant_access_token": "t-download-token", "expire": 7200}
        ),
        FakeStreamingResponse([b"first-", b"second"], headers={"Content-Length": "12"}),
    ]
    client = _client(project_tmp_dir, session)
    destination = project_tmp_dir / "inbox" / "report.xlsx"

    result = client.download_message_resource(
        "om_test",
        "file_test",
        destination,
        max_bytes=100,
    )

    assert result == 12
    assert destination.read_bytes() == b"first-second"
    assert not destination.with_name("report.xlsx.part").exists()
    request = session.request.call_args_list[1].kwargs
    assert request["method"] == "GET"
    assert request["params"] == {"type": "file"}
    assert request["stream"] is True
    assert request["headers"]["Authorization"] == "Bearer t-download-token"


def test_download_message_resource_rejects_declared_oversize_file(
    project_tmp_dir: Path,
) -> None:
    """A too-large resource never creates a partial file in the inbox."""

    session = Mock(spec=requests.Session)
    stream_response = FakeStreamingResponse([b"unused"], headers={"Content-Length": "101"})
    session.request.side_effect = [
        FakeResponse(
            {"code": 0, "tenant_access_token": "t-download-token", "expire": 7200}
        ),
        stream_response,
    ]
    client = _client(project_tmp_dir, session)
    destination = project_tmp_dir / "inbox" / "too-large.xlsx"

    with pytest.raises(FeishuFileDownloadError, match="超过本地限制"):
        client.download_message_resource("om_test", "file_test", destination, max_bytes=100)

    assert not destination.exists()
    assert not destination.with_name("too-large.xlsx.part").exists()
    assert stream_response.closed is True


def test_download_message_resource_reports_api_error_without_credentials(
    project_tmp_dir: Path,
) -> None:
    """Failed downloads surface Feishu diagnostics without leaking secrets."""

    token = "t-full-token-must-not-leak"
    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        FakeResponse({"code": 0, "tenant_access_token": token, "expire": 7200}),
        FakeResponse(
            {
                "code": 99991672,
                "msg": f"Access denied {TEST_SECRET} {token}",
            },
            status_code=403,
            headers={"X-Tt-Logid": "log-id-403"},
        ),
    ]
    client = _client(project_tmp_dir, session)
    destination = project_tmp_dir / "inbox" / "denied.xlsx"

    with pytest.raises(FeishuFileDownloadError) as exc_info:
        client.download_message_resource(
            "om_test",
            "file_test",
            destination,
            max_bytes=100,
        )

    message = str(exc_info.value)
    assert "HTTP状态码=403" in message
    assert "code=99991672" in message
    assert "request_id=log-id-403" in message
    assert TEST_SECRET not in message
    assert token not in message
    assert not destination.exists()


def test_tenant_access_token_refreshes_before_expiry(project_tmp_dir: Path) -> None:
    """A token inside the five-minute margin is fetched again."""

    now = [1000.0]
    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        FakeResponse(
            {"code": 0, "msg": "ok", "tenant_access_token": "t-old", "expire": 7200}
        ),
        FakeResponse(
            {"code": 0, "msg": "ok", "tenant_access_token": "t-new", "expire": 7200}
        ),
    ]
    client = _client(project_tmp_dir, session, clock=lambda: now[0])

    assert client.get_tenant_access_token() == "t-old"
    now[0] = 1000.0 + 7200 - 299
    assert client.get_tenant_access_token() == "t-new"
    assert session.request.call_count == 2


def test_authentication_business_error_is_clear_and_safe(
    project_tmp_dir: Path,
) -> None:
    """A non-zero Feishu code raises diagnostics without leaking the secret."""

    session = Mock(spec=requests.Session)
    session.request.return_value = FakeResponse(
        {"code": 10003, "msg": f"invalid secret {TEST_SECRET}"},
        headers={"X-Tt-Logid": "req-test"},
    )
    client = _client(project_tmp_dir, session)

    with pytest.raises(FeishuAuthenticationError) as exc_info:
        client.get_tenant_access_token()

    message = str(exc_info.value)
    assert "鉴权失败" in message
    assert "code=10003" in message
    assert "request_id=req-test" in message
    assert TEST_SECRET not in message


def test_mask_token_never_reveals_short_values() -> None:
    """Even malformed short token values are fully hidden."""

    assert mask_token("x") == "***"
    assert mask_token("short-token") == "***"
    assert mask_token("t-long-token-value") == "t-long-t...alue"


def test_timeout_is_retried_at_most_configured_times(project_tmp_dir: Path) -> None:
    """Transient timeouts are bounded and become an explicit network error."""

    session = Mock(spec=requests.Session)
    session.request.side_effect = requests.Timeout("timed out")
    sleep = Mock()
    client = _client(
        project_tmp_dir,
        session,
        max_retries=3,
        sleep=sleep,
    )

    with pytest.raises(FeishuNetworkError, match="请求超时"):
        client.get_tenant_access_token()

    assert session.request.call_count == 4
    assert sleep.call_count == 3


@pytest.mark.parametrize("status_code", [408, 429, 500, 503])
def test_transient_http_error_is_retried_then_succeeds(
    project_tmp_dir: Path,
    status_code: int,
) -> None:
    """Documented transient HTTP classes are retried with a strict bound."""

    transient_response = FakeResponse(
        {"code": 1, "msg": "temporary"},
        status_code=status_code,
    )
    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        transient_response,
        FakeResponse(
            {"code": 0, "tenant_access_token": "t-recovered", "expire": 7200}
        ),
    ]
    sleep = Mock()
    client = _client(
        project_tmp_dir,
        session,
        max_retries=1,
        sleep=sleep,
    )

    assert client.get_tenant_access_token() == "t-recovered"
    assert transient_response.closed is True
    sleep.assert_called_once()


def test_chunked_response_failure_is_retried(project_tmp_dir: Path) -> None:
    """A response stream cut off mid-transfer is treated as transient."""

    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        requests.exceptions.ChunkedEncodingError("stream ended"),
        FakeResponse(
            {"code": 0, "tenant_access_token": "t-recovered", "expire": 7200}
        ),
    ]
    client = _client(project_tmp_dir, session, max_retries=1)

    assert client.get_tenant_access_token() == "t-recovered"
    assert session.request.call_count == 2


def test_non_json_error_has_bounded_response_excerpt(project_tmp_dir: Path) -> None:
    """Non-JSON responses report only a bounded, credential-safe excerpt."""

    long_body = f"upstream error {TEST_SECRET} " + ("x" * 500)
    session = Mock(spec=requests.Session)
    session.request.return_value = FakeResponse(
        ValueError("not json"),
        status_code=502,
        headers={"X-Tt-Logid": "non-json-log-id"},
        text=long_body,
    )
    client = _client(project_tmp_dir, session)

    with pytest.raises(FeishuAuthenticationError) as exc_info:
        client.get_tenant_access_token()

    message = str(exc_info.value)
    assert "非 JSON" in message
    assert "request_id=non-json-log-id" in message
    assert TEST_SECRET not in message
    assert len(message) < 500


def test_retry_logs_do_not_expose_credentials(
    project_tmp_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Retry diagnostics contain only path/status metadata."""

    token = "t-private-token-for-log-test"
    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        requests.Timeout(f"timeout {TEST_SECRET} {token}"),
        FakeResponse({"code": 0, "tenant_access_token": token, "expire": 7200}),
    ]
    client = _client(project_tmp_dir, session, max_retries=1)

    with caplog.at_level("WARNING"):
        assert client.get_tenant_access_token() == token

    assert TEST_SECRET not in caplog.text
    assert token not in caplog.text
