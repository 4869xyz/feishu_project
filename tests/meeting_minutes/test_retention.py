from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from logging.handlers import TimedRotatingFileHandler
import os
from pathlib import Path

import pytest
from sqlalchemy import func, select

from meeting_minutes_bot.models import MeetingDocument, MeetingEvent, MeetingSubmission
from meeting_minutes_bot.repository import MeetingRepository
from meeting_minutes_bot.retention import RetentionCleaner, cleanup_directory
from meeting_minutes_bot.runtime import configure_logging
from meeting_minutes_bot.settings import MeetingBotSettings


def make_settings(root: Path, database_url: str) -> MeetingBotSettings:
    return MeetingBotSettings(
        app_id="app",
        app_secret="secret",
        database_url=database_url,
        people_config_path=root / "people.yaml",
        template_path=root / "template.docx",
        data_dir=root / "data",
        output_dir=root / "data" / "output",
        log_dir=root / "logs",
        timezone="Asia/Shanghai",
        max_text_length=20_000,
        log_level="INFO",
        attachment_dir=root / "data" / "attachments",
        retention_days=14,
    )


def set_mtime(path: Path, moment: datetime) -> None:
    timestamp = moment.replace(tzinfo=timezone.utc).timestamp()
    os.utime(path, (timestamp, timestamp))


def test_cleanup_directory_uses_strict_cutoff_and_stays_inside_root(
    project_tmp_dir: Path,
) -> None:
    root = project_tmp_dir / "attachments"
    root.mkdir()
    now = datetime(2026, 8, 6, 12, 0)
    cutoff = now - timedelta(days=14)
    expired = root / "expired.pdf"
    exact = root / "exact.docx"
    fresh = root / "fresh.md"
    outside = project_tmp_dir / "outside.txt"
    for path in (expired, exact, fresh, outside):
        path.write_text(path.name, encoding="utf-8")
    set_mtime(expired, cutoff - timedelta(seconds=1))
    set_mtime(exact, cutoff)
    set_mtime(fresh, cutoff + timedelta(seconds=1))
    set_mtime(outside, cutoff - timedelta(days=1))

    result = cleanup_directory(root, cutoff)

    assert result.deleted == 1
    assert not expired.exists()
    assert exact.exists() and fresh.exists() and outside.exists()


def test_cleanup_directory_does_not_follow_directory_symlinks(
    project_tmp_dir: Path,
) -> None:
    root = project_tmp_dir / "attachments"
    outside = project_tmp_dir / "outside"
    root.mkdir()
    outside.mkdir()
    external_file = outside / "keep.txt"
    external_file.write_text("keep", encoding="utf-8")
    set_mtime(external_file, datetime(2020, 1, 1))
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("当前系统不允许创建目录符号链接")

    result = cleanup_directory(root, datetime(2026, 1, 1))

    assert result.deleted == 0
    assert external_file.exists()


