"""End-to-end tests for SOP-driven XLSX aggregation."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from services.sales_workbook_aggregator import (
    DuplicateSourceError,
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


def _write_repayment_headers(sheet) -> None:
    headers = [
        "姓名",
        "收款主体",
        "付款主体",
        "产品",
        "合同日期",
        "合同总价",
        "居间费",
        "已回款-提成口（26年以前）",
        "已回款-提成口径（26年）",
        "已回款-提成口径总额",
        "待回款额",
        " 已开发票金额 ",
        " 已开票未回款 ",
        "回款比例",
        "税/运费",
        "预计剩余回款时间及金额",
        "备注",
        "已回款-业绩口径(26年)",
    ]
    for column, value in enumerate(headers, 1):
        sheet.cell(2, column).value = value
    for index in range(12):
        sheet.cell(3, 18 + index).value = f"{index + 1}月"


def _make_source(path: Path, signing_rows: list[list], repayment_rows: list[list]) -> None:
    workbook = Workbook()
    signing = workbook.active
    signing.title = "Sheet1"
    _write_signing_headers(signing)
    for row, values in enumerate(signing_rows, 4):
        for column, value in enumerate(values, 1):
            signing.cell(row, column).value = value

    repayment = workbook.create_sheet("Sheet2")
    _write_repayment_headers(repayment)
    for row, values in enumerate(repayment_rows, 4):
        for column, value in enumerate(values, 1):
            repayment.cell(row, column).value = value
    workbook.save(path)


def _style_row(sheet, row: int, max_column: int, color: str) -> None:
    for column in range(1, max_column + 1):
        cell = sheet.cell(row, column)
        cell.fill = PatternFill("solid", fgColor=color)
        cell.font = Font(name="Microsoft YaHei", bold=row != 4)
    sheet.row_dimensions[row].height = 20 + row % 3


def _make_template(path: Path) -> None:
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

    repayment = workbook.create_sheet(REPAYMENT_TARGET)
    _write_repayment_headers(repayment)
    repayment["A4"] = "样例人员"
    repayment["D5"] = "个人小计："
    repayment["D7"] = "部门总计："
    for row in range(4, 8):
        _style_row(repayment, row, 29, f"EE{row:02X}{row:02X}")

    detail = workbook.create_sheet("明细")
    detail["A1"] = "必须保留"
    workbook.save(path)


def _repayment_row(person: str, contract: int, prior: int, january: int) -> list:
    row = [None] * 29
    row[0] = person
    row[1] = "收款主体"
    row[5] = contract
    row[6] = "/"
    row[7] = prior
    row[11] = 1
    row[17] = january
    return row


def test_aggregate_rebuilds_formulas_order_and_control_totals(project_tmp_dir: Path) -> None:
    """Different source IDs count twice and all summary layers reconcile."""

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
        [_repayment_row("张三", 100, 10, 20)],
    )
    _make_source(
        source2,
        [
            ["甲组", 1, "A", "客户1", "项目", None, None, None, 10],
            ["乙组", 1, "B", "客户3", "项目", None, None, None, None, None, 5],
        ],
        [
            _repayment_row("张三", 100, 10, 20),
            _repayment_row("李四", 0, 0, 0),
        ],
    )

    result = aggregate_sales_workbooks(
        [SourceWorkbook("one", source1), SourceWorkbook("two", source2)],
        template,
        output,
    )

    assert result.signing_detail_count == 4
    assert result.repayment_detail_count == 3
    assert result.signing_total == Decimal(45)
    assert result.repayment_current_year_total == Decimal(40)
    assert result.repayment_contract_total == Decimal(200)
    assert result.repayment_cumulative_total == Decimal(60)

    workbook = load_workbook(output, data_only=False)
    signing = workbook[SIGNING_TARGET]
    repayment = workbook[REPAYMENT_TARGET]
    assert signing.max_row == 23
    assert signing["A4"].value == "甲组"
    assert signing["A14"].value == "乙组"
    assert signing["I7"].value == "=SUM(I4:I6)"
    assert signing["I10"].value == "=SUM(I7)"
    assert signing["I21"].value == "=SUM(I10,I18)"
    assert "A4:A12" in {str(item) for item in signing.merged_cells.ranges}
    assert "A14:A20" in {str(item) for item in signing.merged_cells.ranges}
    assert all(not signing.row_dimensions[row].hidden for row in range(4, 24))

    assert repayment.max_row == 10
    assert repayment["I4"].value == "=SUM(R4:AC4)"
    assert repayment["J4"].value == "=SUM(H4:I4)"
    assert repayment["K4"].value == "=F4-J4"
    assert repayment["N4"].value == "=IFERROR(J4/F4,0)"
    assert repayment["F10"].value == "=SUM(F6,F9)"
    assert workbook["展示用-签约排名汇总（0702统计）"]["A1"].value == "必须保留"
    assert workbook["明细"]["A1"].value == "必须保留"
    workbook.close()


def test_duplicate_source_id_is_rejected(project_tmp_dir: Path) -> None:
    """Task-level file idempotency is independent from workbook contents."""

    template = project_tmp_dir / "template.xlsx"
    source = project_tmp_dir / "source.xlsx"
    _make_template(template)
    _make_source(
        source,
        [["甲组", 1, "A", None, None, None, None, None, 1]],
        [_repayment_row("张三", 1, 0, 1)],
    )
    item = SourceWorkbook("same", source)
    with pytest.raises(DuplicateSourceError):
        aggregate_sales_workbooks([item, item], template, project_tmp_dir / "out.xlsx")


def test_invalid_amount_reports_sheet_row_and_column(project_tmp_dir: Path) -> None:
    """A malformed business amount is rejected before a partial output exists."""

    source = project_tmp_dir / "source.xlsx"
    _make_source(
        source,
        [["甲组", 1, "A", None, None, None, None, None, "不是金额"]],
        [_repayment_row("张三", 1, 0, 1)],
    )

    with pytest.raises(SourceValidationError, match="Sheet1.*第 4 行.*I 列"):
        validate_source_workbook(SourceWorkbook("source", source))
