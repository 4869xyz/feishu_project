"""Tests for the read-only Feishu Bitable client."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
import requests

from clients.feishu_client import (
    FeishuApiError,
    FeishuAuthenticationError,
    FeishuBitableClient,
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


def _settings(project_tmp_dir: Path) -> Settings:
    """Return complete settings with inert test identifiers."""

    return Settings(
        app_id="cli_test",
        app_secret=TEST_SECRET,
        app_token="bascn_test",
        standard_detail_table_id="tbl_detail",
        person_summary_table_id="tbl_summary",
        output_dir=project_tmp_dir / "output",
        log_dir=project_tmp_dir / "logs",
        log_level="INFO",
    )


def _client(
    project_tmp_dir: Path,
    session: Mock,
    *,
    clock: Any = None,
    max_retries: int = 0,
    sleep: Any = None,
) -> FeishuBitableClient:
    """Build a client with retries disabled unless a test opts in."""

    kwargs: dict[str, Any] = {
        "session": session,
        "max_retries": max_retries,
        "sleep": sleep or Mock(),
    }
    if clock is not None:
        kwargs["clock"] = clock
    return FeishuBitableClient(_settings(project_tmp_dir), **kwargs)


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


def test_authenticated_request_adds_required_headers(project_tmp_dir: Path) -> None:
    """The unified request method adds Bearer auth and JSON content type."""

    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        FakeResponse(
            {"code": 0, "tenant_access_token": "t-private-token", "expire": 7200}
        ),
        FakeResponse({"code": 0, "msg": "ok", "data": {"items": []}}),
    ]
    client = _client(project_tmp_dir, session)

    result = client._request("GET", "/bitable/v1/example", params={"page_size": 10})

    assert result["code"] == 0
    api_request = session.request.call_args_list[1].kwargs
    assert api_request["headers"]["Authorization"] == "Bearer t-private-token"
    assert api_request["headers"]["Content-Type"] == "application/json; charset=utf-8"
    assert api_request["timeout"] == 30.0


def test_api_business_error_contains_diagnostics_without_credentials(
    project_tmp_dir: Path,
) -> None:
    """Business errors include useful context while redacting known credentials."""

    token = "t-full-token-must-not-leak"
    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        FakeResponse({"code": 0, "tenant_access_token": token, "expire": 7200}),
        FakeResponse(
            {
                "code": 1254302,
                "msg": f"permission denied {TEST_SECRET} {token}",
                "request_id": "request-from-json",
            }
        ),
    ]
    client = _client(project_tmp_dir, session)

    with pytest.raises(FeishuApiError) as exc_info:
        client._request("GET", "/bitable/v1/forbidden")

    message = str(exc_info.value)
    assert "HTTP状态码=200" in message
    assert "code=1254302" in message
    assert "request_id=request-from-json" in message
    assert "/bitable/v1/forbidden" in message
    assert TEST_SECRET not in message
    assert token not in message


def test_http_error_is_converted_to_clear_api_error(project_tmp_dir: Path) -> None:
    """Non-retryable HTTP errors are raised immediately with Feishu details."""

    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        FakeResponse({"code": 0, "tenant_access_token": "t-token", "expire": 7200}),
        FakeResponse(
            {"code": 99991672, "msg": "Access denied"},
            status_code=403,
            headers={"X-Tt-Logid": "log-id-403"},
        ),
    ]
    client = _client(project_tmp_dir, session, max_retries=3)

    with pytest.raises(FeishuApiError) as exc_info:
        client._request("GET", "/bitable/v1/forbidden")

    message = str(exc_info.value)
    assert "HTTP状态码=403" in message
    assert "code=99991672" in message
    assert "request_id=log-id-403" in message
    assert session.request.call_count == 2


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


def test_list_fields_reads_every_page_and_preserves_raw_fields(
    project_tmp_dir: Path,
) -> None:
    """Field listing follows page tokens and returns complete raw objects."""

    session = Mock(spec=requests.Session)
    client = _client(project_tmp_dir, session)
    first_field = {
        "field_id": "fld_1",
        "field_name": "客户名称",
        "type": 1,
        "is_primary": True,
        "property": {"extra": "preserved"},
    }
    second_field = {
        "field_id": "fld_2",
        "field_name": "签约金额",
        "type": 2,
        "is_primary": False,
    }
    client._request = Mock(
        side_effect=[
            {
                "code": 0,
                "data": {
                    "items": [first_field],
                    "has_more": True,
                    "page_token": "page-2",
                },
            },
            {
                "code": 0,
                "data": {"items": [second_field], "has_more": False},
            },
        ]
    )

    assert client.list_fields("tbl_test") == [first_field, second_field]
    first_call, second_call = client._request.call_args_list
    assert first_call.args[0] == "GET"
    assert first_call.kwargs["params"] == {"page_size": 100}
    assert second_call.kwargs["params"] == {
        "page_size": 100,
        "page_token": "page-2",
    }


def test_list_fields_rejects_broken_pagination(project_tmp_dir: Path) -> None:
    """A has-more response without a token cannot create an endless loop."""

    client = _client(project_tmp_dir, Mock(spec=requests.Session))
    client._request = Mock(
        return_value={"code": 0, "data": {"items": [], "has_more": True}}
    )

    with pytest.raises(FeishuApiError, match="缺少 page_token"):
        client.list_fields("tbl_test")


def test_search_records_reads_every_page_and_requests_automatic_fields(
    project_tmp_dir: Path,
) -> None:
    """Record search follows pagination and asks Feishu for real timestamps."""

    client = _client(project_tmp_dir, Mock(spec=requests.Session))
    record_1 = {
        "record_id": "rec_1",
        "fields": {"客户": "甲"},
        "created_time": "1700000000000",
        "last_modified_time": "1700000001000",
    }
    record_2 = {"record_id": "rec_2", "fields": {"客户": "乙"}}
    client._request = Mock(
        side_effect=[
            {
                "code": 0,
                "data": {
                    "items": [record_1],
                    "has_more": True,
                    "page_token": "next-record-page",
                },
            },
            {
                "code": 0,
                "data": {"items": [record_2], "has_more": False},
            },
        ]
    )

    assert client.search_records("tbl_test", page_size=200) == [record_1, record_2]
    first_call, second_call = client._request.call_args_list
    assert first_call.args[0] == "POST"
    assert first_call.kwargs["params"] == {"page_size": 200}
    assert first_call.kwargs["json"] == {"automatic_fields": True}
    assert second_call.kwargs["params"] == {
        "page_size": 200,
        "page_token": "next-record-page",
    }


def test_search_records_honors_max_records(project_tmp_dir: Path) -> None:
    """The total limit stops reads and trims an unexpectedly large page."""

    client = _client(project_tmp_dir, Mock(spec=requests.Session))
    records = [
        {"record_id": f"rec_{index}", "fields": {}}
        for index in range(5)
    ]
    client._request = Mock(
        return_value={
            "code": 0,
            "data": {
                "items": records,
                "has_more": True,
                "page_token": "unused-next-page",
            },
        }
    )

    assert client.search_records("tbl_test", page_size=100, max_records=3) == records[:3]
    assert client._request.call_count == 1
    assert client._request.call_args.kwargs["params"] == {"page_size": 3}


def test_search_records_honors_max_records_across_pages(
    project_tmp_dir: Path,
) -> None:
    """The final page shrinks to exactly the remaining requested count."""

    client = _client(project_tmp_dir, Mock(spec=requests.Session))
    first_page = [
        {"record_id": "rec_1", "fields": {}},
        {"record_id": "rec_2", "fields": {}},
    ]
    last_record = {"record_id": "rec_3", "fields": {}}
    client._request = Mock(
        side_effect=[
            {
                "code": 0,
                "data": {
                    "items": first_page,
                    "has_more": True,
                    "page_token": "last-page",
                },
            },
            {
                "code": 0,
                "data": {
                    "items": [last_record],
                    "has_more": True,
                    "page_token": "unused-page",
                },
            },
        ]
    )

    result = client.search_records("tbl_test", page_size=2, max_records=3)

    assert result == [*first_page, last_record]
    first_call, second_call = client._request.call_args_list
    assert first_call.kwargs["params"] == {"page_size": 2}
    assert second_call.kwargs["params"] == {
        "page_size": 1,
        "page_token": "last-page",
    }


def test_retry_logs_do_not_expose_credentials(
    project_tmp_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Retry diagnostics contain only path/status metadata."""

    token = "t-private-token-for-log-test"
    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        FakeResponse({"code": 0, "tenant_access_token": token, "expire": 7200}),
        requests.Timeout(f"timeout {TEST_SECRET} {token}"),
        FakeResponse({"code": 0, "data": {"items": []}}),
    ]
    client = _client(project_tmp_dir, session, max_retries=1)

    with caplog.at_level("WARNING"):
        client._request("GET", "/bitable/v1/safe-log-test")

    assert TEST_SECRET not in caplog.text
    assert token not in caplog.text


@pytest.mark.parametrize("page_size", [0, 501])
def test_search_records_validates_page_size(
    project_tmp_dir: Path,
    page_size: int,
) -> None:
    """The client never sends a page size outside Feishu's documented range."""

    client = _client(project_tmp_dir, Mock(spec=requests.Session))

    with pytest.raises(ValueError, match="page_size"):
        client.search_records("tbl_test", page_size=page_size)


def test_search_records_zero_limit_makes_no_request(project_tmp_dir: Path) -> None:
    """A zero total limit is a valid local no-op."""

    client = _client(project_tmp_dir, Mock(spec=requests.Session))
    client._request = Mock()

    assert client.search_records("tbl_test", max_records=0) == []
    client._request.assert_not_called()
