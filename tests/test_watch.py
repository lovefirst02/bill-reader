import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from openpyxl import load_workbook

from bill_reader import watch as bill_watch
from bill_reader.master import BILLS, CHANGES, FILES, ID, MasterLocked, write_master
from bill_reader.reader import FileFinished, PageDone
from bill_reader.record import BillRecord
from bill_reader.watch import Site, Watcher, load_sites

METER, PERIOD = "Custom No. 電號", "Billing Period 計費期間"


def made_up_row(meter="00-00-0000-00-0", period="114/01/01~114/01/31", basic=1000):
    return {PERIOD: period, METER: meter, "基本電費": basic, "應繳總金額": basic + 1, "需人工檢查": None}


class FakeReads:
    """iter_pages as if each file's first bytes named the row it holds; b"bad" fails."""

    def __init__(self):
        self.read = []

    def __call__(self, files, reader):
        for path in files:
            self.read.append(path.name)
            text = path.read_text()
            if text == "bad":
                yield FileFinished(path, "ValueError: broken")
                continue
            meter, period, basic, *_ = text.split("|")  # what follows stands for a highlighter's marks
            yield PageDone(path, 1, BillRecord(made_up_row(meter, period, int(basic)), []))
            yield FileFinished(path)


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.master = self.root / "shared" / "master.xlsx"
        self.sites = [Site("North", self.root / "north", self.master), Site("South", self.root / "south", self.master)]
        for site in self.sites:
            site.folder.mkdir()
        self.reads = FakeReads()
        patch = mock.patch.object(bill_watch, "iter_pages", self.reads)
        patch.start()
        self.addCleanup(patch.stop)
        self.watcher = self.new_watcher()

    def tearDown(self):
        self.watcher.close()
        self.temp.cleanup()

    def new_watcher(self, **options):
        return Watcher(self.sites, self.root / "state", reader=object(), clock=lambda: time.time() + 3600, **options)

    def bill(self, site, name, meter="00-00-0000-00-1", period="114/01/01~114/01/31", basic=1000, marks=""):
        path = self.sites[site].folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{meter}|{period}|{basic}|{marks}")
        return path

    def sheet(self, title=BILLS):
        book = load_workbook(self.master)
        sheet = book[title]
        header = [cell.value for cell in sheet[1]]
        return [dict(zip(header, (cell.value for cell in row))) for row in sheet.iter_rows(min_row=2)]

    def edit(self, change):
        book = load_workbook(self.master)
        change(book[BILLS])
        book.save(self.master)
        os.utime(self.master, (time.time(), time.time() + 5))

    def test_new_bills_from_every_site_and_subfolder_go_into_the_master(self):
        self.bill(0, "a.pdf", meter="00-00-0000-00-1")
        self.bill(1, "2025/03/b.pdf", meter="00-00-0000-00-2")
        self.watcher.run_once()
        rows = self.sheet()
        self.assertEqual([(row["Site"], row["來源檔案"], row[METER]) for row in rows],
                         [("North", "a.pdf", "00-00-0000-00-1"), ("South", "2025/03/b.pdf", "00-00-0000-00-2")])
        self.assertTrue(all(row[ID] for row in rows))
        self.assertEqual(rows[0]["預繳費用"], 0)  # a figure not read is 0, as in the CSV
        self.assertEqual([row["狀態"] for row in self.sheet(FILES)], ["完成", "完成"])

    def test_a_file_still_changing_waits(self):
        self.watcher.clock = time.time
        self.bill(0, "a.pdf")
        self.watcher.run_once()
        self.assertEqual(self.reads.read, [])
        self.assertFalse(self.master.exists())

    def test_a_hand_correction_stays_and_is_listed_in_changes(self):
        self.bill(0, "a.pdf", basic=1000)
        self.watcher.run_once()
        self.edit(lambda sheet: sheet.cell(2, [c.value for c in sheet[1]].index("基本電費") + 1, 1234))
        self.bill(0, "b.pdf", meter="00-00-0000-00-9")
        self.watcher.run_once()
        rows = self.sheet()
        self.assertEqual([row["基本電費"] for row in rows], [1234, 1000])
        (change,) = self.sheet(CHANGES)
        self.assertEqual((change["類型"], change["欄位"], change["主表的值"], change["程式讀到的值"]), ("人工修改", "基本電費", 1234, 1000))

    def test_a_row_deleted_by_hand_is_not_added_again(self):
        self.bill(0, "a.pdf")
        self.watcher.run_once()
        self.edit(lambda sheet: sheet.delete_rows(2))
        self.watcher.close()
        self.watcher = self.new_watcher()
        self.bill(0, "b.pdf", meter="00-00-0000-00-9")
        self.watcher.run_once()
        self.assertEqual([row[METER] for row in self.sheet() if row[ID]], ["00-00-0000-00-9"])

    def test_the_same_bill_in_another_file_is_listed_not_added(self):
        self.bill(0, "a.pdf", basic=1000)
        self.bill(1, "copy.pdf", basic=999)
        self.watcher.run_once()
        self.assertEqual(len(self.sheet()), 1)
        changes = self.sheet(CHANGES)
        self.assertEqual([(change["類型"], change["欄位"], change["主表的值"], change["程式讀到的值"]) for change in changes],
                         [("重複帳單", "基本電費", 1000, 999), ("重複帳單", "應繳總金額", 1001, 1000)])

    def test_a_moved_file_is_not_read_again(self):
        path = self.bill(0, "a.pdf")
        self.watcher.run_once()
        path.rename(path.with_name("renamed.pdf"))
        self.watcher.run_once()
        self.assertEqual(self.reads.read, ["a.pdf"])
        self.assertEqual(len(self.sheet()), 1)
        self.assertEqual({row["來源檔案"]: row["狀態"] for row in self.sheet(FILES)}, {"a.pdf": "來源已刪除", "renamed.pdf": "完成"})

    def test_a_file_marked_with_a_highlighter_adds_nothing(self):
        self.bill(0, "a.pdf")
        self.watcher.run_once()
        self.bill(0, "a.pdf", marks="yellow")
        self.watcher.run_once()
        self.assertEqual(self.reads.read, ["a.pdf", "a.pdf"])
        self.assertEqual(len(self.sheet()), 1)
        self.assertEqual(self.sheet(CHANGES), [])

    def test_a_changed_file_that_reads_differently_is_listed_not_added(self):
        self.bill(0, "a.pdf", basic=1000)
        self.watcher.run_once()
        self.bill(0, "a.pdf", basic=1200, marks="yellow")
        self.watcher.run_once()
        self.assertEqual([row["基本電費"] for row in self.sheet()], [1000])
        changes = [(change["類型"], change["欄位"], change["主表的值"], change["程式讀到的值"]) for change in self.sheet(CHANGES)]
        self.assertIn(("來源檔已更新", "基本電費", 1000, 1200), changes)

    def test_a_changed_file_is_known_without_its_meter_or_with_it_corrected(self):
        self.bill(0, "a.pdf", meter="")
        self.bill(0, "b.pdf", meter="00-00-0000-00-7")
        self.watcher.run_once()
        meter_column = lambda sheet: [c.value for c in sheet[1]].index(METER) + 1
        self.edit(lambda sheet: (sheet.cell(2, meter_column(sheet), "00-00-0000-00-2"), sheet.cell(3, meter_column(sheet), "00-00-0000-00-8")))
        self.bill(0, "a.pdf", meter="", marks="yellow")
        self.bill(0, "b.pdf", meter="00-00-0000-00-7", marks="yellow")
        self.watcher.run_once()
        self.assertEqual([row[METER] for row in self.sheet()], ["00-00-0000-00-2", "00-00-0000-00-8"])

    def test_a_file_changed_before_it_reached_the_master_is_added_once(self):
        self.watcher.write_every = 1800
        self.watcher.written = {self.watcher._master_key(self.sites[0]): self.watcher.clock()}
        self.bill(0, "a.pdf", basic=1000)
        self.watcher.take(bill_watch.list_site(self.sites[0]))
        self.bill(0, "a.pdf", basic=1200)
        self.watcher.take(bill_watch.list_site(self.sites[0]))
        self.watcher.write_masters(now=True)
        self.assertEqual([row["基本電費"] for row in self.sheet()], [1200])
        self.assertEqual(self.sheet(CHANGES), [])

    def test_a_file_with_no_bill_says_so(self):
        path = self.sites[0].folder / "notice.pdf"
        path.write_text("x")
        with mock.patch.object(bill_watch, "iter_pages", lambda files, reader: iter([PageDone(files[0], 1, None), FileFinished(files[0])])):
            self.bill(0, "a.pdf")
            self.watcher.run_once()
        self.assertIn("沒有帳單頁", [row["狀態"] for row in self.sheet(FILES)])

    def test_columns_are_found_by_name_and_a_missing_one_stops_the_write(self):
        self.bill(0, "a.pdf")
        self.watcher.run_once()
        self.edit(lambda sheet: (sheet.insert_cols(1), sheet.cell(1, 1, "我的備註")))
        self.bill(0, "b.pdf", meter="00-00-0000-00-9")
        self.watcher.run_once()
        self.assertEqual([row[METER] for row in self.sheet()], ["00-00-0000-00-1", "00-00-0000-00-9"])
        self.edit(lambda sheet: sheet.delete_cols([c.value for c in sheet[1]].index("基本電費") + 1))
        before = self.master.read_bytes()
        self.bill(0, "c.pdf", meter="00-00-0000-00-8")
        with self.assertLogs("bill_reader.watch", "ERROR") as said:
            self.watcher.run_once()
        self.assertIn("基本電費", said.output[0])
        self.assertEqual(self.master.read_bytes(), before)

    def test_a_master_open_in_excel_is_written_later(self):
        self.bill(0, "a.pdf")
        with mock.patch("bill_reader.master.is_locked", return_value=True):
            self.watcher.run_once()
        self.assertFalse(self.master.exists())
        self.watcher.run_once()
        self.assertEqual(len(self.sheet()), 1)

    def test_a_master_changed_while_written_is_left_alone(self):
        self.bill(0, "a.pdf")
        self.watcher.run_once()
        self.bill(0, "b.pdf", meter="00-00-0000-00-9")
        real_save = load_workbook(self.master).__class__.save

        def save_while_someone_edits(book, name):
            real_save(book, name)
            os.utime(self.master, (time.time(), time.time() + 60))

        with mock.patch("openpyxl.workbook.workbook.Workbook.save", save_while_someone_edits):
            self.watcher.run_once()
        self.assertEqual(len(self.sheet()), 1)
        self.watcher.run_once()
        self.assertEqual(len(self.sheet()), 2)

    def test_writes_wait_for_write_every(self):
        self.watcher.close()
        clock = [time.time() + 3600]
        self.watcher = Watcher(self.sites, self.root / "state", reader=object(), clock=lambda: clock[0], write_every=1800)
        self.bill(0, "a.pdf")
        for site in self.sites:
            self.watcher.take(bill_watch.list_site(site))
        self.watcher.write_masters()
        self.assertEqual(len(self.sheet()), 1)
        self.bill(0, "b.pdf", meter="00-00-0000-00-9")
        clock[0] += 60
        self.watcher.take(bill_watch.list_site(self.sites[0]))
        self.watcher.write_masters()
        self.assertEqual(len(self.sheet()), 1)
        clock[0] += 1800
        self.watcher.write_masters()
        self.assertEqual(len(self.sheet()), 2)

    def test_a_file_that_fails_is_tried_a_few_times(self):
        self.bill(0, "a.pdf").write_text("bad")
        for _ in range(5):
            self.watcher.run_once()
        self.assertEqual(self.reads.read, ["a.pdf"] * 3)
        (file,) = self.sheet(FILES)
        self.assertEqual((file["狀態"], file["嘗試次數"]), ("失敗", 3))

    def test_a_folder_not_synced_does_not_mark_its_files_deleted(self):
        self.bill(0, "a.pdf")
        self.watcher.run_once()
        self.sites[0].folder.rename(self.root / "away")
        self.watcher.run_once()
        self.assertEqual([row["狀態"] for row in self.sheet(FILES)], ["完成"])

    def test_backups_are_kept_before_each_write(self):
        for number in range(13):
            self.bill(0, f"{number}.pdf", meter=f"00-00-0000-{number:02d}-0")
            self.watcher.run_once()
        self.assertEqual(len(list((self.root / "state" / "backups").glob("*.xlsx"))), 10)

    def test_an_excel_table_grows_with_the_rows(self):
        from openpyxl.worksheet.table import Table

        self.bill(0, "a.pdf")
        self.watcher.run_once()
        self.edit(lambda sheet: sheet.add_table(Table(displayName="Bills", ref=f"A1:{sheet.cell(1, sheet.max_column).column_letter}2")))
        self.bill(0, "b.pdf", meter="00-00-0000-00-9")
        self.watcher.run_once()
        self.assertTrue(load_workbook(self.master)[BILLS].tables["Bills"].ref.endswith("3"))


