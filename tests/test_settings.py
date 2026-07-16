"""Tests for configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import ConfigurationError, load_settings


def _valid_environment() -> dict[str, str]:
    """Return a complete, non-secret test environment."""

    return {
        "FEISHU_APP_ID": "cli_test",
        "FEISHU_APP_SECRET": "test-secret-value",
        "FEISHU_SALES_TEMPLATE_PATH": "./template.xlsx",
        "LOG_LEVEL": "debug",
    }


def test_load_settings_from_environment(project_tmp_dir: Path) -> None:
    """Explicit environment values are mapped to the Settings dataclass."""

    settings = load_settings(
        env_file=None,
        environ=_valid_environment(),
        project_root=project_tmp_dir,
    )

    assert settings.app_id == "cli_test"
    assert settings.app_secret == "test-secret-value"
    assert settings.inbox_dir == (project_tmp_dir / "data" / "inbox").resolve()
    assert settings.archive_dir == (project_tmp_dir / "data" / "archive").resolve()
    assert settings.aggregation_dir == (
        project_tmp_dir / "data" / "aggregation"
    ).resolve()
    assert settings.sales_template_path == (project_tmp_dir / "template.xlsx").resolve()
    assert settings.max_download_bytes == 100 * 1024 * 1024
    assert settings.log_level == "DEBUG"


def test_missing_required_environment_variable_is_explicit(
    project_tmp_dir: Path,
) -> None:
    """A missing value raises an error containing only the missing variable name."""

    environment = _valid_environment()
    secret = environment.pop("FEISHU_APP_SECRET")

    with pytest.raises(ConfigurationError) as exc_info:
        load_settings(
            env_file=None,
            environ=environment,
            project_root=project_tmp_dir,
        )

    assert "FEISHU_APP_SECRET" in str(exc_info.value)
    assert secret not in str(exc_info.value)


def test_all_missing_environment_variables_are_reported(
    project_tmp_dir: Path,
) -> None:
    """A single configuration error lists every value the user must add."""

    with pytest.raises(ConfigurationError) as exc_info:
        load_settings(env_file=None, environ={}, project_root=project_tmp_dir)

    message = str(exc_info.value)
    assert "FEISHU_APP_ID" in message
    assert "FEISHU_APP_SECRET" in message
    assert "FEISHU_SALES_TEMPLATE_PATH" in message


def test_runtime_directories_are_created(project_tmp_dir: Path) -> None:
    """Loading valid settings creates log and Excel inbox directories."""

    environment = _valid_environment()
    environment["FEISHU_INBOX_DIR"] = "./custom-inbox"
    environment["FEISHU_ARCHIVE_DIR"] = "./custom-archive"

    settings = load_settings(
        env_file=None,
        environ=environment,
        project_root=project_tmp_dir,
    )

    assert settings.inbox_dir == (project_tmp_dir / "custom-inbox").resolve()
    assert settings.inbox_dir.is_dir()
    assert settings.archive_dir == (project_tmp_dir / "custom-archive").resolve()
    assert settings.archive_dir.is_dir()
    assert settings.aggregation_dir.is_dir()
    assert settings.log_dir == (project_tmp_dir / "logs").resolve()
    assert settings.log_dir.is_dir()


@pytest.mark.parametrize("raw_value", ["0", "104857601", "not-a-number"])
def test_download_size_limit_is_validated(
    project_tmp_dir: Path,
    raw_value: str,
) -> None:
    """The local download limit cannot exceed Feishu's 100 MB endpoint cap."""

    environment = _valid_environment()
    environment["FEISHU_MAX_DOWNLOAD_BYTES"] = raw_value

    with pytest.raises(ConfigurationError, match="FEISHU_MAX_DOWNLOAD_BYTES"):
        load_settings(env_file=None, environ=environment, project_root=project_tmp_dir)


def test_dotenv_values_are_loaded_and_environment_wins(project_tmp_dir: Path) -> None:
    """Values from a dotenv file are loaded, with explicit environment overrides."""

    env_file = project_tmp_dir / ".env"
    env_file.write_text(
        "\n".join(
            [
                "FEISHU_APP_ID=cli_from_file",
                "FEISHU_APP_SECRET=secret_from_file",
                "FEISHU_SALES_TEMPLATE_PATH=./template.xlsx",
                "FEISHU_INBOX_DIR=./dotenv-inbox",
                "LOG_LEVEL=INFO",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(
        env_file=env_file,
        environ={"FEISHU_APP_ID": "cli_from_environment"},
        project_root=project_tmp_dir,
    )

    assert settings.app_id == "cli_from_environment"
    assert settings.app_secret == "secret_from_file"
    assert settings.inbox_dir == (project_tmp_dir / "dotenv-inbox").resolve()


def test_settings_repr_does_not_expose_app_secret(project_tmp_dir: Path) -> None:
    """Dataclass repr must never reveal the full application secret."""

    settings = load_settings(
        env_file=None,
        environ=_valid_environment(),
        project_root=project_tmp_dir,
    )

    assert settings.app_secret not in repr(settings)
