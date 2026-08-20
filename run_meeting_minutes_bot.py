"""Top-level entry point so PyInstaller can freeze the meeting-minutes bot.

Running ``python -m meeting_minutes_bot`` from source keeps working unchanged.
"""

from __future__ import annotations

from meeting_minutes_bot.__main__ import cli


if __name__ == "__main__":
    cli()
