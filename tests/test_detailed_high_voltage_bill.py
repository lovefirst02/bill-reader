import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bill_reader.parser import OcrLine, parse_bill_layout


class DetailedHighVoltageBillTests(unittest.TestCase):
    def setUp(self):
        self.lines = [
            OcrLine("Billing period:20260601-20260630", 50, 150, 326, 23),
            OcrLine("基本資料", 203, 185, 136, 35),
            OcrLine("計費內容", 648, 185, 133, 35),
            OcrLine("電價種類：", 50, 242, 96, 21),
            OcrLine("高壓電力非營業用", 294, 242, 159, 21),
            OcrLine("時間種類：", 50, 263, 92, 21),
            OcrLine("三段式時間電價", 293, 259, 140, 26),
            OcrLine("繳費期限：", 53, 341, 96, 24),
            OcrLine("115年07月23日", 294, 342, 141, 20),
            OcrLine("基本電費(約定)：", 495, 221, 145, 24),
            OcrLine("335,400.0元", 703, 223, 119, 20),
            OcrLine("流動電費：", 494, 240, 91, 24),
            OcrLine("1,558,228.5元", 704, 242, 137, 21),
            OcrLine("功率因數調整費：", 496, 262, 148, 21),
            OcrLine("-25,450.3元", 703, 263, 115, 20),
            OcrLine("稅前應缴總金额：", 496, 321, 148, 21),
            OcrLine("1.591,649.0元", 703, 322, 137, 21),
            OcrLine("應繳總金額：", 497, 382, 123, 24),
            OcrLine("1,671,231元", 698, 383, 121, 21),
            OcrLine("用電資料", 216, 642, 129, 32),
            OcrLine("電表資料", 647, 640, 128, 32),
            OcrLine("計費度數", 65, 686, 79, 21),
            OcrLine("尖峰度數：", 182, 676, 89, 21),
            OcrLine("128210", 328, 676, 73, 21),
            OcrLine("離峰度數：", 184, 695, 87, 21),
            OcrLine("140054", 328, 695, 74, 21),
            OcrLine("尖峰度數：", 185, 714, 87, 21),
            OcrLine("31190", 328, 714, 62, 21),
            OcrLine("轉供度數", 66, 743, 79, 21),
            OcrLine("半尖峰度數：", 184, 731, 107, 24),
            OcrLine("244800", 328, 733, 72, 18),
            OcrLine("週六半尖峰度數：68600", 184, 750, 206, 21),
            OcrLine("離峰度數：", 184, 769, 90, 24),
            OcrLine("253146", 329, 772, 72, 18),
            OcrLine("經常（尖峰）契約：", 68, 790, 167, 20),
            OcrLine("1500", 330, 790, 49, 19),
            OcrLine("經常（尖峰）需量：", 69, 808, 164, 21),
            OcrLine("1428", 330, 809, 49, 18),
            OcrLine("功率因數（%）：", 70, 864, 145, 21),
            OcrLine("100", 331, 864, 38, 18),
            OcrLine("組別：01(經常)", 498, 675, 98, 17),
            OcrLine("表號：021900276", 634, 674, 121, 17),
            OcrLine("電表倍數：2000", 785, 675, 107, 14),
            OcrLine("表別", 495, 691, 43, 22),
            OcrLine("上期指數", 582, 689, 70, 22),
            OcrLine("本期指數", 838, 689, 78, 22),
            OcrLine("01", 495, 712, 27, 20),
            OcrLine("06280.8", 582, 711, 78, 20),
            OcrLine("06403.2", 838, 710, 77, 18),
        ]



    def test_energy_groups_recover_layout_when_meter_title_is_missed(self):
        """Catches readable usage rows being flattened into charge_info."""
        lines = [
            line for line in self.lines if not line.text.startswith("電表資料")
        ]

        result = parse_bill_layout(lines)

        self.assertEqual(
            result.get("energy_usage.energy_consumption"),
            {"peak_kwh": 128210, "off_peak_kwh": 140054},
        )
        self.assertEqual(
            result.get("energy_usage.transferred_energy"),
            {
                "peak_kwh": 31190,
                "semi_peak_kwh": 244800,
                "saturday_semi_peak_kwh": 68600,
                "off_peak_kwh": 253146,
            },
        )

    def test_original_panel_titles_classify_without_annotation_text(self):
        """Catches the optional blue Billing period annotation becoming required."""
        lines = [
            line for line in self.lines if "Billing period" not in line.text
        ]

        result = parse_bill_layout(lines)

        self.assertEqual(
            result.get("energy_usage.energy_consumption"),
            {"peak_kwh": 128210, "off_peak_kwh": 140054},
        )
        self.assertEqual(
            result.get("energy_usage.transferred_energy"),
            {
                "peak_kwh": 31190,
                "semi_peak_kwh": 244800,
                "saturday_semi_peak_kwh": 68600,
                "off_peak_kwh": 253146,
            },
        )


    def test_uses_group_ranges_for_compact_usage_table(self):
        """Catches midpoint grouping and the previous-period rows leaking in."""
        lines = [
            OcrLine("Billing period:20260601-20260630", 342, 1211, 697, 57),
            OcrLine("基本資料", 662, 1282, 293, 80),
            OcrLine("計費內容", 1629, 1287, 297, 77),
            OcrLine("用電資料", 685, 2252, 278, 73),
            OcrLine("電表資料", 1619, 2266, 285, 73),
            OcrLine("計費度數", 367, 2338, 169, 48),
            OcrLine("尖峰度數：", 617, 2326, 187, 44),
            OcrLine("128210", 928, 2333, 155, 42),
            OcrLine("離峰度數：", 619, 2365, 185, 44),
            OcrLine("140054", 929, 2372, 153, 40),
            OcrLine("尖峰度數：", 619, 2404, 187, 47),
            OcrLine("31190", 927, 2411, 129, 42),
            OcrLine("轉供度數", 371, 2458, 169, 46),
            OcrLine("半尖峰度數：", 620, 2443, 229, 47),
            OcrLine("244800", 928, 2450, 155, 42),
            OcrLine("调六半尖峰度數：68600", 621, 2481, 438, 52),
            OcrLine("離峰度數：", 621, 2524, 187, 43),
            OcrLine("253146", 930, 2532, 153, 38),
            OcrLine("經常（尖峰）契约：", 374, 2556, 349, 51),
            OcrLine("1500", 930, 2570, 105, 40),
            OcrLine("前期轉供度數", 387, 2885, 242, 45),
            OcrLine("週六半尖峰度數：80400", 630, 2911, 433, 48),
        ]

        result = parse_bill_layout(lines)

        self.assertEqual(
            result.get("energy_usage.transferred_energy"),
            {
                "peak_kwh": 31190,
                "semi_peak_kwh": 244800,
                "saturday_semi_peak_kwh": 68600,
                "off_peak_kwh": 253146,
            },
        )

    def test_a_peak_row_printed_as_regular_peak_is_read_even_at_zero(self):
        """Catches 經常(尖峰)度數 being skipped, which left a 0 peak flagged as missing."""
        lines = list(self.lines)
        lines[lines.index(next(line for line in lines if line.text == "尖峰度數："))] = OcrLine("經常(尖峰)度數：", 182, 676, 140, 21)
        lines[lines.index(next(line for line in lines if line.text == "128210"))] = OcrLine("0", 328, 676, 12, 21)

        result = parse_bill_layout(lines)

        self.assertEqual(
            result.get("energy_usage.energy_consumption"),
            {"peak_kwh": 0, "off_peak_kwh": 140054},
        )
        self.assertEqual(result.get("energy_usage.transferred_energy.peak_kwh"), 31190)

    def test_a_bill_with_no_transfers_has_its_rows_billed(self):
        """Catches the whole usage table being dropped when 轉供度數 is not printed."""
        lines = [
            OcrLine("基本資料", 203, 85, 136, 35),
            OcrLine("計費內容", 648, 85, 133, 35),
            OcrLine("用電資料", 216, 642, 129, 32),
            OcrLine("電表資料", 647, 640, 128, 32),
            OcrLine("計費度數", 65, 686, 79, 21),
            OcrLine("經常(尖峰)度數：", 182, 686, 140, 21),
            OcrLine("8800", 328, 686, 50, 21),
            OcrLine("底度：", 65, 710, 50, 21),
            OcrLine("120", 328, 710, 38, 21),
            OcrLine("功率因數(%)：", 65, 734, 120, 21),
            OcrLine("100", 328, 734, 38, 21),
        ]

        result = parse_bill_layout(lines)

        self.assertEqual(result.get("energy_usage.energy_consumption"), {"peak_kwh": 8800})
        self.assertIsNone(result.get("energy_usage.transferred_energy"))

    def test_kwh_rows_whose_block_heading_was_lost_are_still_read(self):
        """Catches the rows going to 電表資料 when glare hides 基本資料 and 用電資料."""
        lines = [
            OcrLine("計費內容", 648, 85, 133, 35),
            OcrLine("電表資料", 647, 640, 128, 32),
            OcrLine("計費度數", 65, 686, 79, 21),
            OcrLine("經常(尖峰)度數:8800", 182, 686, 200, 21),
            OcrLine("底度:", 65, 710, 50, 21),
            OcrLine("120", 328, 710, 38, 21),
        ]

        result = parse_bill_layout(lines)

        self.assertEqual(result.get("user_info.energy_consumption"), {"peak_kwh": 8800})

    def test_boundary_adjacent_peak_belongs_to_transfer_group(self):
        """Catches the first transfer row being pulled into billed consumption."""
        result = parse_bill_layout(self.lines)

        self.assertEqual(
            result.get("energy_usage.transferred_energy.peak_kwh"), 31190
        )

    def test_usage_rows_are_not_dropped_at_low_pdf_render_scale(self):
        """Catches the fixed x<150 filter deleting rows rendered at low DPI."""
        scaled = [
            OcrLine(
                line.text,
                line.x * 0.5,
                line.y * 0.5,
                line.width * 0.5,
                line.height * 0.5,
            )
            for line in self.lines
        ]

        result = parse_bill_layout(scaled)

        self.assertEqual(
            result.get("energy_usage.energy_consumption"),
            {"peak_kwh": 128210, "off_peak_kwh": 140054},
        )
        self.assertEqual(
            result.get("energy_usage.transferred_energy"),
            {
                "peak_kwh": 31190,
                "semi_peak_kwh": 244800,
                "saturday_semi_peak_kwh": 68600,
                "off_peak_kwh": 253146,
            },
        )

    def test_extracts_fields_from_each_detailed_layout_panel(self):
        result = parse_bill_layout(self.lines)

        self.assertEqual(
            result.get("user_info.billing_period"),
            {"start": "115/06/01", "end": "115/06/30"},
        )
        self.assertEqual(result.get("charge_info.basic_charge_twd"), 335400)
        self.assertEqual(result.get("charge_info.energy_charge_twd"), 1558228.5)
        self.assertEqual(result.get("charge_info.pretax_total_twd"), 1591649)
        self.assertEqual(result.get("charge_info.total_amount_twd"), 1671231)
        self.assertEqual(
            result.get("energy_usage.transferred_energy"),
            {
                "peak_kwh": 31190,
                "semi_peak_kwh": 244800,
                "saturday_semi_peak_kwh": 68600,
                "off_peak_kwh": 253146,
            },
        )



    def test_extracts_customer_number_from_inline_and_separate_value(self):
        """Catches the detailed Basic Info parser omitting the 電號 row."""
        variants = (
            [OcrLine("電號：00123456789", 50, 220, 210, 21)],
            [
                OcrLine("電號：", 50, 220, 80, 21),
                OcrLine("00-12-3456-78-9", 294, 220, 155, 21),
            ],
            [
                OcrLine("电號：", 50, 220, 80, 21),
                OcrLine("00123456789", 294, 220, 135, 21),
            ],
        )

        for extra_lines in variants:
            with self.subTest(lines=[line.text for line in extra_lines]):
                result = parse_bill_layout([*self.lines, *extra_lines])
                expected = {
                    "電號：00123456789": "00123456789",
                    "電號：": "00-12-3456-78-9",
                    "电號：": "00123456789",
                }[extra_lines[0].text]
                self.assertEqual(result.get("user_info.customer_number"), expected)


