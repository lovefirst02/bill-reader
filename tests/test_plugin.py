import contextlib
import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from openpyxl import load_workbook

from bill_reader import watch as bill_watch
from bill_reader.cli import main as cli
from bill_reader.master import BILLS, FILES
from bill_reader.watch import Site, Watcher, load_sites
from test_watch import METER, PERIOD, FakeReads

# A landlord's bill made up for the tests: "landlord|<unit>|<period>|<pretax>|<kwh>".
PLUGIN = '''
import logging

def read(path, site):
    if path.suffix == ".xlsx":
        return None
    text = path.read_text(encoding="utf-8")
    if not text.startswith("landlord|"):
        return None
    _, unit, period, pretax, kwh = text.split("|")
    if pretax == "boom":
        raise ValueError("no receipt for us")
    logging.getLogger("bill_reader.plugin").info("read %s", path.name)
    return [{
        "Billing Period 計費期間": period,
        "Custom No. 電號": "landlord-" + unit,
        "稅前應繳總金額": int(pretax),
        "計費度數_尖峰度數": float(kwh),
        "電費單價": 4.5,
        "需人工檢查": "kWh from the amount",
    }]
'''


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.plugin = self.root / "plugins" / "landlord.py"
        self.plugin.parent.mkdir()
        self.plugin.write_text(PLUGIN, encoding="utf-8")
        self.master = self.root / "master.xlsx"
        self.site = Site("Leased", self.root / "leased", self.master, self.plugin.resolve())
        self.site.folder.mkdir()
        self.reads = FakeReads()
        patch = mock.patch.object(bill_watch, "iter_pages", self.reads)
        patch.start()
        self.addCleanup(patch.stop)
        self.watcher = Watcher([self.site], self.root / "state", reader=object(), clock=lambda: time.time() + 3600)

    def tearDown(self):
        self.watcher.close()
        self.temp.cleanup()

    def file(self, name, text):
        path = self.site.folder / name
        path.write_text(text, encoding="utf-8")
        return path

    def sheet(self, title=BILLS):
        sheet = load_workbook(self.master)[title]
        header = [cell.value for cell in sheet[1]]
        return [dict(zip(header, (cell.value for cell in row))) for row in sheet.iter_rows(min_row=2)]

    def test_a_landlord_bill_is_read_by_the_plugin_and_a_taipower_bill_by_the_reader(self):
        self.file("rent.pdf", "landlord|3F|114/10/01~114/10/31|12000|2666.67")
        self.file("taipower.pdf", "00-00-0000-00-1|114/10/01~114/10/31|1000|")
        self.watcher.run_once()
        self.assertEqual(self.reads.read, ["taipower.pdf"])
        rows = {row[METER]: row for row in self.sheet()}
        rent = rows["landlord-3F"]
        self.assertEqual((rent[PERIOD], rent["稅前應繳總金額"], rent["計費度數_尖峰度數"]), ("114/10/01~114/10/31", 12000, 2666.67))
        self.assertEqual((rent["Site"], rent["來源檔案"], rent["頁碼"], rent["基本電費"]), ("Leased", "rent.pdf", 1, 0))
        self.assertTrue(rent["ID"].endswith("-p1"))
        self.assertNotIn("電費單價", rent)  # the master has no such column
        self.assertIn("00-00-0000-00-1", rows)

    def test_a_column_of_its_own_is_written_where_the_master_has_it(self):
        self.file("a.pdf", "landlord|3F|114/10/01~114/10/31|12000|2666.67")
        self.watcher.run_once()
        book = load_workbook(self.master)
        book[BILLS].cell(1, book[BILLS].max_column + 1, "電費單價")
        book.save(self.master)
        self.file("b.pdf", "landlord|3F|114/11/01~114/11/30|9000|2000")
        self.watcher.run_once()
        self.assertEqual([row["電費單價"] for row in self.sheet()], [None, 4.5])

    def test_a_plugin_that_fails_fails_the_file_and_says_on_which_line(self):
        self.file("a.pdf", "landlord|3F|114/10/01~114/10/31|boom|0")
        with self.assertLogs("bill_reader.watch", "WARNING"):
            self.watcher.run_once()
        status = {row["來源檔案"]: row for row in self.sheet(FILES)}["a.pdf"]
        self.assertEqual(status["狀態"], "失敗")
        self.assertIn("第 12 行 ValueError: no receipt for us", status["錯誤"])

    def test_a_row_without_its_meter_or_with_a_figure_as_text_fails_the_file(self):
        for answer, words in (('[{"Billing Period 計費期間": "114/10/01~114/10/31"}]', "沒有「Custom No. 電號」"),
                              ('[{"Billing Period 計費期間": "p", "Custom No. 電號": "m", "應繳總金額": "1,000"}]', "要是數字"),
                              ('{"Custom No. 電號": "m"}', "要回傳 list")):
            with self.subTest(words):
                plugin = self.root / "plugins" / f"bad{abs(hash(answer))}.py"
                plugin.write_text(f"def read(path, site):\n    return {answer}\n", encoding="utf-8")
                with self.assertRaises(bill_watch.PluginError) as failed:
                    bill_watch.read_rows(plugin, self.root / "x.pdf", "Leased")
                self.assertIn(words, str(failed.exception))

    def test_a_spreadsheet_the_plugin_leaves_is_listed_with_no_bill(self):
        self.file("receipts.xlsx", "x")
        self.watcher.run_once()
        self.assertEqual(self.reads.read, [])
        self.assertEqual({row["來源檔案"]: row["狀態"] for row in self.sheet(FILES)}, {"receipts.xlsx": "沒有帳單頁"})

    def test_a_site_without_a_plugin_does_not_list_spreadsheets(self):
        self.file("receipts.xlsx", "x")
        listing = bill_watch.list_site(Site("Plain", self.site.folder, self.master))
        self.assertEqual(listing.files, [])

    def test_plugin_test_prints_the_rows_and_marks_columns_the_master_lacks(self):
        bill = self.file("rent.pdf", "landlord|3F|114/10/01~114/10/31|12000|2666.67")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli(["plugin-test", str(self.plugin), str(bill)])
        self.assertEqual(code, 0)
        self.assertIn("Custom No. 電號：'landlord-3F'", out.getvalue())
        self.assertIn("電費單價：4.5　（主表要自己加這欄才會寫入）", out.getvalue())


