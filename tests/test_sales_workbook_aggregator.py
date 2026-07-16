"""End-to-end tests for signing-only XLSX aggregation."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from services.sales_workbook_aggregator import (
    DuplicateSourceError,
    SalesAggregationError,
    SourceValidationError,
    SourceWorkbook,
    aggregate_sales_workbooks,
    validate_source_workbook,
)


SIGNING_TARGET = "展示用-2026年签约数据汇总（0702统计）"
REPAYMENT_TARGET = "展示用-2026年回款数据汇总(0702统计)"


def _write_signing_headers(sheet) -> None:
    headers = [
        "组别",
        "序号",
        "人员",
        "客户简称",
        "销售产品名称/项目名称",
        "落地地点",
        "客户来源\n（展会需注明）",
        "备注",
        "签约后且收到第一笔款后才算已签约",
    ]
    for column, value in enumerate(headers, 1):
        sheet.cell(2, column).value = value
    for index in range(12):
        sheet.cell(3, 9 + index).value = f"{index + 1}月"


def _write_signing_rows(sheet, rows: list[list]) -> None:
    _write_signing_headers(sheet)
    for row, values in enumerate(rows, 4):
        for column, value in enumerate(values, 1):
            sheet.cell(row, column).value = value


def _make_source(
    path: Path,
    signing_rows: list[list],
    *,
    signing_sheet_name: str = "签约数据",
    other_sheet_names: tuple[str, ...] = (),
) -> None:
    workbook = Workbook()
    signing = workbook.active
    signing.title = signing_sheet_name
    _write_signing_rows(signing, signing_rows)
    for name in other_sheet_names:
        other = workbook.create_sheet(name)
        other["A1"] = "该工作表应被忽略"
        other["F4"] = "回款金额故意写成非法文本"
    workbook.save(path)


def _source_row(*, group=None, sequence=None, person=None, note=None, months=()) -> list:
    """Return one A:T source row with selected test values."""

    values = [None] * 20
    values[0] = group
    values[1] = sequence
    values[2] = person
    values[7] = note
    for index, value in enumerate(months):
        values[8 + index] = value
    return values


def _make_candidate_source(path: Path, sheet_names: list[str]) -> None:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for index, name in enumerate(sheet_names, 1):
        sheet = workbook.create_sheet(name)
        _write_signing_rows(
            sheet,
            [[f"第{index}组", 1, f"人员{index}", None, None, None, None, None, index]],
        )
    workbook.save(path)


def _style_row(sheet, row: int, max_column: int, color: str) -> None:
    for column in range(1, max_column + 1):
        cell = sheet.cell(row, column)
        cell.fill = PatternFill("solid", fgColor=color)
        cell.font = Font(name="Microsoft YaHei", bold=row != 4)
    sheet.row_dimensions[row].height = 20 + row % 3


def _make_template(path: Path, *, include_repayment: bool = True) -> None:
    workbook = Workbook()
    signing = workbook.active
    signing.title = SIGNING_TARGET
    _write_signing_headers(signing)
    signing["A4"] = "样例组"
    signing["B4"] = 1
    signing["C4"] = "样例人员"
    labels = {
        5: "个人月度合计：",
        6: "个人季度合计：",
        7: "个人年度合计：",
        9: "小组月度合计：",
        10: "小组季度合计：",
        11: "小组年度合计：",
    }
    for row, label in labels.items():
        signing.cell(row, 2).value = label
    signing["A13"] = "部门月度合计："
    signing["A14"] = "部门季度合计："
    signing["A15"] = "部门年度合计："
    for row in range(4, 16):
        _style_row(signing, row, 20, f"{row:02X}{row:02X}EE")
    signing.row_dimensions[12].hidden = True

    ranking = workbook.create_sheet("展示用-签约排名汇总（0702统计）")
    ranking["A1"] = "必须保留"
    ranking["B2"] = "=1+1"

    if include_repayment:
        repayment = workbook.create_sheet(REPAYMENT_TARGET)
        repayment["A1"] = "回款表必须原样保留"
        repayment["F4"] = "旧回款内容"
        repayment["G5"] = "=1+1"
        repayment.row_dimensions[6].hidden = True

    detail = workbook.create_sheet("明细")
    detail["A1"] = "必须保留"
    workbook.save(path)


@pytest.mark.parametrize("sheet_name", ["Sheet1", "签约数据", "任意名称"])
def test_single_sheet_uses_its_only_worksheet(
    project_tmp_dir: Path, sheet_name: str
) -> None:
    source = project_tmp_dir / f"single-{sheet_name}.xlsx"
    _make_source(
        source,
        [["甲组", 1, "A", None, None, None, None, None, 10]],
        signing_sheet_name=sheet_name,
    )

    result = validate_source_workbook(SourceWorkbook("single", source))

    assert result.signing_sheet_name == sheet_name
    assert result.signing_detail_count == 1


@pytest.mark.parametrize(
    ("sheet_names", "expected"),
    [
        (["销售签约数据", "签约数据", "签约情况"], "签约情况"),
        (["销售签约数据", "签约数据"], "签约数据"),
        (["销售签约数据", "2026签约情况"], "2026签约情况"),
        (["销售签约数据", "历史签约数据"], "销售签约数据"),
    ],
)
def test_multi_sheet_uses_confirmed_name_priority(
    project_tmp_dir: Path, sheet_names: list[str], expected: str
) -> None:
    source = project_tmp_dir / "candidates.xlsx"
    _make_candidate_source(source, sheet_names)

    result = validate_source_workbook(SourceWorkbook("candidate", source))

    assert result.signing_sheet_name == expected
    assert result.signing_detail_count == 1


def test_multi_sheet_without_candidate_lists_available_names(
    project_tmp_dir: Path,
) -> None:
    source = project_tmp_dir / "no-signing-source.xlsx"
    _make_candidate_source(source, ["签约汇总", "签约排名"])

    with pytest.raises(SourceValidationError) as exc_info:
        validate_source_workbook(SourceWorkbook("missing", source))

    message = str(exc_info.value)
    assert "未找到" in message
    assert "签约汇总" in message
    assert "签约排名" in message


def test_aggregate_rebuilds_signing_and_preserves_repayment(
    project_tmp_dir: Path,
) -> None:
    template = project_tmp_dir / "template.xlsx"
    source1 = project_tmp_dir / "source1.xlsx"
    source2 = project_tmp_dir / "source2.xlsx"
    output = project_tmp_dir / "output.xlsx"
    _make_template(template)
    _make_source(
        source1,
        [
            ["甲组", 1, "A", "客户1", "项目", None, None, None, 10],
            [None, 2, "A", "客户2", "项目", None, None, None, None, 20],
        ],
        other_sheet_names=("回款数据",),
    )
    _make_source(
        source2,
        [
            ["甲组", 1, "A", "客户1", "项目", None, None, None, 10],
            ["乙组", 1, "B", "客户3", "项目", None, None, None, None, None, 5],
        ],
        signing_sheet_name="2026签约情况",
        other_sheet_names=("损坏的回款情况",),
    )

    result = aggregate_sales_workbooks(
        [SourceWorkbook("one", source1), SourceWorkbook("two", source2)],
        template,
        output,
    )

    assert result.signing_detail_count == 4
    assert result.signing_total == Decimal(45)

    workbook = load_workbook(output, data_only=False)
    signing = workbook[SIGNING_TARGET]
    assert signing.max_row == 23
    assert signing["A4"].value == "甲组"
    assert signing["A14"].value == "乙组"
    assert signing["I7"].value == "=SUM(I4:I6)"
    assert signing["I10"].value == "=SUM(I7)"
    assert signing["I21"].value == "=SUM(I10,I18)"
    assert "A4:A12" in {str(item) for item in signing.merged_cells.ranges}
    assert "A14:A20" in {str(item) for item in signing.merged_cells.ranges}
    assert all(not signing.row_dimensions[row].hidden for row in range(4, 24))

    repayment = workbook[REPAYMENT_TARGET]
    assert repayment["A1"].value == "回款表必须原样保留"
    assert repayment["F4"].value == "旧回款内容"
    assert repayment["G5"].value == "=1+1"
    assert repayment.row_dimensions[6].hidden is True
    assert workbook["展示用-签约排名汇总（0702统计）"]["A1"].value == "必须保留"
    assert workbook["明细"]["A1"].value == "必须保留"
    workbook.close()


def test_template_without_repayment_sheet_still_succeeds(project_tmp_dir: Path) -> None:
    template = project_tmp_dir / "template.xlsx"
    source = project_tmp_dir / "source.xlsx"
    output = project_tmp_dir / "output.xlsx"
    _make_template(template, include_repayment=False)
    _make_source(
        source,
        [["甲组", 1, "A", None, None, None, None, None, 1]],
        signing_sheet_name="任意单表名称",
    )

    aggregate_sales_workbooks([SourceWorkbook("one", source)], template, output)

    workbook = load_workbook(output, read_only=True)
    assert SIGNING_TARGET in workbook.sheetnames
    assert REPAYMENT_TARGET not in workbook.sheetnames
    workbook.close()


def test_duplicate_source_id_is_rejected(project_tmp_dir: Path) -> None:
    template = project_tmp_dir / "template.xlsx"
    source = project_tmp_dir / "source.xlsx"
    _make_template(template)
    _make_source(
        source,
        [["甲组", 1, "A", None, None, None, None, None, 1]],
        signing_sheet_name="Sheet1",
    )
    item = SourceWorkbook("same", source)
    with pytest.raises(DuplicateSourceError):
        aggregate_sales_workbooks([item, item], template, project_tmp_dir / "out.xlsx")


def test_invalid_amount_reports_selected_sheet_row_and_column(
    project_tmp_dir: Path,
) -> None:
    source = project_tmp_dir / "source.xlsx"
    _make_source(
        source,
        [["甲组", 1, "A", None, None, None, None, None, "不是金额"]],
        signing_sheet_name="任意名称",
    )

    with pytest.raises(SourceValidationError, match="任意名称.*第 4 行.*I 列"):
        validate_source_workbook(SourceWorkbook("source", source))


def test_blank_person_summary_and_note_rows_are_ignored(
    project_tmp_dir: Path,
) -> None:
    """Summary values and annotations never enter signing validation or totals."""

    template = project_tmp_dir / "template.xlsx"
    source = project_tmp_dir / "source-with-summary.xlsx"
    output = project_tmp_dir / "output.xlsx"
    _make_template(template)
    rows = [
        _source_row(group="甲组", sequence=1, person="A", months=(10,)),
        _source_row(sequence="个人月度合计：", months=(10, " - ")),
        _source_row(sequence="个人季度合计：", months=(10,)),
        _source_row(sequence="个人年度合计：", months=(10,)),
        _source_row(sequence="有修改的请用别的颜色填充", note="说明", months=(" - ",)),
        _source_row(sequence=None, person="A", months=(None, 5)),
        _source_row(sequence=99, person=None, note="漏填人员", months=(999,)),
    ]
    _make_source(source, rows, signing_sheet_name="签约情况")
    workbook = load_workbook(source)
    sheet = workbook["签约情况"]
    for row in range(5, 8):
        sheet.merge_cells(start_row=row, start_column=2, end_row=row, end_column=8)
    workbook.save(source)
    workbook.close()

    validation = validate_source_workbook(SourceWorkbook("source", source))
    result = aggregate_sales_workbooks(
        [SourceWorkbook("source", source)], template, output
    )

    assert validation.signing_detail_count == 2
    assert result.signing_detail_count == 2
    assert result.signing_total == Decimal(15)


def test_all_summary_labels_do_not_replace_inherited_group(
    project_tmp_dir: Path,
) -> None:
    """All nine summary labels are skipped before they can alter group inheritance."""

    template = project_tmp_dir / "template.xlsx"
    source = project_tmp_dir / "all-summary-labels.xlsx"
    output = project_tmp_dir / "output.xlsx"
    _make_template(template)
    labels = (
        "个人月度合计",
        "个人季度合计：",
        "个人年度合计",
        "小组月度合计：",
        "小组季度合计",
        "小组年度合计：",
        "部门月度合计",
        "部门季度合计：",
        "部门年度合计",
    )
    rows = [_source_row(group="甲组", sequence=1, person="A", months=(1,))]
    for label in labels[:6]:
        rows.append(_source_row(sequence=label, months=(" - ",)))
    for label in labels[6:]:
        rows.append(_source_row(group=label, months=(" - ",)))
    rows.append(_source_row(sequence=2, person="B", months=(2,)))
    _make_source(source, rows, signing_sheet_name="签约数据")

    result = aggregate_sales_workbooks(
        [SourceWorkbook("source", source)], template, output
    )

    assert result.signing_detail_count == 2
    assert result.signing_total == Decimal(3)
    workbook = load_workbook(output, data_only=False)
    signing = workbook[SIGNING_TARGET]
    assert signing["A4"].value == "甲组"
    vertical_group_merges = [
        merged
        for merged in signing.merged_cells.ranges
        if merged.min_col == merged.max_col == 1 and merged.min_row >= 4
    ]
    assert len(vertical_group_merges) == 1
    workbook.close()


def test_group_only_row_sets_group_for_following_detail(project_tmp_dir: Path) -> None:
    """An A-only group header remains meaningful even though its person is blank."""

    source = project_tmp_dir / "group-header.xlsx"
    _make_source(
        source,
        [
            _source_row(group="甲组"),
            _source_row(sequence=1, person="A", months=(1,)),
        ],
        signing_sheet_name="Sheet1",
    )

    validation = validate_source_workbook(SourceWorkbook("source", source))

    assert validation.signing_detail_count == 1


@pytest.mark.parametrize("person", [123, True])
def test_non_text_non_blank_person_is_rejected(
    project_tmp_dir: Path,
    person,
) -> None:
    """Only blank personnel cells are ignored; malformed nonblank values still fail."""

    source = project_tmp_dir / f"invalid-person-{person}.xlsx"
    _make_source(
        source,
        [_source_row(group="甲组", sequence=1, person=person, months=(1,))],
        signing_sheet_name="Sheet1",
    )

    with pytest.raises(SourceValidationError, match="第 4 行.*C 列.*人员必须是文本"):
        validate_source_workbook(SourceWorkbook("source", source))


def test_empty_signing_data_is_rejected_during_aggregation(
    project_tmp_dir: Path,
) -> None:
    template = project_tmp_dir / "template.xlsx"
    source = project_tmp_dir / "empty.xlsx"
    _make_template(template)
    _make_source(
        source,
        [
            _source_row(sequence="个人月度合计：", months=(" - ",)),
            _source_row(sequence="仅供说明", note="没有业务明细"),
        ],
        signing_sheet_name="Sheet1",
    )

    with pytest.raises(SalesAggregationError, match="没有有效签约明细"):
        aggregate_sales_workbooks(
            [SourceWorkbook("empty", source)],
            template,
            project_tmp_dir / "out.xlsx",
        )
