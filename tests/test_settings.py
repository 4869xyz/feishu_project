"""Tests for configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import ConfigurationError, load_settings


def _valid_environment(output_dir: str = "./output") -> dict[str, str]:
    """Return a complete, non-secret test environment."""

    return {
        "FEISHU_APP_ID": "cli_test",
        "FEISHU_APP_SECRET": "test-secret-value",
        "FEISHU_APP_TOKEN": "bascn_test",
        "STANDARD_DETAIL_TABLE_ID": "tbl_detail",
        "PERSON_SUMMARY_TABLE_ID": "tbl_summary",
        "LOCAL_OUTPUT_DIR": output_dir,
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
    assert settings.app_token == "bascn_test"
    assert settings.standard_detail_table_id == "tbl_detail"
    assert settings.person_summary_table_id == "tbl_summary"
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
    assert "STANDARD_DETAIL_TABLE_ID" in message
    assert "PERSON_SUMMARY_TABLE_ID" in message


def test_runtime_directories_are_created(project_tmp_dir: Path) -> None:
    """Loading valid settings creates both output and log directories."""

    settings = load_settings(
        env_file=None,
        environ=_valid_environment("./custom-output"),
        project_root=project_tmp_dir,
    )

    assert settings.output_dir == (project_tmp_dir / "custom-output").resolve()
    assert settings.output_dir.is_dir()
    assert settings.log_dir == (project_tmp_dir / "logs").resolve()
    assert settings.log_dir.is_dir()


def test_dotenv_values_are_loaded_and_environment_wins(project_tmp_dir: Path) -> None:
    """Values from a dotenv file are loaded, with explicit environment overrides."""

    env_file = project_tmp_dir / ".env"
    env_file.write_text(
        "\n".join(
            [
                "FEISHU_APP_ID=cli_from_file",
                "FEISHU_APP_SECRET=secret_from_file",
                "FEISHU_APP_TOKEN=bascn_from_file",
                "STANDARD_DETAIL_TABLE_ID=tbl_detail_file",
                "PERSON_SUMMARY_TABLE_ID=tbl_summary_file",
                "LOCAL_OUTPUT_DIR=./dotenv-output",
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
    assert settings.output_dir == (project_tmp_dir / "dotenv-output").resolve()


def test_settings_repr_does_not_expose_app_secret(project_tmp_dir: Path) -> None:
    """Dataclass repr must never reveal the full application secret."""

    settings = load_settings(
        env_file=None,
        environ=_valid_environment(),
        project_root=project_tmp_dir,
    )

    assert settings.app_secret not in repr(settings)