class PluginSitesTests(unittest.TestCase):
    def test_a_plugin_is_found_beside_sites_csv_and_a_missing_one_is_named(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "landlord.py").write_text("def read(path, site):\n    return None\n", encoding="utf-8")
            path = Path(folder) / "sites.csv"
            path.write_text("Site,Folder,Master,Enabled,Plugin\nA,C:\\a,C:\\m.xlsx,Y,landlord.py\nB,C:\\b,C:\\m.xlsx,Y,\n", encoding="utf-8")
            a, b = load_sites(path)
            self.assertEqual((a.plugin, b.plugin), ((Path(folder) / "landlord.py").resolve(), None))
            path.write_text("Site,Folder,Master,Enabled,Plugin\nA,C:\\a,C:\\m.xlsx,Y,other.py\n", encoding="utf-8")
            with self.assertRaises(SystemExit) as stopped:
                load_sites(path)
            self.assertIn("line 2: Plugin other.py not found", str(stopped.exception))


if __name__ == "__main__":
    unittest.main()


class ExampleTests(unittest.TestCase):
    def test_the_example_plugin_loads_and_reads_a_period_either_way(self):
        from bill_reader.plugin import load

        example = load(Path(__file__).resolve().parent.parent / "packaging" / "plugin_example.py")
        self.assertEqual(example._period([["期間", "2025/10/01 ~ 2025/10/31"]]), "114/10/01~114/10/31")
        self.assertEqual(example._period([["計費期間 114/9/1-114/9/30"]]), "114/09/01~114/09/30")
        self.assertIsNone(example.read(Path("x.xlsx"), "Leased"))
