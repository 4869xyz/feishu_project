"""Atomic backup and hot-apply for people YAML and Word templates."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .document import MinutesDocumentRenderer, MinutesTemplateError
from .people import PeopleConfigurationError, PeopleDirectory, PeopleStore, load_people


CONFIG_BACKUP_DIRNAME = "config_backups"
MAX_CONFIG_BACKUPS = 20
YAML_SUFFIXES = frozenset({".yaml", ".yml"})


class ConfigUpdateError(ValueError):
    """Raised when an uploaded config file cannot be applied safely."""


@dataclass(frozen=True, slots=True)
class ConfigApplyResult:
    kind: str
    summary: str
    directory: PeopleDirectory | None = None
    backup_path: Path | None = None
    admin_warning: str | None = None


def classify_admin_config_upload(
    *,
    file_name: str,
    template_path: Path,
    is_admin: bool,
) -> str | None:
    """Return ``people`` / ``template`` when the file is an admin config upload."""

    if not is_admin:
        return None
    suffix = Path(file_name).suffix.lower()
    if suffix in YAML_SUFFIXES:
        return "people"
    if suffix == ".docx" and file_name.lower() == template_path.name.lower():
        return "template"
    return None


def backup_dir(data_dir: Path) -> Path:
    path = Path(data_dir) / CONFIG_BACKUP_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _prune_backups(directory: Path, *, keep: int = MAX_CONFIG_BACKUPS) -> None:
    files = sorted(
        (path for path in directory.iterdir() if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale in files[keep:]:
        try:
            stale.unlink()
        except OSError:
            continue


def backup_existing(path: Path, data_dir: Path, *, stamp: datetime | None = None) -> Path | None:
    """Copy ``path`` into config_backups when it exists; return backup path."""

    source = Path(path)
    if not source.is_file():
        return None
    when = stamp or datetime.now()
    target_dir = backup_dir(data_dir)
    target = target_dir / f"{source.name}.{when.strftime('%Y%m%d_%H%M%S')}.bak"
    shutil.copy2(source, target)
    _prune_backups(target_dir)
    return target


def atomic_replace(source: Path, destination: Path) -> None:
    """Replace ``destination`` with ``source`` via same-directory temp + replace."""

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_name(f".{destination.name}.{os_getpid()}.part")
    try:
        shutil.copy2(source, temp_path)
        temp_path.replace(destination)
    finally:
        if temp_path.is_file():
            try:
                temp_path.unlink()
            except OSError:
                pass


def os_getpid() -> int:
    """Indirection for tests; wraps ``os.getpid``."""

    import os

    return os.getpid()


def apply_people_yaml(
    *,
    uploaded_path: Path,
    people_store: PeopleStore,
    renderer: MinutesDocumentRenderer,
    data_dir: Path,
    sender_open_id: str,
) -> ConfigApplyResult:
    """Validate uploaded YAML then backup and replace the live people file."""

    if people_store.config_path is None:
        raise ConfigUpdateError("人员配置路径未设置，无法应用上传的 YAML")

    candidate = load_people(uploaded_path)
    renderer.validate_template(candidate)

    backup = backup_existing(people_store.config_path, data_dir)
    atomic_replace(uploaded_path, people_store.config_path)
    people_store.replace(candidate)

    enabled = candidate.enabled_people
    warning: str | None = None
    if sender_open_id not in candidate.admins:
        warning = (
            "警告：新配置的 admins 未包含你的 open_id，"
            "你将失去后续管理指令权限，请尽快修正。"
        )
    summary = (
        "人员配置已通过上传更新并生效。\n"
        f"总人数：{len(candidate.people)}\n"
        f"启用人数：{len(enabled)}\n"
        f"管理员数：{len(candidate.admins)}\n"
        f"启用人员：{'、'.join(person.name for person in enabled) or '无'}"
    )
    if backup is not None:
        summary += f"\n已备份旧配置：{backup.name}"
    if warning:
        summary += f"\n{warning}"
    return ConfigApplyResult(
        kind="people",
        summary=summary,
        directory=candidate,
        backup_path=backup,
        admin_warning=warning,
    )


def apply_template_docx(
    *,
    uploaded_path: Path,
    renderer: MinutesDocumentRenderer,
    data_dir: Path,
) -> ConfigApplyResult:
    """Validate uploaded template then backup and replace the live template file."""

    renderer.validate_template(template_path=uploaded_path)
    backup = backup_existing(renderer.template_path, data_dir)
    atomic_replace(uploaded_path, renderer.template_path)
    # Confirm the live path still validates after replace.
    variables = renderer.validate_template()
    summary = (
        "Word 模板已通过上传更新并生效。\n"
        f"模板文件：{renderer.template_path.name}\n"
        f"占位符数量：{len(variables)}"
    )
    if backup is not None:
        summary += f"\n已备份旧模板：{backup.name}"
    return ConfigApplyResult(
        kind="template",
        summary=summary,
        backup_path=backup,
    )


def describe_config_health(
    *,
    people_store: PeopleStore,
    renderer: MinutesDocumentRenderer,
) -> str:
    """Return a read-only configuration health summary for admins."""

    lines = [
        "当前配置体检：",
        f"人员 YAML：{people_store.config_path or '未设置路径'}",
        f"模板路径：{renderer.template_path}",
    ]
    try:
        if people_store.config_path is None:
            raise PeopleConfigurationError("人员配置路径未设置")
        on_disk = load_people(people_store.config_path)
        variables = renderer.validate_template(on_disk)
        enabled = on_disk.enabled_people
        lines.extend(
            [
                "状态：通过",
                f"内存启用人数：{len(people_store.directory.enabled_people)}",
                f"磁盘启用人数：{len(enabled)}",
                f"管理员数：{len(on_disk.admins)}",
                f"模板占位符：{len(variables)}",
            ]
        )
        disk_ids = {person.open_id for person in on_disk.enabled_people}
        memory_ids = {
            person.open_id for person in people_store.directory.enabled_people
        }
        if disk_ids != memory_ids:
            lines.append(
                "提示：磁盘 YAML 与内存名单不一致，可发送「重载人员配置」或重新上传 YAML。"
            )
    except (PeopleConfigurationError, MinutesTemplateError, OSError) as exc:
        lines.append(f"状态：失败 — {exc}")
    return "\n".join(lines)
