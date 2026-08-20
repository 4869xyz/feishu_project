"""Configuration for the independent meeting-minutes bot."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values


def _runtime_project_root() -> Path:
    """Return the source root or the directory containing a frozen executable."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


PROJECT_ROOT = _runtime_project_root()
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env.meeting-minutes"
VALID_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
DEFAULT_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_PDF_PAGES = 50
DEFAULT_RETENTION_DAYS = 14
DEFAULT_ATTACHMENT_CACHE_TTL_SECONDS = DEFAULT_RETENTION_DAYS * 24 * 60 * 60
DEFAULT_ATTACHMENT_CACHE_MAX_BYTES = 512 * 1024 * 1024


class MeetingBotConfigurationError(ValueError):
    """Raised when meeting-bot configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class MeetingBotSettings:
    app_id: str
    app_secret: str = field(repr=False)
    database_url: str
    people_config_path: Path
    template_path: Path
    data_dir: Path
    output_dir: Path
    log_dir: Path
    timezone: str
    max_text_length: int
    log_level: str
    attachment_dir: Path | None = None
    max_attachment_bytes: int = DEFAULT_MAX_ATTACHMENT_BYTES
    max_pdf_pages: int = DEFAULT_MAX_PDF_PAGES
    retention_days: int = DEFAULT_RETENTION_DAYS
    attachment_cache_ttl_seconds: int = DEFAULT_ATTACHMENT_CACHE_TTL_SECONDS
    attachment_cache_max_bytes: int = DEFAULT_ATTACHMENT_CACHE_MAX_BYTES
    reminder_enabled: bool = True


def _project_path(value: str, root: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _sqlite_url(value: str, root: Path) -> str:
    prefix = "sqlite+aiosqlite:///"
    if not value.startswith(prefix):
        raise MeetingBotConfigurationError(
            "MEETING_BOT_DATABASE_URL 首版只支持 sqlite+aiosqlite:/// 地址"
        )
    raw_path = value[len(prefix) :]
    if not raw_path or raw_path == ":memory:":
        return value
    database_path = _project_path(raw_path, root)
    return f"{prefix}{database_path.as_posix()}"


def load_settings(
    *,
    env_file: str | Path | None = DEFAULT_ENV_FILE,
    environ: Mapping[str, str] | None = None,
    project_root: str | Path = PROJECT_ROOT,
) -> MeetingBotSettings:
    """Load settings without reading or overriding the sales bot's ``.env``."""

    root = Path(project_root).resolve()
    values: dict[str, str] = {}
    if env_file is not None:
        env_path = Path(env_file)
        if env_path.is_file():
            values.update(
                {
                    key: value
                    for key, value in dotenv_values(env_path).items()
                    if value is not None
                }
            )
    values.update(os.environ if environ is None else environ)

    required_names = (
        "MEETING_BOT_FEISHU_APP_ID",
        "MEETING_BOT_FEISHU_APP_SECRET",
        "MEETING_BOT_PEOPLE_CONFIG_PATH",
        "MEETING_BOT_TEMPLATE_PATH",
    )
    missing = [name for name in required_names if not values.get(name, "").strip()]
    if missing:
        raise MeetingBotConfigurationError(f"缺少环境变量：{'、'.join(missing)}")

    data_dir = _project_path(
        values.get("MEETING_BOT_DATA_DIR", "./data/meeting_minutes"), root
    )
    log_dir = _project_path(
        values.get("MEETING_BOT_LOG_DIR", "./logs/meeting_minutes"), root
    )
    output_dir = data_dir / "output"
    database_url = _sqlite_url(
        values.get(
            "MEETING_BOT_DATABASE_URL",
            "sqlite+aiosqlite:///./data/meeting_minutes/meeting_minutes.db",
        ).strip(),
        root,
    )

    try:
        max_text_length = int(
            values.get("MEETING_BOT_MAX_TEXT_LENGTH", "20000").strip()
        )
    except ValueError as exc:
        raise MeetingBotConfigurationError(
            "MEETING_BOT_MAX_TEXT_LENGTH 必须是正整数"
        ) from exc
    if not 1 <= max_text_length <= 100_000:
        raise MeetingBotConfigurationError(
            "MEETING_BOT_MAX_TEXT_LENGTH 必须在 1 到 100000 之间"
        )

    log_level = values.get("MEETING_BOT_LOG_LEVEL", "INFO").strip().upper()
    if log_level not in VALID_LOG_LEVELS:
        raise MeetingBotConfigurationError(
            "MEETING_BOT_LOG_LEVEL 必须是 " + ", ".join(sorted(VALID_LOG_LEVELS))
        )

    timezone = values.get("MEETING_BOT_TIMEZONE", "Asia/Shanghai").strip()
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(timezone)
    except Exception as exc:
        raise MeetingBotConfigurationError(
            f"无效的 MEETING_BOT_TIMEZONE：{timezone}"
        ) from exc

    def positive_int(name: str, default: int) -> int:
        try:
            value = int(values.get(name, str(default)).strip())
        except ValueError as exc:
            raise MeetingBotConfigurationError(f"{name} 必须是正整数") from exc
        if value <= 0:
            raise MeetingBotConfigurationError(f"{name} 必须是正整数")
        return value

    max_attachment_bytes = positive_int(
        "MEETING_BOT_MAX_ATTACHMENT_BYTES", DEFAULT_MAX_ATTACHMENT_BYTES
    )
    if max_attachment_bytes > 100 * 1024 * 1024:
        raise MeetingBotConfigurationError(
            "MEETING_BOT_MAX_ATTACHMENT_BYTES 不能超过 100 MB"
        )
    max_pdf_pages = positive_int("MEETING_BOT_MAX_PDF_PAGES", DEFAULT_MAX_PDF_PAGES)
    retention_days = positive_int("MEETING_BOT_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)
    if retention_days > 3650:
        raise MeetingBotConfigurationError(
            "MEETING_BOT_RETENTION_DAYS 不能超过 3650 天"
        )
    if "MEETING_BOT_RETENTION_DAYS" in values:
        attachment_cache_ttl_seconds = retention_days * 24 * 60 * 60
    else:
        attachment_cache_ttl_seconds = positive_int(
            "MEETING_BOT_ATTACHMENT_CACHE_TTL_SECONDS",
            retention_days * 24 * 60 * 60,
        )
    attachment_cache_max_bytes = positive_int(
        "MEETING_BOT_ATTACHMENT_CACHE_MAX_BYTES",
        DEFAULT_ATTACHMENT_CACHE_MAX_BYTES,
    )
    if attachment_cache_max_bytes < max_attachment_bytes:
        raise MeetingBotConfigurationError(
            "MEETING_BOT_ATTACHMENT_CACHE_MAX_BYTES 不能小于单附件大小限制"
        )

    attachment_dir = data_dir / "attachments"

    reminder_raw = values.get("MEETING_BOT_REMINDER_ENABLED", "true").strip().lower()
    if reminder_raw in {"1", "true", "yes", "on"}:
        reminder_enabled = True
    elif reminder_raw in {"0", "false", "no", "off"}:
        reminder_enabled = False
    else:
        raise MeetingBotConfigurationError(
            "MEETING_BOT_REMINDER_ENABLED 必须是 true 或 false"
        )

    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        attachment_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "submission_docs").mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise MeetingBotConfigurationError(f"无法创建纪要机器人运行目录：{exc}") from exc

    return MeetingBotSettings(
        app_id=values["MEETING_BOT_FEISHU_APP_ID"].strip(),
        app_secret=values["MEETING_BOT_FEISHU_APP_SECRET"].strip(),
        database_url=database_url,
        people_config_path=_project_path(
            values["MEETING_BOT_PEOPLE_CONFIG_PATH"].strip(), root
        ),
        template_path=_project_path(values["MEETING_BOT_TEMPLATE_PATH"].strip(), root),
        data_dir=data_dir,
        output_dir=output_dir,
        log_dir=log_dir,
        timezone=timezone,
        max_text_length=max_text_length,
        log_level=log_level,
        attachment_dir=attachment_dir,
        max_attachment_bytes=max_attachment_bytes,
        max_pdf_pages=max_pdf_pages,
        retention_days=retention_days,
        attachment_cache_ttl_seconds=attachment_cache_ttl_seconds,
        attachment_cache_max_bytes=attachment_cache_max_bytes,
        reminder_enabled=reminder_enabled,
    )
