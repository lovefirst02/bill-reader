import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from openpyxl import load_workbook
from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter

from bill_reader import watch as bill_watch
from bill_reader.evidence import Mark, _turned_back, has_marks, mark_pdf, mark_photo, page_marks
from bill_reader.lines import BillResult, OcrLine
from bill_reader.locate import locate
from bill_reader.master import FILES
from bill_reader.reader import FileFinished, PageDone
from bill_reader.record import BillRecord
from bill_reader.watch import Site, Watcher, load_sites

PERIOD, METER = "Billing Period 計費期間", "Custom No. 電號"


def line(text, x, y, width=120, height=20):
    return OcrLine(text, x, y, width, height)


def made_up_bill(**charges):
    """A charge block on a 1000 x 1400 pixel page, all its figures made up."""
    lines = [
        line("計費期間", 100, 100), line("114/01/01~114/01/31", 300, 100, 200),
        line("稅前應繳總金額", 100, 400), line("3,000.0", 600, 400),
        line("上期預繳餘額", 100, 450), line("-500.0", 600, 450),
        line("繳費總金額", 100, 500), line("3,150", 600, 500),
        line("計費度數", 100, 700), line("尖峰度數", 120, 730), line("800", 600, 730), line("離峰度數", 120, 760), line("200", 600, 760),
        line("轉供度數", 100, 800), line("尖峰度數", 120, 830), line("50", 600, 830),
    ]
    bill = BillResult({"charge_info": {"pretax_total_twd": 3000, "total_amount_twd": 3150, "business_tax_twd": 150,
                                       "previous_prepaid_balance_twd": -500, **charges},
                       "quality": {"warnings": [], "image_size": (1000, 1400), "dpi": 200, "source": "pdf_text"}})
    bill.ocr_lines = lines
    return bill


def made_up_row(**values):
    return {PERIOD: "114/01/01~114/01/31", METER: "00-00-0000-00-1", "稅前應繳總金額": 3500, "應繳總金額": 3650,
            "計費度數_尖峰度數": 800, "計費度數_離峰度數": 200, "轉供度數_尖峰度數": 50, **values}


def blank_pdf(path, pages=1):
    images = [Image.new("RGB", (1000, 1400), "white") for _ in range(pages)]
    images[0].save(path, "PDF", resolution=200, save_all=True, append_images=images[1:])
    return path


class LocateTests(unittest.TestCase):
    def test_a_value_is_found_beside_its_label_and_a_wrapped_period_across_two_lines(self):
        lines = [line("稅前應繳總金額", 100, 400), line("3,000.0", 600, 400), line("計費期間", 100, 100),
                 line("114/01/01~", 100, 122), line("114/01/31", 100, 144)]
        self.assertEqual(locate(lines, ("稅前應繳總金額",), 3000), (96, 396, 628, 28))
        self.assertIsNotNone(locate(lines, ("計費期間",), "114/01/01~114/01/31"))
        self.assertIsNone(locate(lines, ("稅前應繳總金額",), 3001))

    def test_a_kwh_row_is_found_only_under_its_own_group(self):
        lines = made_up_bill().ocr_lines
        billed, transferred = (locate(lines, ("尖峰度數",), value, group) for value, group in
                               ((800, "energy_consumption"), (50, "transferred_energy")))
        self.assertEqual((billed[1], transferred[1]), (726, 826))
        self.assertIsNone(locate(lines, ("尖峰度數",), 800, "transferred_energy"))


