"""SQLAlchemy persistence models owned only by the meeting-minutes bot."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.utcnow()


class Base(DeclarativeBase):
    pass


class MeetingEvent(Base):
    __tablename__ = "meeting_events"

    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PROCESSING")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MeetingSubmission(Base):
    __tablename__ = "meeting_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    meeting_period: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    sender_open_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    employee_name: Mapped[str] = mapped_column(String(128), nullable=False)
    department: Mapped[str] = mapped_column(String(128), nullable=False)
    template_key: Mapped[str] = mapped_column(String(128), nullable=False)
    message_type: Mapped[str] = mapped_column(String(16), nullable=False, default="text")
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_content: Mapped[str] = mapped_column(Text, nullable=False)
    formatted_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    submit_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    processing_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="COMPLETED"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now
    )


class MeetingDocument(Base):
    __tablename__ = "meeting_documents"
    __table_args__ = (
        UniqueConstraint("meeting_period", "version", name="uq_meeting_period_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    meeting_period: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="GENERATING")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now
    )
