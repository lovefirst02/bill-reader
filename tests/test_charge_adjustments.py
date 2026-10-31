import unittest

from test_prepaid_charge import bill


class ChargeAdjustmentTests(unittest.TestCase):
    def test_adjustment_preserves_small_printed_total(self):
        for label in ("本期預繳餘額", "上期預繳餘額", "電價調整金額", "補收(退)金額(含停電扣減)"):
            for amount in ("-1000元", None):
                with self.subTest(label=label, amount=amount):
                    result = bill([("流動電費", "1000元"), (label, amount), ("應繳總金額", "0元")])
                    self.assertEqual(result.get("charge_info.total_amount_twd"), 0)
