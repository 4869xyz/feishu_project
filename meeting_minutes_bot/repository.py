"""Transactional database operations for submissions and generated documents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from .models import (
    Base,
    MeetingDocument,
    MeetingEvent,
    MeetingReminderRun,
    MeetingSubmission,
    utc_now,
)
from .people import Person

REMINDER_PROCESSING_TIMEOUT = timedelta(minutes=10)


@dataclass(frozen=True, slots=True)
class DocumentReservation:
    id: int
    version: int


@dataclass(frozen=True, slots=True)
class ReminderReservation:
    id: int
    reused: bool = False


@dataclass(frozen=True, slots=True)
class SubmissionContent:
    """One active submission used when rendering the weekly DOCX."""

    parsed_content: str
    source_relative_path: str | None = None
    message_type: str = "text"


@dataclass(frozen=True, slots=True)
class DatabaseCleanupResult:
    documents: int
    submissions: int
    events: int
    reminders: int = 0

    @property
    def total(self) -> int:
        return self.documents + self.submissions + self.events + self.reminders


class MeetingRepository:
    def __init__(self, database_url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(database_url)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def initialize(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    async def delete_records_before(self, cutoff) -> DatabaseCleanupResult:
        """Delete expired bot-owned records in one transaction."""

        async with self.sessions.begin() as session:
            documents = await session.execute(
                delete(MeetingDocument).where(MeetingDocument.created_at < cutoff)
            )
            submissions = await session.execute(
                delete(MeetingSubmission).where(MeetingSubmission.created_at < cutoff)
            )
            events = await session.execute(
                delete(MeetingEvent).where(MeetingEvent.created_at < cutoff)
            )
            reminders = await session.execute(
                delete(MeetingReminderRun).where(MeetingReminderRun.created_at < cutoff)
            )
        return DatabaseCleanupResult(
            documents=int(documents.rowcount or 0),
            submissions=int(submissions.rowcount or 0),
            events=int(events.rowcount or 0),
            reminders=int(reminders.rowcount or 0),
        )

    async def vacuum(self) -> None:
        """Ask SQLite to return unused pages to the filesystem."""

        async with self.engine.connect() as connection:
            connection = await connection.execution_options(
                isolation_level="AUTOCOMMIT"
            )
            await connection.exec_driver_sql("VACUUM")

    async def claim_event(self, message_id: str, action: str) -> bool:
        async with self.sessions() as session:
            session.add(MeetingEvent(message_id=message_id, action=action))
            try:
                await session.commit()
                return True
            except IntegrityError:
                await session.rollback()
                return False

    async def finish_event(
        self, message_id: str, *, status: str = "COMPLETED", error: str | None = None
    ) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                update(MeetingEvent)
                .where(MeetingEvent.message_id == message_id)
                .values(status=status, error_message=error, completed_at=utc_now())
            )

    async def add_submission(
        self,
        *,
        message_id: str,
        period: str,
        person: Person,
        raw_content: str,
        parsed_content: str,
        message_type: str,
        mode: str,
        formatted_content: str | None = None,
    ) -> None:
        async with self.sessions.begin() as session:
            if mode == "replace":
                await session.execute(
                    update(MeetingSubmission)
                    .where(
                        MeetingSubmission.meeting_period == period,
                        MeetingSubmission.sender_open_id == person.open_id,
                        MeetingSubmission.is_active.is_(True),
                    )
                    .values(
                        is_active=False,
                        processing_status="REPLACED",
                        updated_at=utc_now(),
                    )
                )
            session.add(
                MeetingSubmission(
                    message_id=message_id,
                    meeting_period=period,
                    sender_open_id=person.open_id,
                    employee_name=person.name,
                    department=person.department,
                    template_key=person.template_key,
                    message_type=message_type,
                    raw_content=raw_content,
                    parsed_content=parsed_content,
                    formatted_content=formatted_content,
                    submit_mode=mode,
                    processing_status="COMPLETED",
                    is_active=True,
                )
            )

    async def withdraw(self, *, period: str, open_id: str) -> int:
        async with self.sessions.begin() as session:
            result = await session.execute(
                update(MeetingSubmission)
                .where(
                    MeetingSubmission.meeting_period == period,
                    MeetingSubmission.sender_open_id == open_id,
                    MeetingSubmission.is_active.is_(True),
                )
                .values(
                    is_active=False,
                    processing_status="WITHDRAWN",
                    updated_at=utc_now(),
                )
            )
            return int(result.rowcount or 0)

    async def active_contents(self, *, period: str, open_id: str) -> tuple[str, ...]:
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    select(
                        MeetingSubmission.parsed_content,
                        MeetingSubmission.formatted_content,
                        MeetingSubmission.message_type,
                    )
                    .where(
                        MeetingSubmission.meeting_period == period,
                        MeetingSubmission.sender_open_id == open_id,
                        MeetingSubmission.is_active.is_(True),
                        MeetingSubmission.processing_status == "COMPLETED",
                    )
                    .order_by(MeetingSubmission.created_at, MeetingSubmission.id)
                )
            ).all()
            labels: list[str] = []
            for parsed, formatted, message_type in rows:
                text = str(parsed or "").strip()
                if formatted and message_type == "docx":
                    if text:
                        labels.append(f"{text}\n（含表格/图片，生成纪要时原样嵌入）")
                    else:
                        labels.append("（Word 含表格/图片，生成纪要时原样嵌入）")
                else:
                    labels.append(text)
            return tuple(labels)

    async def contents_by_template_key(self, period: str) -> dict[str, tuple[str, ...]]:
        """Compatibility helper returning plain-text summaries only."""

        rich = await self.submissions_by_template_key(period)
        return {
            key: tuple(item.parsed_content for item in items)
            for key, items in rich.items()
        }

    async def submissions_by_template_key(
        self, period: str
    ) -> dict[str, tuple[SubmissionContent, ...]]:
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    select(
                        MeetingSubmission.template_key,
                        MeetingSubmission.parsed_content,
                        MeetingSubmission.formatted_content,
                        MeetingSubmission.message_type,
                    )
                    .where(
                        MeetingSubmission.meeting_period == period,
                        MeetingSubmission.is_active.is_(True),
                        MeetingSubmission.processing_status == "COMPLETED",
                    )
                    .order_by(MeetingSubmission.created_at, MeetingSubmission.id)
                )
            ).all()
        grouped: dict[str, list[SubmissionContent]] = {}
        for template_key, parsed, formatted, message_type in rows:
            path = str(formatted).strip() if formatted else None
            if path == "":
                path = None
            grouped.setdefault(template_key, []).append(
                SubmissionContent(
                    parsed_content=str(parsed or ""),
                    source_relative_path=path,
                    message_type=str(message_type or "text"),
                )
            )
        return {key: tuple(values) for key, values in grouped.items()}

    async def submitted_open_ids(self, period: str) -> frozenset[str]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(MeetingSubmission.sender_open_id)
                .where(
                    MeetingSubmission.meeting_period == period,
                    MeetingSubmission.is_active.is_(True),
                    MeetingSubmission.processing_status == "COMPLETED",
                )
                .distinct()
            )
            return frozenset(rows)

    async def reserve_document(self, *, message_id: str, period: str) -> DocumentReservation:
        async with self.sessions.begin() as session:
            latest = await session.scalar(
                select(func.max(MeetingDocument.version)).where(
                    MeetingDocument.meeting_period == period
                )
            )
            document = MeetingDocument(
                message_id=message_id,
                meeting_period=period,
                version=int(latest or 0) + 1,
                status="GENERATING",
            )
            session.add(document)
            await session.flush()
            return DocumentReservation(id=document.id, version=document.version)

    async def finish_document(
        self,
        document_id: int,
        *,
        status: str,
        output_path: Path | None = None,
        error: str | None = None,
    ) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                update(MeetingDocument)
                .where(MeetingDocument.id == document_id)
                .values(
                    status=status,
                    output_path=str(output_path) if output_path is not None else None,
                    error_message=error,
                    updated_at=utc_now(),
                )
            )

    async def document_by_message_id(self, message_id: str) -> MeetingDocument | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(MeetingDocument).where(MeetingDocument.message_id == message_id)
            )

    async def submissions_for_person(
        self, *, period: str, open_id: str
    ) -> tuple[MeetingSubmission, ...]:
        """Return complete history for diagnostics and tests."""

        async with self.sessions() as session:
            rows = await session.scalars(
                select(MeetingSubmission)
                .where(
                    MeetingSubmission.meeting_period == period,
                    MeetingSubmission.sender_open_id == open_id,
                )
                .order_by(MeetingSubmission.id)
            )
            return tuple(rows)

    async def claim_reminder_run(
        self,
        *,
        period: str,
        slot: str,
        processing_timeout: timedelta = REMINDER_PROCESSING_TIMEOUT,
        now: datetime | None = None,
    ) -> ReminderReservation | None:
        """Claim a reminder wave. Returns None when the wave already completed."""

        current = now or utc_now()
        async with self.sessions() as session:
            existing = await session.scalar(
                select(MeetingReminderRun).where(
                    MeetingReminderRun.meeting_period == period,
                    MeetingReminderRun.slot == slot,
                )
            )
            if existing is None:
                run = MeetingReminderRun(
                    meeting_period=period,
                    slot=slot,
                    status="PROCESSING",
                    created_at=current,
                    updated_at=current,
                )
                session.add(run)
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    existing = await session.scalar(
                        select(MeetingReminderRun).where(
                            MeetingReminderRun.meeting_period == period,
                            MeetingReminderRun.slot == slot,
                        )
                    )
                    if existing is None:
                        return None
                else:
                    return ReminderReservation(id=run.id)

            if existing.status == "COMPLETED":
                return None

            stale_before = current - processing_timeout
            updated_at = existing.updated_at or existing.created_at
            if existing.status == "PROCESSING" and updated_at > stale_before:
                return None

            existing.status = "PROCESSING"
            existing.attempted = 0
            existing.sent = 0
            existing.failed = 0
            existing.error_message = None
            existing.updated_at = current
            existing.completed_at = None
            await session.commit()
            return ReminderReservation(id=existing.id, reused=True)

    async def finish_reminder_run(
        self,
        run_id: int,
        *,
        status: str,
        attempted: int = 0,
        sent: int = 0,
        failed: int = 0,
        error: str | None = None,
    ) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                update(MeetingReminderRun)
                .where(MeetingReminderRun.id == run_id)
                .values(
                    status=status,
                    attempted=attempted,
                    sent=sent,
                    failed=failed,
                    error_message=error,
                    updated_at=utc_now(),
                    completed_at=utc_now() if status == "COMPLETED" else None,
                )
            )

    async def reminder_run(
        self, *, period: str, slot: str
    ) -> MeetingReminderRun | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(MeetingReminderRun).where(
                    MeetingReminderRun.meeting_period == period,
                    MeetingReminderRun.slot == slot,
                )
            )
