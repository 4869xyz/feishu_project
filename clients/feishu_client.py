"""Read-only client for Feishu authentication and Bitable APIs."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import requests

from config.settings import Settings


LOGGER = logging.getLogger(__name__)

DEFAULT_API_BASE_URL = "https://open.feishu.cn/open-apis"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 3
TOKEN_REFRESH_MARGIN_SECONDS = 5 * 60
RETRYABLE_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}
REQUEST_ID_HEADERS = (
    "X-Tt-Logid",
    "X-Request-Id",
    "X-Lark-Request-Id",
)
FIELD_PAGE_SIZE = 100
MAX_RECORD_PAGE_SIZE = 500


class FeishuClientError(RuntimeError):
    """Base exception raised by the Feishu client."""


class FeishuNetworkError(FeishuClientError):
    """Raised when a Feishu request cannot complete due to a network error."""


class FeishuApiError(FeishuClientError):
    """Raised when Feishu returns an HTTP or business-level error."""


class FeishuAuthenticationError(FeishuApiError):
    """Raised when tenant access token acquisition fails."""


def mask_token(token: str) -> str:
    """Return a token-safe representation suitable for terminal output."""

    if not token:
        return "***"
    if len(token) <= 12:
        return "***"
    return f"{token[:8]}...{token[-4:]}"


class FeishuBitableClient:
    """Read-only Feishu Bitable client backed by one ``requests.Session``."""

    AUTH_PATH = "/auth/v3/tenant_access_token/internal"

    def __init__(
        self,
        settings: Settings,
        *,
        session: requests.Session | None = None,
        api_base_url: str = DEFAULT_API_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Initialize the client without making a network request."""

        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        if max_retries < 0:
            raise ValueError("max_retries 不能小于 0")

        self.settings = settings
        self.session = session or requests.Session()
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._clock = clock
        self._sleep = sleep
        self._token_lock = threading.Lock()
        self._tenant_access_token: str | None = None
        self._token_expires_at = 0.0

    def _redact(self, value: str) -> str:
        """Remove known credentials from diagnostic text."""

        redacted = value
        secrets = (
            self.settings.app_secret,
            self._tenant_access_token or "",
        )
        for secret in secrets:
            if secret:
                redacted = redacted.replace(secret, "<redacted>")
        return redacted

    @staticmethod
    def _request_id(response: requests.Response, payload: dict[str, Any]) -> str | None:
        """Extract Feishu's request identifier from payload or response headers."""

        payload_request_id = payload.get("request_id")
        if payload_request_id:
            return str(payload_request_id)
        error_details = payload.get("error")
        if isinstance(error_details, dict):
            for key in ("log_id", "logid"):
                value = error_details.get(key)
                if value:
                    return str(value)
        for header in REQUEST_ID_HEADERS:
            value = response.headers.get(header)
            if value:
                return value
        return None

    def _retry_delay(self, retry_number: int) -> float:
        """Return a small capped exponential backoff delay."""

        return min(0.5 * (2**retry_number), 4.0)

    def _send_with_retries(
        self,
        method: str,
        url: str,
        *,
        path: str,
        **kwargs: Any,
    ) -> requests.Response:
        """Send an HTTP request and retry only transient failures."""

        timeout = kwargs.pop("timeout", self.timeout)
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    timeout=timeout,
                    **kwargs,
                )
            except requests.Timeout as exc:
                if attempt < self.max_retries:
                    LOGGER.warning(
                        "飞书请求超时，将重试：path=%s, retry=%d/%d",
                        path,
                        attempt + 1,
                        self.max_retries,
                    )
                    self._sleep(self._retry_delay(attempt))
                    continue
                message = self._redact(
                    f"飞书请求超时：path={path}, timeout={timeout:g}s"
                )
                raise FeishuNetworkError(message) from exc
            except (
                requests.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
            ) as exc:
                if attempt < self.max_retries:
                    LOGGER.warning(
                        "飞书网络连接失败，将重试：path=%s, retry=%d/%d",
                        path,
                        attempt + 1,
                        self.max_retries,
                    )
                    self._sleep(self._retry_delay(attempt))
                    continue
                message = self._redact(f"飞书网络连接失败：path={path}")
                raise FeishuNetworkError(message) from exc
            except requests.RequestException as exc:
                message = self._redact(
                    f"飞书 HTTP 请求失败：path={path}, error={exc}"
                )
                raise FeishuNetworkError(message) from exc

            if (
                response.status_code in RETRYABLE_HTTP_STATUSES
                and attempt < self.max_retries
            ):
                LOGGER.warning(
                    "飞书返回临时 HTTP 错误，将重试：path=%s, status=%d, retry=%d/%d",
                    path,
                    response.status_code,
                    attempt + 1,
                    self.max_retries,
                )
                response.close()
                self._sleep(self._retry_delay(attempt))
                continue
            return response

        raise AssertionError("unreachable")

    def _decode_json(
        self,
        response: requests.Response,
        *,
        path: str,
        error_class: type[FeishuApiError] = FeishuApiError,
    ) -> dict[str, Any]:
        """Decode a Feishu response and provide a bounded error for non-JSON data."""

        try:
            payload = response.json()
        except ValueError as exc:
            excerpt = self._redact(response.text[:300])
            request_id = self._request_id(response, {}) or "unknown"
            message = (
                "飞书返回非 JSON 内容："
                f"HTTP状态码={response.status_code}, path={path}, "
                f"request_id={request_id}, response={excerpt!r}"
            )
            raise error_class(message) from exc

        if not isinstance(payload, dict):
            request_id = self._request_id(response, {}) or "unknown"
            raise error_class(
                "飞书响应 JSON 结构无效："
                f"HTTP状态码={response.status_code}, request_id={request_id}, "
                f"path={path}"
            )
        return payload

    def _raise_api_error(
        self,
        response: requests.Response,
        payload: dict[str, Any],
        *,
        path: str,
        prefix: str,
        error_class: type[FeishuApiError] = FeishuApiError,
    ) -> None:
        """Raise a credential-safe exception with Feishu diagnostics."""

        code = payload.get("code")
        msg = self._redact(str(payload.get("msg", "")))
        request_id = self._request_id(response, payload) or "unknown"
        message = self._redact(
            f"{prefix}：HTTP状态码={response.status_code}, code={code}, "
            f"msg={msg}, request_id={request_id}, path={path}"
        )
        raise error_class(message)

    def get_tenant_access_token(self) -> str:
        """Return a cached tenant token, refreshing it five minutes before expiry."""

        with self._token_lock:
            now = self._clock()
            if (
                self._tenant_access_token is not None
                and now < self._token_expires_at - TOKEN_REFRESH_MARGIN_SECONDS
            ):
                return self._tenant_access_token

            response = self._send_with_retries(
                "POST",
                f"{self.api_base_url}{self.AUTH_PATH}",
                path=self.AUTH_PATH,
                headers={"Content-Type": "application/json; charset=utf-8"},
                json={
                    "app_id": self.settings.app_id,
                    "app_secret": self.settings.app_secret,
                },
            )
            payload = self._decode_json(
                response,
                path=self.AUTH_PATH,
                error_class=FeishuAuthenticationError,
            )

            if not response.ok or payload.get("code") != 0:
                self._raise_api_error(
                    response,
                    payload,
                    path=self.AUTH_PATH,
                    prefix="飞书鉴权失败，请检查 App ID、App Secret 和应用状态",
                    error_class=FeishuAuthenticationError,
                )

            token = payload.get("tenant_access_token")
            expire = payload.get("expire")
            if not isinstance(token, str) or not token:
                raise FeishuAuthenticationError(
                    f"飞书鉴权响应缺少 tenant_access_token：path={self.AUTH_PATH}"
                )
            if not isinstance(expire, (int, float)) or isinstance(expire, bool) or expire <= 0:
                raise FeishuAuthenticationError(
                    f"飞书鉴权响应中的 expire 无效：path={self.AUTH_PATH}"
                )

            self._tenant_access_token = token
            self._token_expires_at = self._clock() + float(expire)
            return token

    def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send an authenticated request and validate Feishu's response.

        The method is intentionally private because this project exposes only
        explicit read operations. It never accepts a caller-provided
        ``Authorization`` header.
        """

        normalized_path = path if path.startswith("/") else f"/{path}"
        token = self.get_tenant_access_token()
        custom_headers = kwargs.pop("headers", {}) or {}
        headers = {
            **custom_headers,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }

        response = self._send_with_retries(
            method.upper(),
            f"{self.api_base_url}{normalized_path}",
            path=normalized_path,
            headers=headers,
            **kwargs,
        )
        payload = self._decode_json(response, path=normalized_path)
        if not response.ok or payload.get("code") != 0:
            self._raise_api_error(
                response,
                payload,
                path=normalized_path,
                prefix="飞书 API 请求失败",
            )
        return payload

    @staticmethod
    def _response_data(payload: dict[str, Any], *, path: str) -> dict[str, Any]:
        """Return a validated Feishu ``data`` object."""

        data = payload.get("data")
        if not isinstance(data, dict):
            raise FeishuApiError(f"飞书响应缺少有效 data 对象：path={path}")
        return data

    @staticmethod
    def _response_items(data: dict[str, Any], *, path: str) -> list[dict[str, Any]]:
        """Return a validated page of item dictionaries."""

        items = data.get("items", [])
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise FeishuApiError(f"飞书响应中的 items 结构无效：path={path}")
        return items

    @staticmethod
    def _next_page_token(
        data: dict[str, Any],
        *,
        path: str,
        seen_tokens: set[str],
    ) -> str | None:
        """Validate pagination metadata and guard against endless loops."""

        if not data.get("has_more", False):
            return None

        page_token = data.get("page_token")
        if not isinstance(page_token, str) or not page_token:
            raise FeishuApiError(
                f"飞书响应指示仍有下一页，但缺少 page_token：path={path}"
            )
        if page_token in seen_tokens:
            raise FeishuApiError(
                f"飞书分页返回了重复 page_token，已停止读取：path={path}"
            )
        seen_tokens.add(page_token)
        return page_token

    def _table_path(self, table_id: str, suffix: str) -> str:
        """Build an encoded Bitable path for a configured Base and table."""

        normalized_table_id = table_id.strip()
        if not normalized_table_id:
            raise ValueError("table_id 不能为空")
        app_token = quote(self.settings.app_token, safe="")
        encoded_table_id = quote(normalized_table_id, safe="")
        return f"/bitable/v1/apps/{app_token}/tables/{encoded_table_id}/{suffix}"

    def list_fields(self, table_id: str) -> list[dict[str, Any]]:
        """Read every field from a Bitable table using pagination."""

        path = self._table_path(table_id, "fields")
        fields: list[dict[str, Any]] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()

        while True:
            params: dict[str, Any] = {"page_size": FIELD_PAGE_SIZE}
            if page_token is not None:
                params["page_token"] = page_token

            payload = self._request("GET", path, params=params)
            data = self._response_data(payload, path=path)
            fields.extend(self._response_items(data, path=path))
            page_token = self._next_page_token(
                data,
                path=path,
                seen_tokens=seen_tokens,
            )
            if page_token is None:
                return fields

    def search_records(
        self,
        table_id: str,
        page_size: int = 100,
        max_records: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read Bitable records with pagination and an optional total limit.

        The official search endpoint permits at most 500 records per page.
        ``automatic_fields`` is enabled so Feishu can return its real
        ``created_time`` and ``last_modified_time`` properties when available.
        """

        if isinstance(page_size, bool) or not isinstance(page_size, int):
            raise TypeError("page_size 必须是整数")
        if not 1 <= page_size <= MAX_RECORD_PAGE_SIZE:
            raise ValueError(
                f"page_size 必须在 1 到 {MAX_RECORD_PAGE_SIZE} 之间"
            )
        if max_records is not None:
            if isinstance(max_records, bool) or not isinstance(max_records, int):
                raise TypeError("max_records 必须是整数或 None")
            if max_records < 0:
                raise ValueError("max_records 不能小于 0")

        path = self._table_path(table_id, "records/search")
        if max_records == 0:
            return []

        records: list[dict[str, Any]] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()

        while True:
            remaining = (
                None if max_records is None else max_records - len(records)
            )
            current_page_size = (
                page_size if remaining is None else min(page_size, remaining)
            )
            params: dict[str, Any] = {"page_size": current_page_size}
            if page_token is not None:
                params["page_token"] = page_token

            payload = self._request(
                "POST",
                path,
                params=params,
                json={"automatic_fields": True},
            )
            data = self._response_data(payload, path=path)
            page_items = self._response_items(data, path=path)
            if remaining is not None:
                page_items = page_items[:remaining]
            records.extend(page_items)

            if max_records is not None and len(records) >= max_records:
                return records

            page_token = self._next_page_token(
                data,
                path=path,
                seen_tokens=seen_tokens,
            )
            if page_token is None:
                return records
