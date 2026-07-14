"""Check read-only access to the configured Feishu Bitable tables."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from clients.feishu_client import FeishuBitableClient, mask_token
from config.settings import ConfigurationError, Settings, load_settings


def _configure_stdout_encoding() -> None:
    """Prefer UTF-8 on Windows consoles so Chinese messages are not garbled."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - console encoding is best-effort
                pass


LOGGER = logging.getLogger(__name__)
SEPARATOR = "=" * 60
REPORT_FILE_NAME = "feishu_connection_report.json"


def _table_definitions(settings: Settings) -> dict[str, str]:
    """Return configured table names and identifiers in check order."""

    return {
        "签约标准明细表": settings.standard_detail_table_id,
        "签约个人汇总表": settings.person_summary_table_id,
    }


def _safe_error(error: BaseException, settings: Settings, token: str = "") -> str:
    """Return bounded diagnostic text with known credentials removed."""

    message = str(error)
    for secret in (settings.app_secret, token):
        if secret:
            message = message.replace(secret, "<redacted>")
    return message[:2000]


def _describe_table_error(
    table_name: str,
    error: BaseException,
    settings: Settings,
    token: str,
) -> str:
    """Add a concrete hint for common Base, table, and permission errors."""

    details = _safe_error(error, settings, token)
    if any(code in details for code in ("1254003", "1254040")):
        return (
            "无法访问多维表格 Base，请检查 FEISHU_APP_TOKEN；"
            f"{details}"
        )
    if any(code in details for code in ("1254004", "1254041")):
        return f"无法访问“{table_name}”，请检查对应 table_id；{details}"
    if "HTTP状态码=403" in details or "1254302" in details:
        return f"应用权限不足，无法读取“{table_name}”；{details}"
    return details


def _empty_table_result(table_id: str, error: str | None = None) -> dict[str, Any]:
    """Return a stable report entry for a table not yet checked or failed."""

    return {
        "table_id": table_id,
        "success": False,
        "field_count": 0,
        "fields": [],
        "sample_record_count": 0,
        "sample_records": [],
        "records_have_required_keys": False,
        "error": error,
    }


def _check_table(
    table_name: str,
    table_id: str,
    client: FeishuBitableClient,
    settings: Settings,
    token: str,
) -> dict[str, Any]:
    """Check one table's fields and at most three sample records."""

    print(f"\n正在检查：{table_name}")
    print(f"table_id：{table_id}")
    try:
        raw_fields = client.list_fields(table_id)
        sample_records = client.search_records(table_id, max_records=3)
        records_valid = all(
            "record_id" in record
            and "fields" in record
            and isinstance(record["fields"], dict)
            for record in sample_records
        )
        if not records_valid:
            raise ValueError("样例记录缺少 record_id 或有效的 fields 对象")

        report_fields = [
            {
                "field_id": field.get("field_id"),
                "field_name": field.get("field_name"),
                "type": field.get("type"),
                "is_primary": field.get("is_primary"),
            }
            for field in raw_fields
        ]

        print("[成功] 字段和记录读取成功")
        print(f"字段数量：{len(raw_fields)}")
        print(f"样例记录数量：{len(sample_records)}")
        print("字段列表：")
        if report_fields:
            for field in report_fields:
                print(f"- {field['field_name']}（type={field['type']}）")
        else:
            print("- （无字段）")

        LOGGER.info(
            "表检查成功：table=%s, table_id=%s, fields=%d, sample_records=%d",
            table_name,
            table_id,
            len(raw_fields),
            len(sample_records),
        )
        return {
            "table_id": table_id,
            "success": True,
            "field_count": len(raw_fields),
            "fields": report_fields,
            "sample_record_count": len(sample_records),
            "sample_records": sample_records,
            "records_have_required_keys": True,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - each table must fail independently
        error_message = _describe_table_error(
            table_name,
            exc,
            settings,
            token,
        )
        print("[失败] 无法完成该表的只读检查")
        print(f"错误信息：{error_message}")
        LOGGER.error(
            "表检查失败：table=%s, table_id=%s, error=%s",
            table_name,
            table_id,
            error_message,
        )
        return _empty_table_result(table_id, error_message)


def _write_report(report: dict[str, Any], output_dir: Path) -> Path:
    """Write a formatted UTF-8 JSON report using an atomic local replacement."""

    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / REPORT_FILE_NAME
    temporary_path = output_dir / f"{REPORT_FILE_NAME}.tmp"
    temporary_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(report_path)
    return report_path


def _display_path(path: Path) -> str:
    """Prefer a readable project-relative path for terminal output."""

    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(path)


def run_connection_check(
    settings: Settings,
    *,
    client: FeishuBitableClient | None = None,
) -> tuple[dict[str, Any], Path]:
    """Run authentication and both configured table checks, then persist a report."""

    active_client = client or FeishuBitableClient(settings)
    tables = _table_definitions(settings)
    report: dict[str, Any] = {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "authentication": {"success": False, "error": None},
        "app_token": settings.app_token,
        "all_tables_success": False,
        "success_table_count": 0,
        "failed_table_count": 0,
        "tables": {},
    }

    print(SEPARATOR)
    print("开始检查飞书连接")
    print(SEPARATOR)

    token = ""
    try:
        token = active_client.get_tenant_access_token()
        report["authentication"] = {"success": True, "error": None}
        print(f"\n[成功] tenant_access_token 获取成功：{mask_token(token)}")
        LOGGER.info("飞书 tenant_access_token 获取成功")
    except Exception as exc:  # noqa: BLE001 - an auth report must still be written
        error_message = _safe_error(exc, settings)
        report["authentication"] = {
            "success": False,
            "error": error_message,
        }
        print("\n[失败] tenant_access_token 获取失败")
        print(f"错误信息：{error_message}")
        LOGGER.error("飞书鉴权失败：%s", error_message)

        for table_name, table_id in tables.items():
            report["tables"][table_name] = _empty_table_result(
                table_id,
                "鉴权未通过，未执行表检查",
            )
        report["failed_table_count"] = len(tables)
    else:
        for table_name, table_id in tables.items():
            table_result = _check_table(
                table_name,
                table_id,
                active_client,
                settings,
                token,
            )
            report["tables"][table_name] = table_result

        success_count = sum(
            1 for result in report["tables"].values() if result["success"]
        )
        report["success_table_count"] = success_count
        report["failed_table_count"] = len(tables) - success_count
        report["all_tables_success"] = success_count == len(tables)

    report_path = _write_report(report, settings.output_dir)

    print(f"\n{SEPARATOR}")
    print("检查完成")
    print(f"成功表数量：{report['success_table_count']}")
    print(f"失败表数量：{report['failed_table_count']}")
    print(f"报告路径：{_display_path(report_path)}")
    print(SEPARATOR)
    return report, report_path


def _configure_logging(settings: Settings) -> None:
    """Configure file-only logging under the project-local log directory."""

    log_path = settings.log_dir / "feishu_connection.log"
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
        force=True,
    )


def main() -> int:
    """CLI entry point for ``python -m scripts.check_connection``."""

    _configure_stdout_encoding()
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        print(f"[配置错误] {exc}")
        return 2

    _configure_logging(settings)
    try:
        report, _ = run_connection_check(settings)
    except OSError as exc:
        safe_message = _safe_error(exc, settings)
        print(f"[失败] 无法写入本地检查报告：{safe_message}")
        LOGGER.error("报告写入失败：%s", safe_message)
        return 1

    return 0 if report["all_tables_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
