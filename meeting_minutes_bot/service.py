"""Application service implementing the text-only weekly-minutes workflow."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .attachments import ExtractedAttachment
from .config_update import (
    ConfigUpdateError,
    apply_people_yaml,
    apply_template_docx,
    classify_admin_config_upload,
    describe_config_health,
)
from .document import MinutesDocumentRenderer, MinutesTemplateError
from .docx_merge import persist_submission_docx
from .people import (
    PeopleConfigurationError,
    PeopleDirectory,
    PeopleStore,
    Person,
    ensure_store,
)
from .period import local_datetime, meeting_period, period_label
from .repository import MeetingRepository


VIEW_COMMAND = "查看我的纪要"
WITHDRAW_COMMAND = "撤回本周提交"
GENERATE_COMMAND = "生成本周纪要"
STATUS_COMMAND = "查看本周提交状态"
RELOAD_COMMAND = "重载人员配置"
VALIDATE_COMMAND = "校验配置"
ADMIN_COMMANDS = frozenset(
    {GENERATE_COMMAND, STATUS_COMMAND, RELOAD_COMMAND, VALIDATE_COMMAND}
)


@dataclass(frozen=True, slots=True)
class ServiceResult:
    text: str
    file_path: Path | None = None
    duplicate: bool = False


class MeetingMinutesService:
    def __init__(
        self,
        *,
        repository: MeetingRepository,
        people: PeopleDirectory | PeopleStore,
        renderer: MinutesDocumentRenderer,
        timezone: str = "Asia/Shanghai",
        max_text_length: int = 20_000,
        data_dir: str | Path | None = None,
    ) -> None:
        self.repository = repository
        self._people_store = ensure_store(people)
        self.renderer = renderer
        self.timezone = timezone
        self.max_text_length = max_text_length
        self.data_dir = (
            Path(data_dir).resolve()
            if data_dir is not None
            else Path(renderer.data_dir).resolve()
        )
        self._generation_lock = asyncio.Lock()
        self._config_lock = asyncio.Lock()

    @property
    def people(self) -> PeopleDirectory:
        return self._people_store.directory

    def _person_or_reply(self, open_id: str) -> tuple[Person | None, ServiceResult | None]:
        person = self.people.find(open_id)
        if person is None:
            return None, ServiceResult(
                "你的飞书账号尚未绑定到周例会纪要人员名单，请联系管理员完成绑定。"
            )
        if not person.enabled:
            return None, ServiceResult("你的周例会纪要账号当前已停用，请联系管理员。")
        return person, None

    def submission_rejection(self, open_id: str) -> ServiceResult | None:
        """Return a user-facing rejection before an attachment is downloaded."""

        _, rejection = self._person_or_reply(open_id)
        return rejection

    def classify_config_upload(self, open_id: str, file_name: str) -> str | None:
        """Classify an inbound file as people/template config for admins."""

        return classify_admin_config_upload(
            file_name=file_name,
            template_path=self.renderer.template_path,
            is_admin=self.people.is_admin(open_id),
        )

    async def handle_text(
        self,
        *,
        message_id: str,
        sender_open_id: str,
        text: str,
        received_at: datetime | None = None,
    ) -> ServiceResult:
        cleaned = text.strip()
        now = local_datetime(received_at, self.timezone)
        period = meeting_period(now, self.timezone)

        if cleaned in ADMIN_COMMANDS:
            if not self.people.is_admin(sender_open_id):
                return ServiceResult(f"你没有执行“{cleaned}”的权限。")
            if cleaned == STATUS_COMMAND:
                return await self._status(period)
            if cleaned == RELOAD_COMMAND:
                return await self._reload_people()
            if cleaned == VALIDATE_COMMAND:
                return await self._validate_config()
            return await self._generate(message_id, period, now)

        person, rejection = self._person_or_reply(sender_open_id)
        if rejection is not None or person is None:
            return rejection or ServiceResult("人员身份校验失败。")

        if cleaned == VIEW_COMMAND:
            return await self._view(person, period)
        if cleaned == WITHDRAW_COMMAND:
            return await self._withdraw(message_id, person, period)

        mode = "append"
        content = cleaned
        if cleaned.startswith("替换：") or cleaned.startswith("替换:"):
            mode = "replace"
            content = cleaned[3:].strip()
        if not content:
            return ServiceResult("纪要内容不能为空。")
        return await self._submit_content(
            message_id=message_id,
            person=person,
            period=period,
            raw_content=content,
            parsed_content=content,
            message_type="text",
            mode=mode,
        )

    async def handle_config_upload(
        self,
        *,
        sender_open_id: str,
        file_name: str,
        source_path: Path,
        kind: str | None = None,
    ) -> ServiceResult:
        """Apply an admin-uploaded people YAML or Word template."""

        if not self.people.is_admin(sender_open_id):
            return ServiceResult("你没有上传配置文件的权限。")
        resolved_kind = kind or self.classify_config_upload(sender_open_id, file_name)
        if resolved_kind is None:
            return ServiceResult(
                "未识别为配置更新。人员请上传 .yaml/.yml；"
                f"模板请使用与正式模板同名的文件（当前：{self.renderer.template_path.name}）。"
            )
        if not source_path.is_file():
            return ServiceResult("配置文件下载失败，请重新上传。")

        async with self._config_lock:
            try:
                if resolved_kind == "people":
                    result = await asyncio.to_thread(
                        apply_people_yaml,
                        uploaded_path=source_path,
                        people_store=self._people_store,
                        renderer=self.renderer,
                        data_dir=self.data_dir,
                        sender_open_id=sender_open_id,
                    )
                else:
                    result = await asyncio.to_thread(
                        apply_template_docx,
                        uploaded_path=source_path,
                        renderer=self.renderer,
                        data_dir=self.data_dir,
                    )
            except (
                ConfigUpdateError,
                PeopleConfigurationError,
                MinutesTemplateError,
                OSError,
            ) as exc:
                return ServiceResult(f"配置更新失败，仍使用原配置：{exc}")
        return ServiceResult(result.summary)

    async def handle_attachment(
        self,
        *,
        message_id: str,
        sender_open_id: str,
        attachment: ExtractedAttachment,
        received_at: datetime | None = None,
    ) -> ServiceResult:
        now = local_datetime(received_at, self.timezone)
        period = meeting_period(now, self.timezone)

        config_kind = None
        if attachment.source_path is not None:
            config_kind = self.classify_config_upload(
                sender_open_id, attachment.file_name
            )
        if config_kind is not None and attachment.source_path is not None:
            return await self.handle_config_upload(
                sender_open_id=sender_open_id,
                file_name=attachment.file_name,
                source_path=attachment.source_path,
                kind=config_kind,
            )

        person, rejection = self._person_or_reply(sender_open_id)
        if rejection is not None or person is None:
            return rejection or ServiceResult("人员身份校验失败。")

        formatted_content: str | None = None
        if (
            attachment.message_type == "docx"
            and attachment.source_path is not None
            and attachment.source_path.is_file()
        ):
            formatted_content = await asyncio.to_thread(
                persist_submission_docx,
                source=attachment.source_path,
                data_dir=self.data_dir,
                period=period,
                message_id=message_id,
            )

        result = await self._submit_content(
            message_id=message_id,
            person=person,
            period=period,
            raw_content=attachment.raw_content,
            parsed_content=attachment.parsed_content,
            message_type=attachment.message_type,
            mode="append",
            formatted_content=formatted_content,
            allow_empty_text=attachment.has_embedded_media,
        )
        if result.duplicate or not result.text.startswith("已收到"):
            return result
        return ServiceResult(
            "已识别并追加你的周例会纪要。\n"
            f"姓名：{person.name}\n"
            f"部门：{person.department}\n"
            f"周期：{period_label(period)}\n"
            f"文件：{attachment.file_name}\n"
            f"类型：{attachment.message_type.upper()}\n"
            f"识别方式：{attachment.recognition_method}\n"
            f"识别字数：{attachment.character_count}\n"
            f"内容预览：{attachment.preview}"
        )

    async def _submit_content(
        self,
        *,
        message_id: str,
        person: Person,
        period: str,
        raw_content: str,
        parsed_content: str,
        message_type: str,
        mode: str,
        formatted_content: str | None = None,
        allow_empty_text: bool = False,
    ) -> ServiceResult:
        if not parsed_content and not allow_empty_text:
            return ServiceResult("纪要内容不能为空。")
        if len(parsed_content) > self.max_text_length:
            return ServiceResult(
                f"纪要内容超过 {self.max_text_length} 字限制，请拆分后重新发送。"
            )

        action = "REPLACE" if mode == "replace" else "APPEND"
        if not await self.repository.claim_event(message_id, action):
            return ServiceResult("该消息已经处理，请勿重复提交。", duplicate=True)
        try:
            await self.repository.add_submission(
                message_id=message_id,
                period=period,
                person=person,
                raw_content=raw_content,
                parsed_content=parsed_content,
                message_type=message_type,
                mode=mode,
                formatted_content=formatted_content,
            )
        except Exception as exc:
            await self.repository.finish_event(message_id, status="FAILED", error=str(exc))
            raise
        await self.repository.finish_event(message_id)
        return ServiceResult(
            "已收到你的周例会纪要。\n"
            f"姓名：{person.name}\n"
            f"部门：{person.department}\n"
            f"周期：{period_label(period)}\n"
            f"处理方式：{'替换' if mode == 'replace' else '追加'}\n"
            f"消息类型：{'文字' if message_type == 'text' else message_type.upper()}"
        )

    async def _view(self, person: Person, period: str) -> ServiceResult:
        contents = await self.repository.active_contents(
            period=period, open_id=person.open_id
        )
        if not contents:
            return ServiceResult(f"{period_label(period)}你还没有有效的纪要内容。")
        numbered = "\n".join(
            f"{index}. {content}" for index, content in enumerate(contents, 1)
        )
        return ServiceResult(f"{period_label(period)}你的有效纪要：\n{numbered}")

    async def _withdraw(
        self, message_id: str, person: Person, period: str
    ) -> ServiceResult:
        if not await self.repository.claim_event(message_id, "WITHDRAW"):
            return ServiceResult("该撤回指令已经处理。", duplicate=True)
        try:
            count = await self.repository.withdraw(period=period, open_id=person.open_id)
        except Exception as exc:
            await self.repository.finish_event(message_id, status="FAILED", error=str(exc))
            raise
        await self.repository.finish_event(message_id)
        if count == 0:
            return ServiceResult(f"{period_label(period)}没有可撤回的纪要内容。")
        return ServiceResult(f"已撤回{period_label(period)}的 {count} 条有效纪要。")

    async def _status(self, period: str) -> ServiceResult:
        submitted_ids = await self.repository.submitted_open_ids(period)
        submitted = [
            person.name
            for person in self.people.enabled_people
            if person.open_id in submitted_ids
        ]
        missing = [
            person.name
            for person in self.people.enabled_people
            if person.open_id not in submitted_ids
        ]
        submitted_text = "、".join(submitted) if submitted else "无"
        missing_text = "、".join(missing) if missing else "无"
        return ServiceResult(
            f"{period_label(period)}提交状态\n"
            f"已提交：{len(submitted)} 人（{submitted_text}）\n"
            f"未提交：{len(missing)} 人（{missing_text}）"
        )

    async def _reload_people(self) -> ServiceResult:
        """Re-read people.yaml, validate the template, then hot-swap the store."""

        async with self._config_lock:
            try:
                candidate = await asyncio.to_thread(self._people_store.load_candidate)
                await asyncio.to_thread(self.renderer.validate_template, candidate)
            except (PeopleConfigurationError, MinutesTemplateError) as exc:
                return ServiceResult(f"人员配置重载失败，仍使用原配置：{exc}")
            self._people_store.replace(candidate)
        enabled = candidate.enabled_people
        return ServiceResult(
            "人员配置已重载。\n"
            f"总人数：{len(candidate.people)}\n"
            f"启用人数：{len(enabled)}\n"
            f"管理员数：{len(candidate.admins)}\n"
            f"启用人员：{'、'.join(person.name for person in enabled) or '无'}"
        )

    async def _validate_config(self) -> ServiceResult:
        text = await asyncio.to_thread(
            describe_config_health,
            people_store=self._people_store,
            renderer=self.renderer,
        )
        return ServiceResult(text)

    async def _generate(
        self, message_id: str, period: str, generated_at: datetime
    ) -> ServiceResult:
        if not await self.repository.claim_event(message_id, "GENERATE"):
            existing = await self.repository.document_by_message_id(message_id)
            path = (
                Path(existing.output_path)
                if existing is not None
                and existing.status == "COMPLETED"
                and existing.output_path
                else None
            )
            return ServiceResult("该生成指令已经处理。", file_path=path, duplicate=True)

        async with self._generation_lock:
            reservation = None
            try:
                reservation = await self.repository.reserve_document(
                    message_id=message_id, period=period
                )
                contents = await self.repository.submissions_by_template_key(period)
                output_path = await asyncio.to_thread(
                    self.renderer.render,
                    period=period,
                    version=reservation.version,
                    generated_at=generated_at,
                    contents=contents,
                )
            except Exception as exc:
                if reservation is not None:
                    await self.repository.finish_document(
                        reservation.id, status="FAILED", error=str(exc)
                    )
                await self.repository.finish_event(
                    message_id, status="FAILED", error=str(exc)
                )
                return ServiceResult(f"本周纪要生成失败：{exc}")

        await self.repository.finish_document(
            reservation.id, status="COMPLETED", output_path=output_path
        )
        await self.repository.finish_event(message_id)
        return ServiceResult(
            f"{period_label(period)}纪要已生成：v{reservation.version}",
            file_path=output_path,
        )