if __name__ == "__main__":
    unittest.main()


class LabelsAndPageFallbackTest(unittest.TestCase):
    def test_other_wordings_of_the_kwh_rows_mean_the_same(self):
        from bill_reader.parser import _canonical_label

        reword = lambda text: _canonical_label(OcrLine(text, 0, 0, 10, 10)).text
        self.assertEqual(reword("尖峰用電度數"), "尖峰度數")
        self.assertEqual(reword("週六半尖峰用電度數"), "週六半尖峰度數")
        self.assertEqual(reword("本期轉供度數(度)"), "轉供度數(度)")
        self.assertEqual(reword("用電度數(度)"), "計費度數(度)")
        # Not a current figure: left as printed.
        self.assertEqual(reword("前期轉供度數"), "前期轉供度數")
        self.assertEqual(reword("去年同期用電度數"), "去年同期用電度數")

    def test_a_meter_and_period_without_their_labels_are_found_by_their_look(self):
        lines = [OcrLine("45-09-1391-16-5", 89, 516, 282, 31), OcrLine("109/12/01~", 470, 1730, 150, 28), OcrLine("109/12/31", 470, 1760, 140, 28)]
        result = parse_bill_layout(lines)
        self.assertEqual(result["user_info"]["customer_number"], "45-09-1391-16-5")
        self.assertEqual(result["user_info"]["billing_period"], {"start": "109/12/01", "end": "109/12/31"})

    def test_two_different_meters_are_not_guessed_between(self):
        lines = [OcrLine("45-09-1391-16-5", 89, 516, 282, 31), OcrLine("45-09-1391-16-6", 89, 716, 282, 31)]
        self.assertFalse((parse_bill_layout(lines).get("user_info") or {}).get("customer_number"))

    def test_a_thousands_comma_read_as_a_point_is_put_back(self):
        from bill_reader.parser import _canonical_label

        reword = lambda text: _canonical_label(OcrLine(text, 0, 0, 10, 10)).text
        self.assertEqual(reword("2.068.925元"), "2,068,925元")
        self.assertEqual(reword("2068.925"), "2068,925")
        self.assertEqual(reword("***1.725.965元"), "***1,725,965元")
        # A charge's one decimal and a rate's four stay.
        self.assertEqual(reword("634220.0元"), "634220.0元")
        self.assertEqual(reword("2.6639"), "2.6639")

    def test_a_garbled_heading_is_known_by_its_english_half(self):
        from bill_reader.parser import _canonical_label

        self.assertTrue(_canonical_label(OcrLine("1費度()/Energy Consumption(kWh)", 0, 0, 10, 10)).text.startswith("計費度數"))

    def test_kwh_rows_without_their_headings_are_billed_then_transferred(self):
        rows = [("尖峰度數", "8000", 100), ("半尖峰度數", "3000", 130), ("離峰度數", "1000", 160),
                ("尖峰度數", "700", 220), ("半尖峰度數", "600", 250), ("離峰度數", "500", 280)]
        lines = [OcrLine("用戶資訊", 50, 40, 200, 30)]
        for label, value, y in rows:
            lines += [OcrLine(label, 60, y, 120, 26), OcrLine(value, 400, y, 80, 26)]
        result = parse_bill_layout(lines)
        self.assertEqual(result.get("user_info.energy_consumption.peak_kwh"), 8000)
        self.assertEqual(result.get("user_info.transferred_energy.off_peak_kwh"), 500)

    def test_a_reading_and_a_charge_date_are_not_a_period(self):
        result = parse_bill_layout([OcrLine("113/04/10~113/04/16", 100, 100, 300, 30)])
        self.assertFalse((result.get("user_info") or {}).get("billing_period"))