class MarkTests(unittest.TestCase):
    def test_only_the_period_the_cost_and_the_usage_are_labelled(self):
        marks, missing = page_marks(made_up_bill(), made_up_row(), 1)
        shown = [mark.label for mark in marks if mark.label_box]
        self.assertEqual(missing, [])
        self.assertEqual(shown, ["Billing Period 2025-01-01 ~ 2025-01-31", "Electricity Cost 3,500 = 3,000 + 500",
                                 "Electricity Usage 1,000 kWh"])
        self.assertFalse([mark for mark in marks if "Transferred" in mark.label])  # transferred kWh are not marked

    def test_a_cost_paid_partly_from_a_balance_highlights_the_balance_under_the_same_label(self):
        marks, _ = page_marks(made_up_bill(), made_up_row(), 1)
        highlighted = [mark.label for mark in marks if mark.box and not mark.label_box]
        self.assertIn("Prepaid balance used 500", highlighted)  # its own words only in the highlight's comment
        self.assertIn("Electricity Cost 3,500", highlighted)
        self.assertIn("Electricity Usage peak 800 kWh", highlighted)

    def test_a_period_is_labelled_in_gregorian_dates_whatever_way_it_was_written(self):
        from bill_reader.evidence import _period_label

        self.assertEqual(_period_label("114/01/01~114/01/31"), "Billing Period 2025-01-01 ~ 2025-01-31")
        self.assertEqual(_period_label("113/12/16~114/1/15"), "Billing Period 2024-12-16 ~ 2025-01-15")
        self.assertEqual(_period_label("2025/02/01~2025/02/28"), "Billing Period 2025-02-01 ~ 2025-02-28")
        self.assertEqual(_period_label("114/01/01"), "Billing Period")

    def test_a_pre_tax_total_not_printed_is_marked_on_the_total_with_its_sum(self):
        bill = made_up_bill(pretax_total_twd=None, previous_prepaid_balance_twd=None)
        marks, _ = page_marks(bill, made_up_row(稅前應繳總金額=3000, 應繳總金額=3150), 1)
        self.assertIn("Electricity Cost 3,000 = 3,150 - 150", [mark.label for mark in marks])

    def test_labels_keep_off_the_printed_text_where_there_is_room(self):
        bill = made_up_bill()
        # Figures beside their labels, with the right of the page free.
        bill.ocr_lines = [OcrLine(l.text, 300 if l.x == 600 else l.x, l.y, 80 if l.x == 600 else l.width, l.height)
                          for l in bill.ocr_lines]
        marks, _ = page_marks(bill, made_up_row(), 1)
        printed = [(l.x / 1000, l.y / 1400, l.width / 1000, l.height / 1400) for l in bill.ocr_lines]
        for mark in marks:
            for box in printed if mark.label_box else ():
                overlap = (min(mark.label_box[0] + mark.label_box[2], box[0] + box[2]) - max(mark.label_box[0], box[0]),
                           min(mark.label_box[1] + mark.label_box[3], box[1] + box[3]) - max(mark.label_box[1], box[1]))
                self.assertFalse(overlap[0] > 0 and overlap[1] > 0, mark.label)

    def test_a_box_on_a_straightened_photo_turns_back_onto_the_photo(self):
        picture = Image.new("L", (1000, 1400), 0)
        ImageDraw.Draw(picture).rectangle((695, 295, 705, 305), fill=255)
        left, top, right, bottom = picture.rotate(10, resample=Image.Resampling.BICUBIC).getbbox()
        x, y, _, _ = _turned_back(((left + right) / 2 - 1, (top + bottom) / 2 - 1, 2, 2), (1000, 1400), 10)
        self.assertAlmostEqual(x + 1, 700, delta=2)
        self.assertAlmostEqual(y + 1, 300, delta=2)


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_marks_are_appended_and_the_original_bytes_stay(self):
        source = blank_pdf(self.root / "bill.pdf")
        marks = [Mark(1, "Electricity Cost 3,500", (0.5, 0.3, 0.2, 0.02), (0.5, 0.27, 0.3, 0.02)), Mark(1, "A note")]
        self.assertEqual(mark_pdf(source, self.root / "marked.pdf", marks), 3)
        self.assertTrue((self.root / "marked.pdf").read_bytes().startswith(source.read_bytes()))
        annotations = [a.get_object() for a in PdfReader(self.root / "marked.pdf").pages[0]["/Annots"]]
        self.assertEqual([a["/Subtype"] for a in annotations], ["/Square", "/FreeText", "/FreeText"])
        self.assertEqual({a["/T"] for a in annotations}, {"BillReader"})
        self.assertTrue(has_marks(self.root / "marked.pdf"))
        self.assertFalse(has_marks(source))

    def test_a_turned_page_is_left_unmarked(self):
        writer = PdfWriter(blank_pdf(self.root / "bill.pdf"))
        writer.pages[0].rotate(90)
        writer.write(self.root / "turned.pdf")
        self.assertEqual(mark_pdf(self.root / "turned.pdf", self.root / "out.pdf", [Mark(1, "x", (0.1, 0.1, 0.1, 0.1))]), 0)

    def test_a_photo_becomes_a_pdf_with_its_marks(self):
        Image.new("RGB", (900, 1200), "white").save(self.root / "photo.jpg")
        self.assertEqual(mark_photo(self.root / "photo.jpg", self.root / "photo.pdf", [Mark(1, "x", (0.1, 0.1, 0.1, 0.1))]), 1)
        self.assertEqual(len(PdfReader(self.root / "photo.pdf").pages), 1)


class BillReads:
    """iter_pages giving every file the made-up bill, with its lines."""

    def __init__(self):
        self.read = []

    def __call__(self, files, reader):
        for path in files:
            self.read.append(path.name)
            yield PageDone(path, 1 if path.suffix == ".pdf" else None, BillRecord(made_up_row(), []), made_up_bill())
            yield FileFinished(path)


class WatchEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.folder = self.root / "site"
        self.folder.mkdir()
        self.master = self.root / "shared" / "master.xlsx"
        self.reads = BillReads()
        for target, value in (("iter_pages", self.reads), ("has_bill_values", lambda bill: True)):
            patch = mock.patch.object(bill_watch, target, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.watcher = None

    def tearDown(self):
        if self.watcher:
            self.watcher.close()
        self.temp.cleanup()

    def watch(self, evidence, in_place):
        self.watcher = Watcher([Site("North", self.folder, self.master, None, evidence, in_place)], self.root / "state",
                               reader=object(), clock=lambda: time.time() + 3600)
        return self.watcher

    def files(self):
        sheet = load_workbook(self.master)[FILES]
        header = [cell.value for cell in sheet[1]]
        return {row[1]: dict(zip(header, row)) for row in sheet.iter_rows(min_row=2, values_only=True)}

    def test_a_pdf_marked_in_place_keeps_its_original_and_is_not_read_again(self):
        source = blank_pdf(self.folder / "bill.pdf")
        original = source.read_bytes()
        watcher = self.watch(self.master.parent / "Evidence" / "North", True)
        watcher.run_once()
        self.assertTrue(has_marks(source))
        self.assertTrue(source.read_bytes().startswith(original))
        self.assertEqual((self.root / "state" / "originals" / "North" / "bill.pdf").read_bytes(), original)
        self.assertEqual(self.files()["bill.pdf"]["證據"], "已標記在來源檔")
        watcher.run_once()
        self.assertEqual(self.reads.read, ["bill.pdf"])
        self.assertFalse(list(self.folder.glob("~$*")))

    def test_a_pdf_open_elsewhere_is_marked_once_it_is_closed(self):
        source = blank_pdf(self.folder / "bill.pdf")
        watcher = self.watch(self.master.parent / "Evidence" / "North", True)
        with mock.patch.object(bill_watch, "is_locked", return_value=True), self.assertLogs("bill_reader.watch", "INFO"):
            watcher.run_once()
        self.assertFalse(has_marks(source))
        self.assertTrue(self.files()["bill.pdf"]["證據"].startswith("等待標記"))
        watcher.run_once()
        self.assertTrue(has_marks(source))

    def test_a_photo_marked_in_place_gets_a_copy_beside_the_master(self):
        Image.new("RGB", (900, 1200), "white").save(self.folder / "photo.jpg")
        self.watch(self.master.parent / "Evidence" / "North", True).run_once()
        copy = self.master.parent / "Evidence" / "North" / "photo.jpg.evidence.pdf"
        self.assertTrue(has_marks(copy))
        self.assertEqual(self.files()["photo.jpg"]["證據"], f"已另存：{copy}")

    def test_a_copy_changed_by_hand_is_not_written_over(self):
        source = blank_pdf(self.folder / "bill.pdf")
        copies = self.root / "evidence"
        watcher = self.watch(copies, False)
        watcher.run_once()
        copy = copies / "bill.pdf.evidence.pdf"
        self.assertTrue(has_marks(copy))
        self.assertFalse(has_marks(source))
        copy.write_bytes(copy.read_bytes() + b"\n% moved a mark\n")
        changed = copy.read_bytes()
        blank_pdf(source, pages=2)  # a new version of the bill
        watcher.run_once()
        self.assertEqual(copy.read_bytes(), changed)
        self.assertTrue(self.files()["bill.pdf"]["證據"].startswith("已另存"))
        self.assertIn("證據檔已被修改，未覆蓋", self.files()["bill.pdf"]["證據"])


class EvidenceSitesTests(unittest.TestCase):
    def sites(self, evidence):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sites.csv"
            path.write_text(f"Site,Folder,Master,Enabled,Evidence\nA,C:\\bills\\a,C:\\shared\\m.xlsx,Y,{evidence}\n", encoding="utf-8")
            return load_sites(path)[0]

    def test_y_marks_in_place_with_photos_beside_the_master_and_a_folder_takes_copies(self):
        site = self.sites("Y")
        self.assertEqual((site.evidence, site.in_place), (Path("C:\\shared") / "Evidence" / "A", True))
        site = self.sites("C:\\evidence\\a")
        self.assertEqual((site.evidence, site.in_place), (Path("C:\\evidence\\a").resolve(), False))
        self.assertEqual((self.sites("").evidence, self.sites("N").evidence), (None, None))

    def test_a_folder_inside_the_watched_one_is_refused(self):
        with self.assertRaises(SystemExit) as stopped:
            self.sites("C:\\bills\\a\\marked")
        self.assertIn("line 2: Evidence should be", str(stopped.exception))


if __name__ == "__main__":
    unittest.main()
