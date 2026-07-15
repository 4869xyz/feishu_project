"""Feishu client for authentication and IM message resource downloads."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
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
EXPORT_TASK_POLL_INTERVAL_SECONDS = 1.0
EXPORT_TASK_TIMEOUT_SECONDS = 90.0
EXPORTABLE_DOCUMENT_TYPES = frozenset({"sheet", "bitable"})
PERMISSION_ERROR_CODES = frozenset({1069902, 99991672})


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


class FeishuPermissionError(FeishuApiError):
    """Raised when the current application cannot access a Feishu resource."""


class FeishuDocumentExportError(FeishuApiError):
    """Raised when a cloud document cannot be exported as an Excel workbook."""


@dataclass(frozen=True, slots=True)
class WikiNode:
    """Resolved Feishu Wiki node metadata for its underlying document."""

    obj_type: str
    obj_token: str
    title: str


@dataclass(frozen=True, slots=True)
class ExportTaskResult:
    """Completed export task metadata needed to download the generated file."""

    file_name: str
    file_token: str


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

    @staticmethod
    def _is_permission_error(response: requests.Response, payload: dict[str, Any]) -> bool:
        """Return whether a Feishu response denotes an access-control failure."""

        code = payload.get("code")
        try:
            numeric_code = int(code)
        except (TypeError, ValueError):
            numeric_code = None
        return response.status_code in {401, 403} or numeric_code in PERMISSION_ERROR_CODES

    def _authenticated_json_request(
        self,
        method: str,
        path: str,
        *,
        error_class: type[FeishuApiError] = FeishuApiError,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Call an authenticated JSON API and close its response on every path."""

        token = self.get_tenant_access_token()
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {token}"
        headers.setdefault("Content-Type", "application/json; charset=utf-8")
        response = self._send_with_retries(
            method,
            f"{self.api_base_url}{path}",
            path=path,
            headers=headers,
            **kwargs,
        )
        try:
            payload = self._decode_json(response, path=path, error_class=error_class)
            if not response.ok or payload.get("code") != 0:
                resolved_error_class: type[FeishuApiError] = error_class
                if self._is_permission_error(response, payload):
                    resolved_error_class = FeishuPermissionError
                self._raise_api_error(
                    response,
                    payload,
                    path=path,
                    prefix="Feishu API request failed",
                    error_class=resolved_error_class,
                )
            return payload
        finally:
            response.close()

    @staticmethod
    def _required_string(source: dict[str, Any], field: str, *, path: str) -> str:
        """Read a non-empty string field from a Feishu response object."""

        value = source.get(field)
        if not isinstance(value, str) or not value.strip():
            raise FeishuDocumentExportError(
                f"Feishu response is missing {field!r}; path={path}"
            )
        return value.strip()

    def get_wiki_node(self, wiki_node_token: str) -> WikiNode:
        """Resolve a Wiki node to the actual document type, token, and title."""

        token = wiki_node_token.strip()
        if not token:
            raise ValueError("wiki_node_token cannot be empty")

        path = "/wiki/v2/spaces/get_node"
        payload = self._authenticated_json_request(
            "GET",
            path,
            params={"token": token},
            error_class=FeishuDocumentExportError,
        )
        data = payload.get("data")
        node = data.get("node") if isinstance(data, dict) else None
        if not isinstance(node, dict):
            raise FeishuDocumentExportError(
                f"Feishu Wiki response is missing data.node; path={path}"
            )

        return WikiNode(
            obj_type=self._required_string(node, "obj_type", path=path),
            obj_token=self._required_string(node, "obj_token", path=path),
            title=str(node.get("title") or "").strip(),
        )

    def create_export_task(self, document_token: str, document_type: str) -> str:
        """Create an XLSX export task for a sheet or bitable document."""

        token = document_token.strip()
        normalized_type = document_type.strip().lower()
        if not token:
            raise ValueError("document_token cannot be empty")
        if normalized_type not in EXPORTABLE_DOCUMENT_TYPES:
            allowed = ", ".join(sorted(EXPORTABLE_DOCUMENT_TYPES))
            raise ValueError(f"document_type must be one of: {allowed}")

        path = "/drive/v1/export_tasks"
        payload = self._authenticated_json_request(
            "POST",
            path,
            json={
                "file_extension": "xlsx",
                "token": token,
                "type": normalized_type,
            },
            error_class=FeishuDocumentExportError,
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise FeishuDocumentExportError(
                f"Feishu export response is missing data; path={path}"
            )
        return self._required_string(data, "ticket", path=path)

    def wait_for_export_task(
        self,
        ticket: str,
        document_token: str,
        *,
        timeout_seconds: float = EXPORT_TASK_TIMEOUT_SECONDS,
    ) -> ExportTaskResult:
        """Poll an export task until Feishu provides a generated file token."""

        normalized_ticket = ticket.strip()
        normalized_token = document_token.strip()
        if not normalized_ticket or not normalized_token:
            raise ValueError("ticket and document_token cannot be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")

        path = f"/drive/v1/export_tasks/{quote(normalized_ticket, safe='')}"
        deadline = self._clock() + timeout_seconds
        while True:
            payload = self._authenticated_json_request(
                "GET",
                path,
                params={"token": normalized_token},
                error_class=FeishuDocumentExportError,
            )
            data = payload.get("data")
            result = data.get("result") if isinstance(data, dict) else None
            if not isinstance(result, dict):
                raise FeishuDocumentExportError(
                    f"Feishu export status response is missing data.result; path={path}"
                )

            job_status = result.get("job_status")
            if job_status == 0:
                return ExportTaskResult(
                    file_name=str(result.get("file_name") or "").strip(),
                    file_token=self._required_string(result, "file_token", path=path),
                )

            job_error = str(result.get("job_error_msg") or "").strip()
            if job_error:
                if "permission" in job_error.lower() or "权限" in job_error:
                    raise FeishuPermissionError(
                        f"Feishu export task permission denied; path={path}"
                    )
                raise FeishuDocumentExportError(
                    f"Feishu export task failed; status={job_status}, error={self._redact(job_error)}"
                )
            if self._clock() >= deadline:
                raise FeishuDocumentExportError(
                    f"Feishu export task timed out; status={job_status}, path={path}"
                )
            self._sleep(EXPORT_TASK_POLL_INTERVAL_SECONDS)

    def download_exported_file(
        self,
        file_token: str,
        destination: str | Path,
        *,
        max_bytes: int,
    ) -> int:
        """Download a completed export task file atomically to ``destination``."""

        normalized_token = file_token.strip()
        if not normalized_token:
            raise ValueError("file_token cannot be empty")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")

        path = (
            "/drive/v1/export_tasks/file/"
            f"{quote(normalized_token, safe='')}/download"
        )
        token = self.get_tenant_access_token()
        response = self._send_with_retries(
            "GET",
            f"{self.api_base_url}{path}",
            path=path,
            headers={"Authorization": f"Bearer {token}"},
            stream=True,
        )
        if not response.ok:
            try:
                payload = self._decode_json(
                    response,
                    path=path,
                    error_class=FeishuDocumentExportError,
                )
                error_class: type[FeishuApiError] = FeishuDocumentExportError
                if self._is_permission_error(response, payload):
                    error_class = FeishuPermissionError
                self._raise_api_error(
                    response,
                    payload,
                    path=path,
                    prefix="Feishu export file download failed",
                    error_class=error_class,
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
                raise FeishuDocumentExportError(
                    f"Exported file exceeds local limit; size={declared_size}, max_bytes={max_bytes}"
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
                        raise FeishuDocumentExportError(
                            f"Exported file exceeds local limit; max_bytes={max_bytes}"
                        )
                    output.write(chunk)
            os.replace(temporary, target)
            return bytes_written
        except (OSError, requests.RequestException) as exc:
            temporary.unlink(missing_ok=True)
            raise FeishuDocumentExportError(
                self._redact(f"Feishu export file download failed; path={path}, error={exc}")
            ) from exc
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            response.close()
