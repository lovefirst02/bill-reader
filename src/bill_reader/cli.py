"""Taipower bills to a CSV, with the models on the CPU.

    python -m bill_reader bills_folder -o bills.csv
    TaipowerBillReaderCLI.exe bill1.pdf bill2.jpg -o bills.csv

Run without arguments, it opens a window to choose them in (gui.py).
The source file, the page and what needs checking are written too, unless
``--no-details``. Pages that are not a bill's charge page are left out. A figure
not read is 0, which only what needs checking tells from a printed 0, by naming
it; opened in Excel, the file reads as Traditional Chinese.
Beside the CSV, a .log says how each page was read and what was read on it.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import Callable, NamedTuple, TextIO

from . import __version__
from .parser import BillResult
from .reader import FileFinished, PageDone, PageReader, find_bill_files, has_bill_values, iter_pages
from .record import REVIEW_COLUMN, with_zeros

_KWH = ("尖峰度數", "半尖峰度數", "週六半尖峰度數", "離峰度數")
CSV_COLUMNS = (
    "Billing Period 計費期間",
    "Custom No. 電號",
    *(f"計費度數_{label}" for label in _KWH),
    *(f"轉供度數_{label}" for label in _KWH),
    "基本電費",
    "流動電費",
    "功率因數調整費",
    "預繳費用",
    "稅前應繳總金額",
    "應繳總金額",
)
DETAIL_COLUMNS = ("來源檔案", "頁碼", "需人工檢查")
NO_BILLS = "No bill files found (PDF, PNG, JPG, TIF and BMP are supported)"


class Summary(NamedTuple):
    written: int
    skipped: int
    flagged: int
    failures: list[str]

    def text(self) -> str:
        return f"Done: {self.written} bill(s), {self.flagged} to review, {self.skipped} other page(s) skipped, {len(self.failures)} file(s) failed"


def _print(text: str) -> None:
    print(text, flush=True)


def collect_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for name in paths:
        path = Path(name)
        if path.is_dir():
            files += find_bill_files(path)
        elif path.is_file():
            files.append(path)
        else:
            raise SystemExit(f"File or folder not found: {name}")
    return files


def default_output(paths: list[str]) -> Path:
    """bills.csv in the folder given, or beside the first file."""
    first = Path(paths[0])
    return (first if first.is_dir() else first.parent) / "bills.csv"


def write_csv(
    files: list[Path],
    out: Path,
    reader: PageReader | None = None,
    *,
    details: bool = True,
    say: Callable[[str], None] = _print,
    on_file: Callable[[int], None] | None = None,
) -> Summary:
    """``say`` hears each bill as it is read; ``on_file`` how many files are through."""
    reader = reader or PageReader()
    columns = CSV_COLUMNS + DETAIL_COLUMNS if details else CSV_COLUMNS
    written = skipped = flagged = finished = 0
    failures: list[str] = []
    with out.open("w", newline="", encoding="utf-8-sig") as handle, out.with_suffix(".log").open("w", encoding="utf-8-sig") as log:
        log.write(f"bill-reader {__version__}  {time.strftime('%Y-%m-%d %H:%M:%S')}  {len(files)} 個檔案\n")
        writer = csv.DictWriter(handle, columns, extrasaction="ignore")
        writer.writeheader()
        started = time.time()
        for event in iter_pages(files, reader):
            if isinstance(event, PageDone):
                _log_page(log, event, time.time() - started)
                started = time.time()
                if event.record is None or (event.bill is not None and not has_bill_values(event.bill)):
                    skipped += 1
                    continue
                filled = with_zeros(event.record.row)
                writer.writerow({column: _cell(filled.get(column)) for column in columns})
                handle.flush()  # stopped halfway, what was read is still there
                written += 1
                flagged += bool(event.record.row.get(REVIEW_COLUMN))
                page = f" page {event.page_number}" if event.page_number else ""
                how = f" ({_ROUTE_NAMES[_route(event.bill)]})" if event.bill is not None and event.bill.get("quality.source") == "pdf_text" else ""
                check = ", to review" if event.record.row.get(REVIEW_COLUMN) else ""
                say(f"{event.path.name}{page}: {event.record.row.get('Custom No. 電號') or 'customer number not read'}{how}{check}")
            elif isinstance(event, FileFinished):
                finished += 1
                if on_file:
                    on_file(finished)
                if event.error:
                    failures.append(f"{event.path.name}: {event.error}")
                    log.write(f"\n{event.path.name}\n  失敗：{event.error}\n")
                    log.flush()
                    say(f"{event.path.name}: failed ({event.error})")
    return Summary(written, skipped, flagged, failures)


_ROUTE_NAMES = {"PDF 文字": "PDF text", "PDF 文字＋模型補值": "PDF text + models"}


def _route(bill: BillResult) -> str:
    if bill.get("quality.source") != "pdf_text":
        return "模型"
    return "PDF 文字＋模型補值" if (bill.get("quality.pdf_text") or {}).get("filled") else "PDF 文字"


def _log_page(log: TextIO, event: PageDone, seconds: float) -> None:
    """One page: which way it was read, what stopped the text, and every line read."""
    bill = event.bill
    page = f" 第 {event.page_number} 頁" if event.page_number else ""
    log.write(f"\n{event.path.name}{page}  {seconds:.1f} 秒\n")
    if bill is None:
        log.write("  沒有讀取結果\n")
        return
    notes = bill.get("quality.pdf_text") or {}
    model = bill.get("quality.model_read") or {}
    log.write(f"  路線：{_route(bill)}\n")
    if notes:
        log.write(f"  PDF：{notes.get('page')} 點，{notes.get('words')} 個字，{notes.get('rules')} 條直線\n")
    if notes.get("refused"):
        log.write(f"  文字未採用：{'；'.join(notes['refused'])}\n")
    if notes.get("missing"):
        log.write(f"  文字缺少：{'；'.join(notes['missing'])}\n")
        log.write(f"  模型補上：{'、'.join(notes.get('filled') or []) or '無'}\n")
    if notes.get("fill_failed"):
        log.write(f"  模型補值失敗：{notes['fill_failed']}\n")
    if model:
        log.write(f"  模型：圖 {model.get('image')}，偵測 {model.get('found')} 行，留下 {model.get('kept')} 行，"
                  f"半尺寸重試 {'有用' if model.get('half_size') else '沒用'}，最後 {model.get('kept_in_the_end')} 行、{model.get('labels')} 個標籤\n")
    if event.record is None:
        log.write("  結果：不是電費頁，略過\n")
    else:
        log.write(f"  需人工檢查：{event.record.row.get('需人工檢查') or '無'}\n")
    read = [line.text for line in getattr(bill, "ocr_lines", None) or []]
    own = "PDF 文字" if bill.get("quality.source") == "pdf_text" else "模型"
    for title, texts in (("PDF 文字內容", notes.get("lines")), (f"{own}讀到", read), ("模型讀到", notes.get("model_lines"))):
        if texts:
            log.write(f"  {title}：\n" + "".join(f"    {text}\n" for text in texts))
    log.flush()


def _cell(value: object) -> object:
    return "" if value is None else value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read Taipower bills (PDF, images) to a CSV; without arguments, opens a window")
    parser.add_argument("inputs", nargs="*", help="bill files or folders")
    parser.add_argument("-o", "--output", type=Path, help="the CSV to write; by default bills.csv in the folder")
    parser.add_argument("--details", action=argparse.BooleanOptionalAction, default=True, help="write the source file, page and what to review (on by default)")
    args = parser.parse_args(argv)

    if not args.inputs:
        from .gui import run

        return run()
    files = [path for path in collect_files(args.inputs) if path != args.output]
    if not files:
        print(NO_BILLS)
        return 1
    out = args.output or default_output(args.inputs)
    if sys.stdout is not None:  # a file's name the console's code page lacks must not stop the batch
        sys.stdout.reconfigure(errors="replace")

    started = time.time()
    summary = write_csv(files, out, details=args.details)
    print(f"\n{summary.text()} in {time.time() - started:.1f} s")
    print(f"Output: {out.resolve()}")
    print(f"Log: {out.with_suffix('.log').resolve()}")
    return 1 if summary.failures and not summary.written else 0


if __name__ == "__main__":
    sys.exit(main())
