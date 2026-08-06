"""Transactional database operations for submissions and generated documents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from .models import Base, MeetingDocument, MeetingEvent, MeetingSubmission, utc_now
from .people import Person


@dataclass(frozen=True, slots=True)
class DocumentReservation:
    id: int
    version: int


@dataclass(frozen=True, slots=True)
class DatabaseCleanupResult:
    documents: int
    submissions: int
    events: int

    @property
    def total(self) -> int:
        return self.documents + self.submissions + self.events


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
        return DatabaseCleanupResult(
            documents=int(documents.rowcount or 0),
            submissions=int(submissions.rowcount or 0),
            events=int(events.rowcount or 0),
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
            rows = await session.scalars(
                select(MeetingSubmission.parsed_content)
                .where(
                    MeetingSubmission.meeting_period == period,
                    MeetingSubmission.sender_open_id == open_id,
                    MeetingSubmission.is_active.is_(True),
                    MeetingSubmission.processing_status == "COMPLETED",
                )
                .order_by(MeetingSubmission.created_at, MeetingSubmission.id)
            )
            return tuple(rows)

    async def contents_by_template_key(self, period: str) -> dict[str, tuple[str, ...]]:
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    select(
                        MeetingSubmission.template_key,
                        MeetingSubmission.parsed_content,
                    )
                    .where(
                        MeetingSubmission.meeting_period == period,
                        MeetingSubmission.is_active.is_(True),
                        MeetingSubmission.processing_status == "COMPLETED",
                    )
                    .order_by(MeetingSubmission.created_at, MeetingSubmission.id)
                )
            ).all()
        grouped: dict[str, list[str]] = {}
        for template_key, content in rows:
            grouped.setdefault(template_key, []).append(content)
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