class MasterTests(unittest.TestCase):
    def test_a_workbook_open_in_excel_is_not_touched(self):
        with tempfile.TemporaryDirectory() as folder:
            master = Path(folder) / "m.xlsx"
            (Path(folder) / "~$m.xlsx").touch()
            with self.assertRaises(MasterLocked):
                write_master(master, [], {}, [], [], [], Path(folder) / "backups")
            self.assertFalse(master.exists())


class SitesTests(unittest.TestCase):
    def sites_of(self, text, encoding="utf-8-sig"):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sites.csv"
            path.write_bytes(text.encode(encoding))
            return load_sites(path)

    def test_rows_turned_off_are_left_out_and_excel_saved_big5_reads(self):
        sites = self.sites_of("Site,Folder,Master,Enabled\n北區,C:\\a,C:\\m.xlsx,Y\nB,C:\\b,C:\\m.xlsx,N\nC,C:\\c,C:\\m.xlsx,\n", "cp950")
        self.assertEqual([site.name for site in sites], ["北區", "C"])

    def test_mistakes_are_named_by_line(self):
        with self.assertRaises(SystemExit) as stopped:
            self.sites_of("Site,Folder,Master,Enabled\nA,C:\\a,C:\\m.csv,Y\nB,,C:\\m.xlsx,Y\nA2,C:\\a,C:\\m.xlsx,maybe\n")
        for text in ("line 2", "line 3", "line 4"):
            self.assertIn(text, str(stopped.exception))


if __name__ == "__main__":
    unittest.main()
