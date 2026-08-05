"""Logging and single-instance primitives kept separate from the sales bot."""

from __future__ import annotations

from contextlib import contextmanager
import logging
import os
from pathlib import Path
from typing import Any, Iterator

from .settings import MeetingBotSettings


INSTANCE_LOCK_FILENAME = "meeting_minutes_bot.lock"


class MeetingBotSingleInstanceError(RuntimeError):
    pass


def configure_logging(settings: MeetingBotSettings) -> None:
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers: tuple[logging.Handler, ...] = (
        logging.StreamHandler(),
        logging.FileHandler(
            settings.log_dir / "meeting_minutes_bot.log", encoding="utf-8"
        ),
    )
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(
        level=getattr(logging, settings.log_level), handlers=handlers, force=True
    )
    lark_logger = logging.getLogger("Lark")
    for handler in tuple(lark_logger.handlers):
        lark_logger.removeHandler(handler)
        handler.close()
    lark_logger.propagate = True
    lark_logger.setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _lock_file(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def single_instance_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    if lock_path.stat().st_size == 0:
        handle.write(b"\0")
        handle.flush()
    try:
        _lock_file(handle)
    except OSError as exc:
        handle.close()
        raise MeetingBotSingleInstanceError("周例会纪要机器人已经在运行。") from exc
    try:
        yield
    finally:
        try:
            _unlock_file(handle)
        finally:
            handle.close()
