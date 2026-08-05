"""Validate and render the official weekly-minutes DOCX template."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from docxtpl import DocxTemplate

from .people import PeopleDirectory


class MinutesTemplateError(ValueError):
    """Raised when the configured Word template cannot safely be rendered."""


def numbered_contents(contents: tuple[str, ...]) -> str:
    if not contents:
        return "本周未提交"
    return "\n".join(f"{index}. {content}" for index, content in enumerate(contents, 1))


class MinutesDocumentRenderer:
    def __init__(
        self,
        *,
        template_path: str | Path,
        output_dir: str | Path,
        people: PeopleDirectory,
    ) -> None:
        self.template_path = Path(template_path).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.people = people
        self.validate_template()

    def validate_template(self) -> frozenset[str]:
        if not self.template_path.is_file():
            raise MinutesTemplateError(f"Word 模板不存在：{self.template_path}")
        try:
            document = DocxTemplate(self.template_path)
            variables = frozenset(document.get_undeclared_template_variables())
        except Exception as exc:
            raise MinutesTemplateError(f"Word 模板无法打开：{exc}") from exc

        required = {person.template_key for person in self.people.enabled_people}
        missing = sorted(required - variables)
        if missing:
            raise MinutesTemplateError(
                "Word 模板缺少启用人员占位符：" + "、".join(missing)
            )
        return variables

    def output_path(self, period: str, version: int, generated_at: datetime) -> Path:
        year, week = period.split("-W", 1)
        timestamp = generated_at.strftime("%Y%m%d_%H%M%S")
        filename = f"{year}年第{int(week)}周周例会纪要_v{version}_{timestamp}.docx"
        return self.output_dir / filename

    def render(
        self,
        *,
        period: str,
        version: int,
        generated_at: datetime,
        contents: dict[str, tuple[str, ...]],
    ) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_path(period, version, generated_at)
        temporary = output_path.with_name(f".{output_path.name}.tmp.docx")
        context = {
            person.template_key: numbered_contents(contents.get(person.template_key, ()))
            for person in self.people.people
        }
        try:
            document = DocxTemplate(self.template_path)
            document.render(context, autoescape=True)
            document.save(temporary)
            temporary.replace(output_path)
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            raise MinutesTemplateError(f"Word 纪要生成失败：{exc}") from exc
        return output_path
