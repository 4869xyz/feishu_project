"""Tests for the end-to-end local connection report workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clients.feishu_client import FeishuApiError, FeishuAuthenticationError
from config.settings import Settings
from scripts.check_connection import run_connection_check


FULL_TOKEN = "t-full-private-token-value"
TEST_SECRET = "script-test-secret"


def _settings(project_tmp_dir: Path) -> Settings:
    """Create isolated settings for report tests."""

    output_dir = project_tmp_dir / "output"
    log_dir = project_tmp_dir / "logs"
    output_dir.mkdir()
    log_dir.mkdir()
    return Settings(
        app_id="cli_test",
        app_secret=TEST_SECRET,
        app_token="bascn_test",
        standard_detail_table_id="tbl_detail",
        person_summary_table_id="tbl_summary",
        output_dir=output_dir,
        log_dir=log_dir,
        log_level="INFO",
    )


class PartiallyFailingClient:
    """Fake client where one table fails and the other remains readable."""

    def __init__(self) -> None:
        self.searched_tables: list[str] = []

    def get_tenant_access_token(self) -> str:
        """Return an inert test token."""

        return FULL_TOKEN

    def list_fields(self, table_id: str) -> list[dict[str, Any]]:
        """Return one field except for the intentionally broken table."""

        if table_id == "tbl_detail":
            raise FeishuApiError(
                "飞书 API 请求失败：HTTP状态码=200, code=1254041, "
                "msg=TableIdNotFound, request_id=req-1, path=/fields"
            )
        return [
            {
                "field_id": f"fld_{table_id}",
                "field_name": "客户名称",
                "type": 1,
                "is_primary": True,
            }
        ]

    def search_records(
        self,
        table_id: str,
        page_size: int = 100,
        max_records: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return one record for the readable table."""

        self.searched_tables.append(table_id)
        assert max_records == 3
        return [{"record_id": f"rec_{table_id}", "fields": {"客户名称": "示例"}}]


def test_report_is_written_when_one_table_fails_and_other_succeeds(
    project_tmp_dir: Path,
    capsys: Any,
) -> None:
    """One table failure does not block later checks or the UTF-8 report."""

    settings = _settings(project_tmp_dir)
    client = PartiallyFailingClient()

    report, report_path = run_connection_check(settings, client=client)  # type: ignore[arg-type]

    assert report_path.is_file()
    saved_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved_report == report
    assert report["success_table_count"] == 1
    assert report["failed_table_count"] == 1
    assert report["all_tables_success"] is False
    assert report["tables"]["签约标准明细表"]["success"] is False
    assert report["tables"]["签约个人汇总表"]["success"] is True
    assert "tbl_summary" in client.searched_tables
    assert "tbl_detail" not in client.searched_tables

    output = capsys.readouterr().out
    assert "签约标准明细表" in output
    assert "签约个人汇总表" in output
    assert "成功表数量：1" in output
    assert FULL_TOKEN not in output
    assert TEST_SECRET not in output


class EmptySummaryClient:
    """Fake client where the summary table is empty but still readable."""

    def __init__(self) -> None:
        self.searched_tables: list[str] = []

    def get_tenant_access_token(self) -> str:
        """Return an inert test token."""

        return FULL_TOKEN

    def list_fields(self, table_id: str) -> list[dict[str, Any]]:
        """Return one field for every table."""

        return [
            {
                "field_id": f"fld_{table_id}",
                "field_name": "客户名称",
                "type": 1,
                "is_primary": True,
            }
        ]

    def search_records(
        self,
        table_id: str,
        page_size: int = 100,
        max_records: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return an empty summary table and one detail record."""

        self.searched_tables.append(table_id)
        assert max_records == 3
        if table_id == "tbl_summary":
            return []
        return [{"record_id": f"rec_{table_id}", "fields": {"客户名称": "示例"}}]


def test_empty_table_still_counts_as_success(
    project_tmp_dir: Path,
) -> None:
    """An empty but readable table is treated as a successful check."""

    settings = _settings(project_tmp_dir)
    client = EmptySummaryClient()

    report, _ = run_connection_check(settings, client=client)  # type: ignore[arg-type]

    assert report["all_tables_success"] is True
    assert report["success_table_count"] == 2
    assert report["tables"]["签约个人汇总表"]["sample_record_count"] == 0
    assert report["tables"]["签约标准明细表"]["sample_record_count"] == 1


class AuthenticationFailingClient:
    """Fake client that fails before any table can be checked."""

    def get_tenant_access_token(self) -> str:
        """Raise a credential-safe authentication error."""

        raise FeishuAuthenticationError("飞书鉴权失败：code=10003")


def test_authentication_failure_still_generates_complete_report(
    project_tmp_dir: Path,
) -> None:
    """A global auth failure creates two explicit skipped table entries."""

    settings = _settings(project_tmp_dir)

    report, report_path = run_connection_check(
        settings,
        client=AuthenticationFailingClient(),  # type: ignore[arg-type]
    )

    assert report_path.is_file()
    assert report["authentication"]["success"] is False
    assert report["success_table_count"] == 0
    assert report["failed_table_count"] == 2
    assert len(report["tables"]) == 2
    assert all(
        table["error"] == "鉴权未通过，未执行表检查"
        for table in report["tables"].values()
    )
