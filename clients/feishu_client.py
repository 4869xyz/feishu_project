"""Feishu client for authentication and IM message resource downloads."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
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
DOWNLOAD_CHUNK_SIZE = 64 * 1024


class FeishuClientError(RuntimeError):
    """Base exception raised by the Feishu client."""


class FeishuNetworkError(FeishuClientError):
    """Raised when a Feishu request cannot complete due to a network error."""


class FeishuApiError(FeishuClientError):
    """Raised when Feishu returns an HTTP or business-level error."""


class FeishuAuthenticationError(FeishuApiError):
    """Raised when tenant access token acquisition fails."""


class FeishuFileDownloadError(FeishuApiError):
    """Raised when a file attached to an IM message cannot be downloaded."""


def mask_token(token: str) -> str:
    """Return a token-safe representation suitable for terminal output."""

    if not token:
        return "***"
    if len(token) <= 12:
        return "***"
    return f"{token[:8]}...{token[-4:]}"


class FeishuClient:
    """Feishu HTTP client for auth and message attachment downloads."""

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

    def download_message_resource(
        self,
        message_id: str,
        file_key: str,
        destination: str | Path,
        *,
        max_bytes: int,
    ) -> int:
        """Download one file-message resource atomically and return its byte size.

        The Feishu IM resource endpoint returns a binary stream rather than a
        JSON envelope. Download to a sibling ``.part`` file first so callers
        never mistake an interrupted transfer for a complete Excel workbook.
        """

        normalized_message_id = message_id.strip()
        normalized_file_key = file_key.strip()
        if not normalized_message_id:
            raise ValueError("message_id 不能为空")
        if not normalized_file_key:
            raise ValueError("file_key 不能为空")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes 必须是正整数")

        path = (
            "/im/v1/messages/"
            f"{quote(normalized_message_id, safe='')}/resources/"
            f"{quote(normalized_file_key, safe='')}"
        )
        token = self.get_tenant_access_token()
        response = self._send_with_retries(
            "GET",
            f"{self.api_base_url}{path}",
            path=path,
            headers={"Authorization": f"Bearer {token}"},
            params={"type": "file"},
            stream=True,
        )

        if not response.ok:
            try:
                payload = self._decode_json(
                    response,
                    path=path,
                    error_class=FeishuFileDownloadError,
                )
                self._raise_api_error(
                    response,
                    payload,
                    path=path,
                    prefix="飞书文件下载失败",
                    error_class=FeishuFileDownloadError,
                )
            finally:
                response.close()

        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = None
            if declared_size is not None and declared_size > max_bytes:
                response.close()
                raise FeishuFileDownloadError(
                    f"文件大小超过本地限制：size={declared_size}, max_bytes={max_bytes}"
                )

        target = Path(destination).resolve()
        temporary = target.with_name(f"{target.name}.part")
        bytes_written = 0
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary.unlink(missing_ok=True)
            with temporary.open("wb") as output:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if not chunk:
                        continue
                    bytes_written += len(chunk)
                    if bytes_written > max_bytes:
                        raise FeishuFileDownloadError(
                            "文件大小超过本地限制："
                            f"max_bytes={max_bytes}"
                        )
                    output.write(chunk)
            os.replace(temporary, target)
            return bytes_written
        except (OSError, requests.RequestException) as exc:
            temporary.unlink(missing_ok=True)
            message = self._redact(
                f"飞书文件下载失败：path={path}, error={exc}"
            )
            raise FeishuFileDownloadError(message) from exc
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            response.close()