def test_retention_cleaner_deletes_old_files_and_database_rows(
    project_tmp_dir: Path,
) -> None:
    async def scenario() -> None:
        database = project_tmp_dir / "meeting.db"
        database_url = f"sqlite+aiosqlite:///{database.as_posix()}"
        repository = MeetingRepository(database_url)
        await repository.initialize()
        settings = make_settings(project_tmp_dir, database_url)
        settings.attachment_dir.mkdir(parents=True)
        settings.output_dir.mkdir(parents=True)
        now = datetime(2026, 8, 6, 12, 0)
        cutoff = now - timedelta(days=14)
        old_file = settings.attachment_dir / "old.pdf"
        old_word = settings.output_dir / "old.docx"
        fresh_word = settings.output_dir / "fresh.docx"
        for path in (old_file, old_word, fresh_word):
            path.write_bytes(b"content")
        set_mtime(old_file, cutoff - timedelta(seconds=1))
        set_mtime(old_word, cutoff - timedelta(seconds=1))
        set_mtime(fresh_word, cutoff)

        old = cutoff - timedelta(seconds=1)
        async with repository.sessions.begin() as session:
            session.add_all(
                [
                    MeetingEvent(message_id="old-event", action="submit", created_at=old),
                    MeetingEvent(message_id="fresh-event", action="submit", created_at=cutoff),
                    MeetingSubmission(
                        message_id="old-submission", meeting_period="2026-W29",
                        sender_open_id="ou_old", employee_name="Old",
                        department="Dept", template_key="old", message_type="text",
                        raw_content="old", parsed_content="old", submit_mode="append",
                        created_at=old,
                    ),
                    MeetingSubmission(
                        message_id="fresh-submission", meeting_period="2026-W30",
                        sender_open_id="ou_fresh", employee_name="Fresh",
                        department="Dept", template_key="fresh", message_type="text",
                        raw_content="fresh", parsed_content="fresh", submit_mode="append",
                        created_at=cutoff,
                    ),
                    MeetingDocument(
                        message_id="old-document", meeting_period="2026-W29",
                        version=1, output_path=str(old_word), status="COMPLETED",
                        created_at=old,
                    ),
                    MeetingDocument(
                        message_id="fresh-document", meeting_period="2026-W30",
                        version=1, output_path=str(fresh_word), status="COMPLETED",
                        created_at=cutoff,
                    ),
                ]
            )

        try:
            result = await RetentionCleaner(
                repository=repository, settings=settings
            ).run_once(now=now)
            assert result.attachments.deleted == 1
            assert result.documents.deleted == 1
            assert result.database.total == 3
            assert not old_file.exists() and not old_word.exists()
            assert fresh_word.exists()
            async with repository.sessions() as session:
                assert await session.scalar(select(func.count(MeetingEvent.message_id))) == 1
                assert await session.scalar(select(func.count(MeetingSubmission.id))) == 1
                assert await session.scalar(select(func.count(MeetingDocument.id))) == 1
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_logging_rotates_daily_with_retention_count(project_tmp_dir: Path) -> None:
    settings = make_settings(project_tmp_dir, "sqlite+aiosqlite:///:memory:")
    settings.log_dir.mkdir(parents=True)
    configure_logging(settings)
    try:
        rotating = [
            handler
            for handler in logging.getLogger().handlers
            if isinstance(handler, TimedRotatingFileHandler)
        ]
        assert len(rotating) == 1
        assert rotating[0].backupCount == 14
        assert rotating[0].interval == 24 * 60 * 60
    finally:
        for handler in tuple(logging.getLogger().handlers):
            handler.close()
            logging.getLogger().removeHandler(handler)


def test_cleanup_failures_do_not_escape(project_tmp_dir: Path, monkeypatch) -> None:
    class BrokenRepository:
        async def delete_records_before(self, cutoff):
            raise RuntimeError("database unavailable")

        async def vacuum(self):
            raise AssertionError("vacuum must not run")

    def broken_cleanup(root, cutoff):
        raise PermissionError("directory unavailable")

    monkeypatch.setattr("meeting_minutes_bot.retention.cleanup_directory", broken_cleanup)
    settings = make_settings(project_tmp_dir, "sqlite+aiosqlite:///:memory:")

    result = asyncio.run(
        RetentionCleaner(repository=BrokenRepository(), settings=settings).run_once()
    )

    assert result.attachments.failed == 1
    assert result.documents.failed == 1
    assert result.database_failed is True
    assert result.database.total == 0


def test_periodic_cleanup_runs_and_cancels_cleanly(project_tmp_dir: Path) -> None:
    class UnusedRepository:
        pass

    async def scenario() -> None:
        cleaner = RetentionCleaner(
            repository=UnusedRepository(),
            settings=make_settings(project_tmp_dir, "sqlite+aiosqlite:///:memory:"),
            interval_seconds=0.01,
        )
        called = asyncio.Event()

        async def fake_run_once(*, now=None):
            called.set()

        cleaner.run_once = fake_run_once
        task = asyncio.create_task(cleaner.run_forever())
        await asyncio.wait_for(called.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
