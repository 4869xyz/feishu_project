"""Load and validate project settings from environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
import sys

from dotenv import dotenv_values, load_dotenv


def _runtime_project_root() -> Path:
    """Return the source root or the directory containing a frozen executable."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


PROJECT_ROOT = _runtime_project_root()
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

REQUIRED_ENV_VARS = (
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_SALES_TEMPLATE_PATH",
)

VALID_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
DEFAULT_INBOX_DIR = "./data/inbox"
DEFAULT_ARCHIVE_DIR = "./data/archive"
DEFAULT_AGGREGATION_DIR = "./data/aggregation"
MAX_MESSAGE_RESOURCE_BYTES = 100 * 1024 * 1024


class ConfigurationError(ValueError):
    """Raised when application configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated configuration required by the Feishu bot listener."""

    app_id: str
    app_secret: str = field(repr=False)
    log_dir: Path
    inbox_dir: Path
    archive_dir: Path
    max_download_bytes: int
    log_level: str
    aggregation_dir: Path = Path("./data/aggregation")
    sales_template_path: Path = Path("./template.xlsx")
    cache_admin_open_ids: tuple[str, ...] = ()


def _read_values(
    env_file: Path | None,
    environ: Mapping[str, str] | None,
) -> dict[str, str]:
    """Read dotenv values with explicit environment values taking precedence."""

    if environ is None:
        if env_file is not None:
            load_dotenv(dotenv_path=env_file, override=False)
        return dict(os.environ)

    values: dict[str, str] = {}
    if env_file is not None and env_file.is_file():
        values.update(
            {
                key: value
                for key, value in dotenv_values(env_file).items()
                if value is not None
            }
        )
    values.update(environ)
    return values


def _resolve_project_path(raw_value: str, project_root: Path) -> Path:
    """Resolve a configured path relative to the project root."""

    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _parse_max_download_bytes(raw_value: str) -> int:
    """Validate the optional local cap for downloaded message resources."""

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(
            "无效环境变量：FEISHU_MAX_DOWNLOAD_BYTES 必须是正整数"
        ) from exc

    if not 1 <= value <= MAX_MESSAGE_RESOURCE_BYTES:
        raise ConfigurationError(
            "无效环境变量：FEISHU_MAX_DOWNLOAD_BYTES 必须在 1 到 "
            f"{MAX_MESSAGE_RESOURCE_BYTES} 之间"
        )
    return value


def load_settings(
    *,
    env_file: str | Path | None = DEFAULT_ENV_FILE,
    environ: Mapping[str, str] | None = None,
    project_root: str | Path = PROJECT_ROOT,
) -> Settings:
    """Load, validate, and return settings while creating runtime directories.

    Args:
        env_file: Dotenv file to load. Pass ``None`` to use environment values only.
        environ: Optional environment mapping, primarily useful for tests. Values in
            this mapping override values loaded from ``env_file``.
        project_root: Base directory used for relative inbox, archive, and log paths.

    Raises:
        ConfigurationError: If a required value is absent or a local directory
            cannot be created.
    """

    root = Path(project_root).resolve()
    dotenv_path = Path(env_file).resolve() if env_file is not None else None
    values = _read_values(dotenv_path, environ)

    missing = [
        name
        for name in REQUIRED_ENV_VARS
        if not values.get(name, "").strip()
    ]
    if missing:
        raise ConfigurationError(f"缺少环境变量：{'、'.join(missing)}")
    required = {name: values[name].strip() for name in REQUIRED_ENV_VARS}

    log_level = values.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    if log_level not in VALID_LOG_LEVELS:
        allowed = ", ".join(sorted(VALID_LOG_LEVELS))
        raise ConfigurationError(
            f"无效环境变量：LOG_LEVEL={log_level!r}，可选值：{allowed}"
        )

    log_dir = (root / "logs").resolve()
    inbox_value = values.get("FEISHU_INBOX_DIR", DEFAULT_INBOX_DIR).strip()
    inbox_dir = _resolve_project_path(inbox_value or DEFAULT_INBOX_DIR, root)
    archive_value = values.get("FEISHU_ARCHIVE_DIR", DEFAULT_ARCHIVE_DIR).strip()
    archive_dir = _resolve_project_path(archive_value or DEFAULT_ARCHIVE_DIR, root)
    aggregation_value = values.get(
        "FEISHU_AGGREGATION_DIR", DEFAULT_AGGREGATION_DIR
    ).strip()
    aggregation_dir = _resolve_project_path(
        aggregation_value or DEFAULT_AGGREGATION_DIR, root
    )
    sales_template_path = _resolve_project_path(
        required["FEISHU_SALES_TEMPLATE_PATH"], root
    )
    cache_admin_open_ids = tuple(
        dict.fromkeys(
            item.strip()
            for item in values.get("FEISHU_CACHE_ADMIN_OPEN_IDS", "").split(",")
            if item.strip()
        )
    )
    max_download_raw = values.get(
        "FEISHU_MAX_DOWNLOAD_BYTES", str(MAX_MESSAGE_RESOURCE_BYTES)
    ).strip()
    max_download_bytes = _parse_max_download_bytes(max_download_raw)

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        inbox_dir.mkdir(parents=True, exist_ok=True)
        archive_dir.mkdir(parents=True, exist_ok=True)
        aggregation_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigurationError(f"无法创建本地运行目录：{exc}") from exc

    return Settings(
        app_id=required["FEISHU_APP_ID"],
        app_secret=required["FEISHU_APP_SECRET"],
        log_dir=log_dir,
        inbox_dir=inbox_dir,
        archive_dir=archive_dir,
        aggregation_dir=aggregation_dir,
        sales_template_path=sales_template_path,
        cache_admin_open_ids=cache_admin_open_ids,
        max_download_bytes=max_download_bytes,
        log_level=log_level,
    )
