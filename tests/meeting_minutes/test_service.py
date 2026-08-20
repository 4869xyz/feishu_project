from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from docx import Document

from meeting_minutes_bot.service import GENERATE_COMMAND, STATUS_COMMAND
from tests.meeting_minutes.helpers import build_service


NOW = datetime(2026, 8, 5, 12, 0)


def test_append_replace_view_withdraw_and_idempotency(project_tmp_dir: Path) -> None:
    async def scenario() -> None:
        service, repository = await build_service(project_tmp_dir)
        try:
            first = await service.handle_text(
                message_id="om_1", sender_open_id="ou_yang", text="完成项目 A", received_at=NOW
            )
            duplicate = await service.handle_text(
                message_id="om_1", sender_open_id="ou_yang", text="完成项目 A", received_at=NOW
            )
            await service.handle_text(
                message_id="om_2", sender_open_id="ou_yang", text="补充项目 B", received_at=NOW
            )
            view = await service.handle_text(
                message_id="om_view", sender_open_id="ou_yang", text="查看我的纪要", received_at=NOW
            )
            replacement = await service.handle_text(
                message_id="om_3", sender_open_id="ou_yang", text="替换：最终版本", received_at=NOW
            )
            after_replace = await repository.submissions_for_person(
                period="2026-W32", open_id="ou_yang"
            )
            withdrawn = await service.handle_text(
                message_id="om_4", sender_open_id="ou_yang", text="撤回本周提交", received_at=NOW
            )
            repeated_withdraw = await service.handle_text(
                message_id="om_4", sender_open_id="ou_yang", text="撤回本周提交", received_at=NOW
            )

            assert "处理方式：追加" in first.text
            assert duplicate.duplicate
            assert "1. 完成项目 A" in view.text and "2. 补充项目 B" in view.text
            assert "处理方式：替换" in replacement.text
            assert [row.processing_status for row in after_replace] == [
                "REPLACED",
                "REPLACED",
                "COMPLETED",
            ]
            assert [row.is_active for row in after_replace] == [False, False, True]
            assert "1 条" in withdrawn.text
            assert repeated_withdraw.duplicate
            assert await repository.active_contents(period="2026-W32", open_id="ou_yang") == ()
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_identity_validation_permissions_and_text_limits(project_tmp_dir: Path) -> None:
    async def scenario() -> None:
        service, repository = await build_service(project_tmp_dir)
        try:
            unbound = await service.handle_text(
                message_id="om_u", sender_open_id="ou_unknown", text="内容", received_at=NOW
            )
            disabled = await service.handle_text(
                message_id="om_d", sender_open_id="ou_disabled", text="内容", received_at=NOW
            )
            denied = await service.handle_text(
                message_id="om_p", sender_open_id="ou_yang", text=GENERATE_COMMAND, received_at=NOW
            )
            empty = await service.handle_text(
                message_id="om_e", sender_open_id="ou_yang", text="替换：", received_at=NOW
            )
            long = await service.handle_text(
                message_id="om_l", sender_open_id="ou_yang", text="x" * 101, received_at=NOW
            )

            assert "尚未绑定" in unbound.text
            assert "已停用" in disabled.text
            assert "没有执行" in denied.text
            assert "不能为空" in empty.text
            assert "超过 100 字" in long.text
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_admin_status_and_versioned_word_generation(project_tmp_dir: Path) -> None:
    async def scenario() -> None:
        service, repository = await build_service(project_tmp_dir)
        try:
            await service.handle_text(
                message_id="om_a", sender_open_id="ou_yang", text="完成 A", received_at=NOW
            )
            status = await service.handle_text(
                message_id="om_s", sender_open_id="ou_admin", text=STATUS_COMMAND, received_at=NOW
            )
            first = await service.handle_text(
                message_id="om_g1", sender_open_id="ou_admin", text=GENERATE_COMMAND, received_at=NOW
            )
            duplicate = await service.handle_text(
                message_id="om_g1", sender_open_id="ou_admin", text=GENERATE_COMMAND, received_at=NOW
            )
            second = await service.handle_text(
                message_id="om_g2", sender_open_id="ou_admin", text=GENERATE_COMMAND, received_at=NOW
            )

            assert "已提交：1 人（杨意林）" in status.text
            assert "未提交：1 人（吴傲翔）" in status.text
            assert first.file_path is not None and "_v1_" in first.file_path.name
            assert duplicate.duplicate and duplicate.file_path == first.file_path
            assert second.file_path is not None and "_v2_" in second.file_path.name
            document = Document(first.file_path)
            rendered_text = "\n".join(p.text for p in document.paragraphs)
            assert "1. 完成 A" in rendered_text
            assert "本周未提交" in rendered_text
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_concurrent_submissions_do_not_overwrite_each_other(project_tmp_dir: Path) -> None:
    async def scenario() -> None:
        service, repository = await build_service(project_tmp_dir)
        try:
            await asyncio.gather(
                *(
                    service.handle_text(
                        message_id=f"om_{index}",
                        sender_open_id="ou_yang",
                        text=f"内容 {index}",
                        received_at=NOW,
                    )
                    for index in range(10)
                )
            )
            contents = await repository.active_contents(
                period="2026-W32", open_id="ou_yang"
            )
            assert len(contents) == 10
            assert set(contents) == {f"内容 {index}" for index in range(10)}
        finally:
            await repository.close()

    asyncio.run(scenario())
