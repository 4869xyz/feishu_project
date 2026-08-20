from __future__ import annotations

from pathlib import Path

from meeting_minutes_bot.document import MinutesDocumentRenderer
from meeting_minutes_bot.people import PeopleDirectory, Person
from meeting_minutes_bot.repository import MeetingRepository
from meeting_minutes_bot.service import MeetingMinutesService


TEST_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "meeting_minutes"
    / "周例会纪要测试模板.docx"
)


def people_directory() -> PeopleDirectory:
    return PeopleDirectory(
        people=(
            Person(
                open_id="ou_wu",
                name="吴傲翔",
                department="商务部-销售组",
                template_key="wu_aoxiang",
                section_order=2,
                sort_order=1,
            ),
            Person(
                open_id="ou_yang",
                name="杨意林",
                department="商务部-销售组",
                template_key="yang_yilin",
                section_order=2,
                sort_order=2,
            ),
            Person(
                open_id="ou_disabled",
                name="停用员工",
                department="测试部",
                template_key="general_manager",
                section_order=1,
                sort_order=1,
                enabled=False,
            ),
        ),
        admins=frozenset({"ou_admin"}),
    )


async def build_service(root: Path) -> tuple[MeetingMinutesService, MeetingRepository]:
    database = root / "meeting.db"
    repository = MeetingRepository(f"sqlite+aiosqlite:///{database.as_posix()}")
    await repository.initialize()
    people = people_directory()
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    renderer = MinutesDocumentRenderer(
        template_path=TEST_TEMPLATE,
        output_dir=root / "output",
        people=people,
        data_dir=data_dir,
    )
    return (
        MeetingMinutesService(
            repository=repository,
            people=people,
            renderer=renderer,
            timezone="Asia/Shanghai",
            max_text_length=100,
            data_dir=data_dir,
        ),
        repository,
    )
