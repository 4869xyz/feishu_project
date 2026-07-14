"""Load and validate project settings from environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values, load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

REQUIRED_ENV_VARS = (
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_APP_TOKEN",
    "STANDARD_DETAIL_TABLE_ID",
    "PERSON_SUMMARY_TABLE_ID",
)

VALID_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


class ConfigurationError(ValueError):
    """Raised when application configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated configuration required by the read-only Feishu client."""

    app_id: str
    app_secret: str = field(repr=False)
    app_token: str
    standard_detail_table_id: str
    person_summary_table_id: str
    output_dir: Path
    log_dir: Path
    log_level: str


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


def _resolve_output_dir(raw_value: str, project_root: Path) -> Path:
    """Resolve a configured output directory relative to the project root."""

    output_dir = Path(raw_value).expanduser()
    if not output_dir.is_absolute():
        output_dir = project_root / output_dir
    return output_dir.resolve()


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
        project_root: Base directory used for relative output and log paths.

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

    output_value = values.get("LOCAL_OUTPUT_DIR", "./output").strip() or "./output"
    output_dir = _resolve_output_dir(output_value, root)
    log_dir = (root / "logs").resolve()

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigurationError(f"无法创建本地运行目录：{exc}") from exc

    return Settings(
        app_id=required["FEISHU_APP_ID"],
        app_secret=required["FEISHU_APP_SECRET"],
        app_token=required["FEISHU_APP_TOKEN"],
        standard_detail_table_id=required["STANDARD_DETAIL_TABLE_ID"],
        person_summary_table_id=required["PERSON_SUMMARY_TABLE_ID"],
        output_dir=output_dir,
        log_dir=log_dir,
        log_level=log_level,
    )
