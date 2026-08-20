"""Load and validate the private Feishu-open-id to employee mapping."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class PeopleConfigurationError(ValueError):
    """Raised when the people mapping is unsafe or incomplete."""


@dataclass(frozen=True, slots=True)
class Person:
    open_id: str
    name: str
    department: str
    template_key: str
    section_order: int
    sort_order: int
    enabled: bool = True
    role: str = "employee"


@dataclass(frozen=True, slots=True)
class PeopleDirectory:
    people: tuple[Person, ...]
    admins: frozenset[str]

    def find(self, open_id: str) -> Person | None:
        return next((person for person in self.people if person.open_id == open_id), None)

    def is_admin(self, open_id: str) -> bool:
        return open_id in self.admins

    @property
    def enabled_people(self) -> tuple[Person, ...]:
        return tuple(
            sorted(
                (person for person in self.people if person.enabled),
                key=lambda person: (
                    person.section_order,
                    person.sort_order,
                    person.name,
                ),
            )
        )


class PeopleStore:
    """Mutable holder that lets consumers share one hot-swappable directory."""

    def __init__(
        self,
        directory: PeopleDirectory,
        *,
        config_path: str | Path | None = None,
    ) -> None:
        self._directory = directory
        self._config_path = Path(config_path) if config_path is not None else None

    @classmethod
    def from_path(cls, path: str | Path) -> "PeopleStore":
        """Load the YAML once and keep the path for later reloads."""

        return cls(load_people(path), config_path=path)

    @property
    def directory(self) -> PeopleDirectory:
        return self._directory

    @property
    def config_path(self) -> Path | None:
        return self._config_path

    def load_candidate(self) -> PeopleDirectory:
        """Re-read the YAML without touching the currently active directory."""

        if self._config_path is None:
            raise PeopleConfigurationError("人员配置路径未设置，无法重载")
        return load_people(self._config_path)

    def replace(self, directory: PeopleDirectory) -> None:
        """Atomically switch every consumer to the validated new directory."""

        self._directory = directory


def ensure_store(people: "PeopleDirectory | PeopleStore") -> PeopleStore:
    """Wrap a bare directory so consumers can uniformly read via a store."""

    if isinstance(people, PeopleStore):
        return people
    return PeopleStore(people)


def _required_text(source: dict[str, Any], name: str, open_id: str) -> str:
    value = str(source.get(name, "")).strip()
    if not value:
        raise PeopleConfigurationError(f"人员 {open_id} 缺少字段：{name}")
    return value


def _positive_int(source: dict[str, Any], name: str, open_id: str) -> int:
    try:
        value = int(source.get(name, 0))
    except (TypeError, ValueError) as exc:
        raise PeopleConfigurationError(
            f"人员 {open_id} 的 {name} 必须是正整数"
        ) from exc
    if value < 1:
        raise PeopleConfigurationError(f"人员 {open_id} 的 {name} 必须是正整数")
    return value


def _enabled(source: dict[str, Any], open_id: str) -> bool:
    value = source.get("enabled", True)
    if not isinstance(value, bool):
        raise PeopleConfigurationError(f"人员 {open_id} 的 enabled 必须是 true 或 false")
    return value


def load_people(path: str | Path) -> PeopleDirectory:
    config_path = Path(path)
    if not config_path.is_file():
        raise PeopleConfigurationError(f"人员配置不存在：{config_path}")
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise PeopleConfigurationError(f"无法读取人员配置：{exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("people"), dict):
        raise PeopleConfigurationError("人员配置必须包含 people 映射")

    people: list[Person] = []
    template_keys: set[str] = set()
    for raw_open_id, raw_person in payload["people"].items():
        open_id = str(raw_open_id).strip()
        if not open_id or not isinstance(raw_person, dict):
            raise PeopleConfigurationError("people 中的 open_id 和人员内容必须有效")
        template_key = _required_text(raw_person, "template_key", open_id)
        if template_key in template_keys:
            raise PeopleConfigurationError(f"template_key 重复：{template_key}")
        template_keys.add(template_key)
        role = str(raw_person.get("role", "employee")).strip().lower()
        if role not in {"employee", "admin"}:
            raise PeopleConfigurationError(f"人员 {open_id} 的 role 无效：{role}")
        people.append(
            Person(
                open_id=open_id,
                name=_required_text(raw_person, "name", open_id),
                department=_required_text(raw_person, "department", open_id),
                template_key=template_key,
                section_order=_positive_int(raw_person, "section_order", open_id),
                sort_order=_positive_int(raw_person, "sort_order", open_id),
                enabled=_enabled(raw_person, open_id),
                role=role,
            )
        )

    raw_admins = payload.get("admins", [])
    if not isinstance(raw_admins, list):
        raise PeopleConfigurationError("admins 必须是 open_id 列表")
    admins = frozenset(str(item).strip() for item in raw_admins if str(item).strip())
    return PeopleDirectory(people=tuple(people), admins=admins)
