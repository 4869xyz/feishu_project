"""Automatic retention cleanup for meeting-minutes bot data."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path

from .docx_merge import submission_docs_root
from .repository import DatabaseCleanupResult, MeetingRepository
from .settings import MeetingBotSettings


LOGGER = logging.getLogger(__name__)
DAILY_CLEANUP_SECONDS = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class FileCleanupResult:
    deleted: int = 0
    released_bytes: int = 0
    failed: int = 0


@dataclass(frozen=True, slots=True)
class RetentionCleanupResult:
    cutoff: datetime
    attachments: FileCleanupResult
    documents: FileCleanupResult
    submission_docs: FileCleanupResult = FileCleanupResult()
    database: DatabaseCleanupResult = DatabaseCleanupResult(0, 0, 0)
    database_failed: bool = False
    vacuum_failed: bool = False


def cleanup_directory(root: Path, cutoff: datetime) -> FileCleanupResult:
    """Delete old regular files below an exact trusted root without following links."""

    trusted_root = root.resolve()
    if not trusted_root.is_dir():
        return FileCleanupResult()

    cutoff_timestamp = (
        cutoff.replace(tzinfo=timezone.utc).timestamp()
        if cutoff.tzinfo is None
        else cutoff.timestamp()
    )
    deleted = released_bytes = failed = 0
    for current, directories, filenames in os.walk(trusted_root, followlinks=False):
        current_path = Path(current)
        directories[:] = [
            name for name in directories if not (current_path / name).is_symlink()
        ]
        for filename in filenames:
            path = current_path / filename
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(trusted_root):
                    continue
                stat = path.stat()
                if stat.st_mtime >= cutoff_timestamp:
                    continue
                size = stat.st_size
                path.unlink()
                deleted += 1
                released_bytes += size
            except OSError:
                failed += 1
                LOGGER.exception("清理过期文件失败：%s", path)
    return FileCleanupResult(deleted, released_bytes, failed)


class RetentionCleaner:
    def __init__(
        self,
        *,
        repository: MeetingRepository,
        settings: MeetingBotSettings,
        interval_seconds: float = DAILY_CLEANUP_SECONDS,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.interval_seconds = interval_seconds

    async def _cleanup_files(
        self, root: Path, label: str, cutoff: datetime
    ) -> FileCleanupResult:
        try:
            return await asyncio.to_thread(cleanup_directory, root, cutoff)
        except Exception:
            LOGGER.exception("清理过期%s文件失败：%s", label, root)
            return FileCleanupResult(failed=1)

    async def run_once(self, *, now: datetime | None = None) -> RetentionCleanupResult:
        current = now or datetime.utcnow()
        cutoff = current - timedelta(days=self.settings.retention_days)
        attachment_dir = self.settings.attachment_dir or (
            self.settings.data_dir / "attachments"
        )

        attachments = await self._cleanup_files(attachment_dir, "附件", cutoff)
        documents = await self._cleanup_files(
            self.settings.output_dir, "Word", cutoff
        )
        submission_docs = await self._cleanup_files(
            submission_docs_root(self.settings.data_dir), "提交源Word", cutoff
        )

        database = DatabaseCleanupResult(0, 0, 0)
        database_failed = False
        vacuum_failed = False
        try:
            database = await self.repository.delete_records_before(cutoff)
        except Exception:
            database_failed = True
            LOGGER.exception("清理过期纪要数据库记录失败")
        if database.total:
            try:
                await self.repository.vacuum()
            except Exception:
                vacuum_failed = True
                LOGGER.exception("回收纪要数据库空闲空间失败")

        result = RetentionCleanupResult(
            cutoff=cutoff,
            attachments=attachments,
            documents=documents,
            submission_docs=submission_docs,
            database=database,
            database_failed=database_failed,
            vacuum_failed=vacuum_failed,
        )
        LOGGER.info(
            "纪要保留期清理完成：cutoff=%s，附件=%d，Word=%d，提交源Word=%d，数据库=%d，"
            "释放=%d bytes，文件失败=%d，数据库失败=%s，VACUUM失败=%s",
            cutoff.isoformat(),
            attachments.deleted,
            documents.deleted,
            submission_docs.deleted,
            database.total,
            (
                attachments.released_bytes
                + documents.released_bytes
                + submission_docs.released_bytes
            ),
            attachments.failed + documents.failed + submission_docs.failed,
            database_failed,
            vacuum_failed,
        )
        return result

    async def run_forever(self) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("纪要定时清理出现未预期异常，将在下一周期重试")
