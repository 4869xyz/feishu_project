from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

from meeting_minutes_bot.people import PeopleConfigurationError, load_people
from meeting_minutes_bot.period import meeting_period
from meeting_minutes_bot.settings import (
    MeetingBotConfigurationError,
    _runtime_project_root,
    load_settings,
)


def test_settings_are_isolated_from_sales_bot_variables(project_tmp_dir: Path) -> None:
    people = project_tmp_dir / "people.yaml"
    template = project_tmp_dir / "template.docx"
    values = {
        "FEISHU_APP_ID": "sales-app",
        "FEISHU_APP_SECRET": "sales-secret",
        "MEETING_BOT_FEISHU_APP_ID": "meeting-app",
        "MEETING_BOT_FEISHU_APP_SECRET": "meeting-secret",
        "MEETING_BOT_PEOPLE_CONFIG_PATH": str(people),
        "MEETING_BOT_TEMPLATE_PATH": str(template),
    }
    settings = load_settings(env_file=None, environ=values, project_root=project_tmp_dir)

    assert settings.app_id == "meeting-app"
    assert "meeting-secret" not in repr(settings)
    assert settings.data_dir == project_tmp_dir / "data" / "meeting_minutes"
    assert settings.log_dir == project_tmp_dir / "logs" / "meeting_minutes"
    assert settings.attachment_dir == project_tmp_dir / "data" / "meeting_minutes" / "attachments"
    assert settings.max_attachment_bytes == 20 * 1024 * 1024
    assert settings.max_pdf_pages == 50
    assert settings.retention_days == 14
    assert settings.attachment_cache_ttl_seconds == 14 * 24 * 60 * 60
    assert settings.attachment_cache_max_bytes == 512 * 1024 * 1024
    assert settings.reminder_enabled is True
    assert "meeting_minutes.db" in settings.database_url


def test_runtime_project_root_uses_frozen_executable_directory(
    monkeypatch: pytest.MonkeyPatch,
    project_tmp_dir: Path,
) -> None:
    """A packaged executable resolves config and runtime data beside itself."""

    executable = project_tmp_dir / "MeetingMinutesBot.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))

    assert _runtime_project_root() == project_tmp_dir.resolve()


def test_runtime_project_root_uses_source_root_when_not_frozen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert (_runtime_project_root() / "meeting_minutes_bot").is_dir()


def test_settings_require_only_meeting_bot_credentials(project_tmp_dir: Path) -> None:
    with pytest.raises(MeetingBotConfigurationError, match="MEETING_BOT_FEISHU_APP_ID"):
        load_settings(env_file=None, environ={}, project_root=project_tmp_dir)


def test_attachment_settings_are_validated(project_tmp_dir: Path) -> None:
    base = {
        "MEETING_BOT_FEISHU_APP_ID": "meeting-app",
        "MEETING_BOT_FEISHU_APP_SECRET": "meeting-secret",
        "MEETING_BOT_PEOPLE_CONFIG_PATH": str(project_tmp_dir / "people.yaml"),
        "MEETING_BOT_TEMPLATE_PATH": str(project_tmp_dir / "template.docx"),
    }
    with pytest.raises(MeetingBotConfigurationError, match="不能超过 100 MB"):
        load_settings(
            env_file=None,
            environ={
                **base,
                "MEETING_BOT_MAX_ATTACHMENT_BYTES": str(101 * 1024 * 1024),
            },
            project_root=project_tmp_dir,
        )
    with pytest.raises(MeetingBotConfigurationError, match="不能小于"):
        load_settings(
            env_file=None,
            environ={
                **base,
                "MEETING_BOT_MAX_ATTACHMENT_BYTES": str(2 * 1024 * 1024),
                "MEETING_BOT_ATTACHMENT_CACHE_MAX_BYTES": str(1024 * 1024),
            },
            project_root=project_tmp_dir,
        )


def test_retention_days_override_legacy_attachment_ttl(project_tmp_dir: Path) -> None:
    settings = load_settings(
        env_file=None,
        environ={
            "MEETING_BOT_FEISHU_APP_ID": "meeting-app",
            "MEETING_BOT_FEISHU_APP_SECRET": "meeting-secret",
            "MEETING_BOT_PEOPLE_CONFIG_PATH": str(project_tmp_dir / "people.yaml"),
            "MEETING_BOT_TEMPLATE_PATH": str(project_tmp_dir / "template.docx"),
            "MEETING_BOT_RETENTION_DAYS": "14",
            "MEETING_BOT_ATTACHMENT_CACHE_TTL_SECONDS": "604800",
        },
        project_root=project_tmp_dir,
    )

    assert settings.retention_days == 14
    assert settings.attachment_cache_ttl_seconds == 14 * 24 * 60 * 60


def test_people_config_validates_identity_and_order(project_tmp_dir: Path) -> None:
    path = project_tmp_dir / "people.yaml"
    path.write_text(
        """
people:
  ou_second:
    name: 第二人
    department: 部门
    template_key: second
    section_order: 2
    sort_order: 2
  ou_first:
    name: 第一人
    department: 部门
    template_key: first
    section_order: 2
    sort_order: 1
admins: [ou_admin]
""".strip(),
        encoding="utf-8",
    )
    directory = load_people(path)

    assert [person.name for person in directory.enabled_people] == ["第一人", "第二人"]
    assert directory.find("ou_first").template_key == "first"
    assert directory.is_admin("ou_admin")


def test_people_config_rejects_duplicate_template_key(project_tmp_dir: Path) -> None:
    path = project_tmp_dir / "people.yaml"
    path.write_text(
        """
people:
  ou_one: {name: 一, department: 部门, template_key: same, section_order: 1, sort_order: 1}
  ou_two: {name: 二, department: 部门, template_key: same, section_order: 1, sort_order: 2}
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(PeopleConfigurationError, match="template_key 重复"):
        load_people(path)


def test_iso_week_uses_shanghai_timezone_at_week_boundary() -> None:
    sunday_utc = datetime(2026, 8, 2, 15, 59, tzinfo=timezone.utc)
    monday_utc = datetime(2026, 8, 2, 16, 0, tzinfo=timezone.utc)

    assert meeting_period(sunday_utc) == "2026-W31"
    assert meeting_period(monday_utc) == "2026-W32"


def test_iso_week_handles_cross_year_week() -> None:
    assert meeting_period(datetime(2021, 1, 1, 12, 0)) == "2020-W53"
