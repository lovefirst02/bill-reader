"""The master workbook people correct by hand: rows are only ever added, never changed.

Sheet ``Bills`` holds one row per bill, found by its ``ID`` column; its columns are
found by their names, so people may reorder them or add their own. Sheets
``Changes`` and ``Files`` belong to the program and are rewritten on each write.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import range_boundaries
from openpyxl.worksheet.worksheet import Worksheet

from .cli import CSV_COLUMNS
from .record import REVIEW_COLUMN

BILLS, CHANGES, FILES = "Bills", "Changes", "Files"
ID, SITE, SOURCE, PAGE, READ_AT = "ID", "Site", "來源檔案", "頁碼", "讀取時間"
MASTER_COLUMNS = (ID, SITE, *CSV_COLUMNS, SOURCE, PAGE, REVIEW_COLUMN, READ_AT)
CHANGE_COLUMNS = ("類型", ID, SITE, SOURCE, PAGE, "欄位", "主表的值", "程式讀到的值", "說明")
FILE_COLUMNS = (SITE, SOURCE, "狀態", "帳單頁數", "需人工檢查", "讀取時間", "嘗試次數", "錯誤")
PERIOD, METER = "Billing Period 計費期間", "Custom No. 電號"
_FIGURES = tuple(column for column in CSV_COLUMNS if column not in (PERIOD, METER))
_HEADER_SEARCH = 10
BACKUPS_KEPT = 10


class MasterLocked(Exception):
    """Open in Excel, or changed while it was being written: try again later."""


class MasterRefused(Exception):
    """Not a master this program can add to without harm; a person has to fix it."""


@dataclass
class Placement:
    """What happened to one waiting row: appended, already there, or the same bill as a row there."""

    id: str
    duplicate_of: str | None = None


@dataclass
class WriteResult:
    appended: int = 0
    placements: list[Placement] = field(default_factory=list)


def bill_key(row: Mapping[str, Any]) -> tuple[str, str] | None:
    """電號 and 計費期間: the same pair in two rows is the same bill."""
    meter, period = (str(row.get(column) or "").strip() for column in (METER, PERIOD))
    return (meter, period) if meter and period else None


def same_value(a: Any, b: Any) -> bool:
    """634220 and 634220.0 and "634,220" are one value; blank and None too."""
    numbers = [_number(a), _number(b)]
    if None not in numbers:
        return abs(numbers[0] - numbers[1]) < 1e-6
    return _text(a) == _text(b)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def is_locked(path: Path) -> bool:
    """Excel leaves ``~$name`` beside a workbook it has open, and keeps the file itself locked."""
    if (path.parent / f"~${path.name}").exists():
        return True
    try:
        with path.open("r+b"):
            return False
    except FileNotFoundError:
        return False
    except PermissionError:
        return True


def _find_header(sheet: Worksheet) -> tuple[int, dict[str, int]]:
    for row in range(1, min(sheet.max_row, _HEADER_SEARCH) + 1):
        names = {_text(cell.value): cell.column for cell in sheet[row] if _text(cell.value)}
        if ID in names:
            return row, names
    raise MasterRefused(f"no {ID} column in the first {_HEADER_SEARCH} rows of sheet {BILLS}")


def _last_row(sheet: Worksheet, header: int) -> int:
    last = header
    for row in sheet.iter_rows(min_row=header + 1):
        if any(cell.value not in (None, "") for cell in row):
            last = row[0].row
    return last


def _new_workbook() -> Workbook:
    book = Workbook()
    book.active.title = BILLS
    _start_bills(book.active)
    return book


def _is_empty(sheet: Worksheet) -> bool:
    return all(cell.value in (None, "") for row in sheet.iter_rows() for cell in row)


def _start_bills(sheet: Worksheet) -> None:
    """The column names in the first row of an empty sheet."""
    for number, name in enumerate(MASTER_COLUMNS, start=1):
        sheet.cell(1, number, name)
        sheet.column_dimensions[get_column_letter(number)].width = max(10, min(28, len(name) * 1.6))
    sheet.freeze_panes = "C2"


def _rewrite_sheet(book: Workbook, title: str, columns: tuple[str, ...], rows: list[list[Any]]) -> None:
    if title in book.sheetnames:
        del book[title]
    sheet = book.create_sheet(title)
    sheet.append(columns)
    for row in rows:
        sheet.append(row)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{max(1, len(rows) + 1)}"


def _grow_ranges(sheet: Worksheet, header: int, last: int) -> None:
    """An Excel table or filter over the bills takes in the rows added below it."""
    for table in sheet.tables.values():
        low_col, top, high_col, _ = range_boundaries(table.ref)
        if top == header:
            table.ref = f"{get_column_letter(low_col)}{top}:{get_column_letter(high_col)}{last}"
            if table.autoFilter is not None:
                table.autoFilter.ref = table.ref
    if sheet.auto_filter.ref:
        low_col, top, high_col, _ = range_boundaries(sheet.auto_filter.ref)
        if top == header:
            sheet.auto_filter.ref = f"{get_column_letter(low_col)}{top}:{get_column_letter(high_col)}{last}"


def _backup(path: Path, backups: Path) -> None:
    backups.mkdir(parents=True, exist_ok=True)
    tag = f"{path.stem}-{hashlib.sha1(str(path.resolve()).lower().encode()).hexdigest()[:6]}"
    shutil.copy2(path, backups / f"{tag}-{datetime.now():%Y%m%d-%H%M%S-%f}.xlsx")
    for old in sorted(backups.glob(f"{tag}-*.xlsx"))[:-BACKUPS_KEPT]:
        old.unlink()


def write_master(
    path: Path,
    waiting: list[dict[str, Any]],
    originals: Mapping[str, Mapping[str, Any]],
    duplicates: list[tuple[dict[str, Any], str]],
    versions: list[tuple[dict[str, Any], str]],
    files: list[list[Any]],
    backups: Path,
) -> WriteResult:
    """Adds ``waiting`` rows below the bills, and rewrites Changes and Files.

    ``originals`` are the rows as first read, by ID, to tell what people changed;
    ``duplicates`` are rows read earlier that were the same bill as a master row;
    ``versions`` the newest reading of a file whose content changed, with the ID of
    its first reading: listed only where they differ, as a highlighter's marks do not.
    Rows already there are never touched. Raises MasterLocked or MasterRefused.
    """
    if is_locked(path):
        raise MasterLocked(f"{path.name} is open")
    before = path.stat() if path.exists() else None
    book = load_workbook(path) if before else _new_workbook()
    if BILLS not in book.sheetnames:  # a workbook made by hand to hold the bills: other sheets stay as they are
        _start_bills(book.create_sheet(BILLS, 0))
    sheet = book[BILLS]
    if _is_empty(sheet):
        _start_bills(sheet)
    header, names = _find_header(sheet)
    missing = [column for column in MASTER_COLUMNS if column not in names]
    if missing:
        raise MasterRefused(f"columns missing from sheet {BILLS}: {', '.join(missing)}")

    present: dict[str, dict[str, Any]] = {}
    keys: dict[tuple[str, str], str] = {}
    last = _last_row(sheet, header)
    for number in range(header + 1, last + 1):
        row = {column: sheet.cell(number, names[column]).value for column in MASTER_COLUMNS}
        row_id = _text(row[ID]) or f"(row {number})"
        present[row_id] = row
        if key := bill_key(row):
            keys.setdefault(key, row_id)

    duplicates = list(duplicates)
    result = WriteResult()
    for row in waiting:
        if row[ID] in present:
            result.placements.append(Placement(row[ID]))
        elif (key := bill_key(row)) and key in keys:
            result.placements.append(Placement(row[ID], keys[key]))
            duplicates.append((row, keys[key]))
        else:
            last += 1
            for column in MASTER_COLUMNS:
                cell = sheet.cell(last, names[column], row.get(column))
                if column in _FIGURES:
                    cell.number_format = "#,##0.##"
            present[row[ID]] = row
            if key:
                keys[key] = row[ID]
            result.placements.append(Placement(row[ID]))
            result.appended += 1
    _grow_ranges(sheet, header, last)

    changes: list[list[Any]] = []
    for row_id, row in present.items():
        original = originals.get(row_id)
        for column in CSV_COLUMNS if original else ():
            if not same_value(row.get(column), original.get(column)):
                changes.append(["人工修改", row_id, row.get(SITE), row.get(SOURCE), row.get(PAGE), column,
                                row.get(column), original.get(column), ""])
    for read, master_id in duplicates:
        there = present.get(master_id, {})
        differ = [column for column in CSV_COLUMNS if not same_value(there.get(column), read.get(column))]
        note = f"和主表 {master_id} 是同一張帳單，未新增"
        for column in differ:
            changes.append(["重複帳單", read[ID], read.get(SITE), read.get(SOURCE), read.get(PAGE), column,
                            there.get(column), read.get(column), note])
        if not differ:
            changes.append(["重複帳單", read[ID], read.get(SITE), read.get(SOURCE), read.get(PAGE), "", "", "", note + "，內容相同"])
    for read, root in versions:
        first, there = originals.get(root, {}), present.get(root)
        for column in CSV_COLUMNS:
            if not same_value(first.get(column), read.get(column)):
                note = f"來源檔內容改了，第一次讀到 {first.get(column)}；主表{'這列未更動' if there else '已刪除這列'}"
                changes.append(["來源檔已更新", root, read.get(SITE), read.get(SOURCE), read.get(PAGE), column,
                                (there or {}).get(column), read.get(column), note])
    _rewrite_sheet(book, CHANGES, CHANGE_COLUMNS, changes)
    _rewrite_sheet(book, FILES, FILE_COLUMNS, files)

    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.stem}.writing.tmp")
    book.save(temp)
    try:
        if before is not None:
            now = path.stat()
            if (now.st_mtime_ns, now.st_size) != (before.st_mtime_ns, before.st_size) or is_locked(path):
                raise MasterLocked(f"{path.name} changed while it was being written")
            _backup(path, backups)
        os.replace(temp, path)
    except PermissionError as error:
        raise MasterLocked(f"{path.name} is open") from error
    finally:
        temp.unlink(missing_ok=True)
    return result
