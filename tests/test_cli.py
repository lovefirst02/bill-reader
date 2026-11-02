import csv
import queue
import tempfile
import unittest
from pathlib import Path

from bill_reader.reader import FileFinished, PageDone
from bill_reader.cli import CSV_COLUMNS, DETAIL_COLUMNS, write_csv
from bill_reader import cli as bill_csv
from bill_reader.parser import BillResult, OcrLine
from unittest import mock


class Record:
    row = {"Billing Period 計費期間": "113/01/01~113/01/31", "Custom No. 電號": "17-94-2403-50-4", "基本電費": 634220,
           "預繳費用": None, "來源檔案": "a.png", "頁碼": None, "需人工檢查": "缺少流動電費"}


def read_back(details):
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "bills.csv"
        events = [PageDone(Path("a.png"), None, Record()), PageDone(Path("b.png"), None, None)]
        with mock.patch.object(bill_csv, "iter_pages", lambda files, extractor: iter(events)):
            result = write_csv([Path("a.png")], out, object(), details=details)
        with out.open(encoding="utf-8-sig", newline="") as handle:
            return result, list(csv.DictReader(handle))


class BillCsvLogTests(unittest.TestCase):
    def log_of(self, *events):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "bills.csv"
            with mock.patch.object(bill_csv, "iter_pages", lambda files, extractor: iter(events)):
                write_csv([Path("a.pdf")], out, object())
            return (Path(folder) / "bills.log").read_text(encoding="utf-8-sig")

    def test_says_why_a_pages_text_was_not_used_and_what_the_model_read(self):
        bill = BillResult({"charge_info": {"total_amount_twd": 1}, "quality": {
            "pdf_text": {"page": "595x842", "words": 143, "rules": 20, "refused": ["缺少計費期間"], "lines": ["電號:"]},
            "model_read": {"image": "1654x2339", "found": 60, "kept": 41, "half_size": False, "kept_in_the_end": 41, "labels": 20},
        }})
        bill.ocr_lines = [OcrLine("基本電費", 0, 0, 10, 10)]
        log = self.log_of(PageDone(Path("a.pdf"), 1, Record(), bill=bill))
        for text in ("a.pdf 第 1 頁", "路線：模型", "143 個字", "文字未採用：缺少計費期間", "圖 1654x2339", "需人工檢查：缺少流動電費", "電號:", "基本電費"):
            self.assertIn(text, log)

    def test_a_page_left_out_and_a_file_that_failed_are_in_it_too(self):
        log = self.log_of(PageDone(Path("b.pdf"), 2, None, bill=BillResult({"quality": {}})), FileFinished(Path("c.pdf"), "ValueError: broken"))
        self.assertIn("不是電費頁，略過", log)
        self.assertIn("失敗：ValueError: broken", log)

    def test_a_text_page_says_so(self):
        bill = BillResult({"charge_info": {"total_amount_twd": 1}, "quality": {"source": "pdf_text", "pdf_text": {"page": "595x842", "words": 143, "rules": 20}}})
        self.assertIn("路線：PDF 文字", self.log_of(PageDone(Path("a.pdf"), 1, Record(), bill=bill)))


class BillCsvTests(unittest.TestCase):
    def test_the_columns_asked_for_and_no_more(self):
        summary, rows = read_back(False)
        self.assertEqual(summary, (1, 1, 1, []))
        self.assertEqual(tuple(rows[0]), CSV_COLUMNS)
        self.assertEqual(rows[0]["Billing Period 計費期間"], "113/01/01~113/01/31")
        self.assertEqual(rows[0]["基本電費"], "634220")
        # A figure not read is 0; what is not a figure stays empty.
        self.assertEqual(rows[0]["預繳費用"], "0")
        self.assertEqual(rows[0]["計費度數_尖峰度數"], "0")

    def test_details_add_the_source_page_and_what_to_check(self):
        _, rows = read_back(True)
        self.assertEqual(tuple(rows[0]), CSV_COLUMNS + DETAIL_COLUMNS)
        self.assertEqual((rows[0]["來源檔案"], rows[0]["需人工檢查"]), ("a.png", "缺少流動電費"))

    def test_details_are_written_unless_turned_off(self):
        for flags, expected in (([], True), (["--no-details"], False)):
            with tempfile.TemporaryDirectory() as folder, self.subTest(flags=flags):
                bill = Path(folder) / "a.png"
                bill.touch()
                with mock.patch.object(bill_csv, "write_csv", return_value=bill_csv.Summary(1, 0, 0, [])) as write:
                    self.assertEqual(bill_csv.main([str(bill), *flags]), 0)
                self.assertEqual(write.call_args.kwargs["details"], expected)

    def test_no_arguments_open_the_window(self):
        with mock.patch("bill_reader.gui.run", return_value=0) as run:
            self.assertEqual(bill_csv.main([]), 0)
        run.assert_called_once()

    def test_each_bill_and_file_is_reported_as_it_goes(self):
        events = [PageDone(Path("a.png"), None, Record()), FileFinished(Path("a.png")), FileFinished(Path("b.png"), "ValueError: broken")]
        said, finished = [], []
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(bill_csv, "iter_pages", lambda files, extractor: iter(events)):
            summary = write_csv([Path("a.png"), Path("b.png")], Path(folder) / "bills.csv", object(), say=said.append, on_file=finished.append)
        self.assertEqual(finished, [1, 2])
        self.assertEqual(summary.failures, ["b.png: ValueError: broken"])
        self.assertIn("to review", said[0])
        self.assertIn("failed", said[1])


class WindowWorkTests(unittest.TestCase):
    def events_of(self, paths, out, write_csv=None):
        from bill_reader import gui

        events = queue.Queue()
        with mock.patch.object(gui, "write_csv", write_csv):
            gui.work(paths, out, events)
        return [events.get_nowait() for _ in range(events.qsize())]

    def test_an_empty_folder_says_so(self):
        with tempfile.TemporaryDirectory() as folder:
            (kind, message), = self.events_of([folder], Path(folder) / "bills.csv")
        self.assertEqual(kind, "error")
        self.assertIn("No bill files found", message)

    def test_a_csv_held_open_by_excel_says_so(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "a.png").touch()
            events = self.events_of([folder], Path(folder) / "bills.csv", write_csv=mock.Mock(side_effect=PermissionError))
        self.assertEqual(events[-1][0], "error")
        self.assertIn("Excel", events[-1][1])

    def test_it_ends_with_the_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "a.png").touch()
            events = self.events_of([folder], Path(folder) / "bills.csv", write_csv=mock.Mock(return_value=bill_csv.Summary(1, 2, 1, [])))
        self.assertEqual(events[0], ("total", 1))
        self.assertEqual(events[-1][0], "done")
        self.assertIn("1 bill(s), 1 to review", events[-1][1])
