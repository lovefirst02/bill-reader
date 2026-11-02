"""What is on a page but is not this bill's: a previous period's charge, or another company's bill. Made-up values."""

import unittest

from bill_reader.parser import OcrLine, parse_bill_layout
from bill_reader.reader import has_bill_values, labels_read

CHARGES = [("上期流動電費", "8,888元"), ("基本電費", "5,000元"), ("流動電費", "10,000元"),
           ("稅前應繳總金額", "15,000元"), ("營業稅", "750元"), ("應繳總金額", "15,750元")]


def page(cells, joined=False, heading=True):
    lines = []
    for number, (label, value) in enumerate(cells):
        y = 120 + number * 70
        if joined:  # a PDF's text: label and figure in one cell
            lines.append(OcrLine(label + value, 120, y, 600, 30))
        else:
            lines += [OcrLine(label, 120, y, 30 * len(label), 30), OcrLine(value, 620, y, 30 * len(value), 30)]
    if heading:
        lines.insert(0, OcrLine("計費內容", 120, 60, 120, 30))
    bill = parse_bill_layout(lines)
    bill.ocr_lines = lines  # as page.py keeps them
    return bill


class PreviousPeriodTests(unittest.TestCase):
    def test_the_previous_periods_energy_charge_is_not_this_ones(self):
        for joined in (False, True):
            with self.subTest(joined=joined):
                self.assertEqual(page(CHARGES, joined).get("charge_info.energy_charge_twd"), 10000)

    def test_a_note_explaining_the_charge_is_not_it(self):
        bill = page([("基本電費", "5,000元"), ("註1:實際流動電費=", "8,888元"), ("應繳總金額", "5,250元")])
        self.assertIsNone(bill.get("charge_info.energy_charge_twd"))


class OtherBillTests(unittest.TestCase):
    def test_a_page_with_a_total_and_no_other_taipower_word_is_left_out(self):
        water = page([("水號", "12-34-5678-90-0"), ("用水度數", "35"), ("應繳總金額", "540元")], heading=False)
        self.assertEqual(labels_read(water), 1)
        self.assertFalse(has_bill_values(water))

    def test_a_page_with_a_few_taipower_words_is_kept(self):
        bill = page(CHARGES[1:])
        self.assertGreaterEqual(labels_read(bill), 2)
        self.assertTrue(has_bill_values(bill))

    def test_a_pdfs_trusted_text_is_not_counted(self):
        text = page([("應繳總金額", "540元")], joined=True)
        text["quality"]["source"] = "pdf_text"
        text["charge_info"]["basic_charge_twd"] = 500
        self.assertTrue(has_bill_values(text))


if __name__ == "__main__":
    unittest.main()
