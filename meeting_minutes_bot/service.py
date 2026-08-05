"""Application service implementing the text-only weekly-minutes workflow."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .document import MinutesDocumentRenderer
from .people import PeopleDirectory, Person
from .period import local_datetime, meeting_period, period_label
from .repository import MeetingRepository


VIEW_COMMAND = "查看我的纪要"
WITHDRAW_COMMAND = "撤回本周提交"
GENERATE_COMMAND = "生成本周纪要"
STATUS_COMMAND = "查看本周提交状态"
ADMIN_COMMANDS = frozenset({GENERATE_COMMAND, STATUS_COMMAND})


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
        people: PeopleDirectory,
        renderer: MinutesDocumentRenderer,
        timezone: str = "Asia/Shanghai",
        max_text_length: int = 20_000,
    ) -> None:
        self.repository = repository
        self.people = people
        self.renderer = renderer
        self.timezone = timezone
        self.max_text_length = max_text_length
        self._generation_lock = asyncio.Lock()

    def _person_or_reply(self, open_id: str) -> tuple[Person | None, ServiceResult | None]:
        person = self.people.find(open_id)
        if person is None:
            return None, ServiceResult(
                "你的飞书账号尚未绑定到周例会纪要人员名单，请联系管理员完成绑定。"
            )
        if not person.enabled:
            return None, ServiceResult("你的周例会纪要账号当前已停用，请联系管理员。")
        return person, None

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
        if len(content) > self.max_text_length:
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
                content=content,
                mode=mode,
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
            "消息类型：文字"
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
                contents = await self.repository.contents_by_template_key(period)
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
