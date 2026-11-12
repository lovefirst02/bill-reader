import unittest

from bill_reader.record import bill_to_record
from bill_reader.parser import BillResult


def high_voltage_bill(**charge_overrides):
    charge_info = {
        "basic_charge_twd": 700000,
        "energy_charge_twd": 120000,
        "power_factor_adjustment_twd": -25000,
        "pretax_total_twd": 761905,
        "business_tax_twd": 38095,
        "total_amount_twd": 800000,
        **charge_overrides,
    }
    charge_info = {key: value for key, value in charge_info.items() if value is not None}
    return BillResult(
        {
            "user_info": {
                "customer_number": "00-12-3456-78-9",
                "billing_period": {"start": "109/12/01", "end": "109/12/31"},
                "energy_consumption": {
                    "peak_kwh": 8000,
                    "semi_peak_kwh": 3000,
                    "saturday_semi_peak_kwh": 3000,
                    "off_peak_kwh": 1000,
                },
                "transferred_energy": {
                    "peak_kwh": 100,
                    "semi_peak_kwh": 200,
                    "saturday_semi_peak_kwh": 300,
                    "off_peak_kwh": 400,
                },
            },
            "charge_info": charge_info,
            "quality": {"warnings": [], "page_number": 1},
        }
    )


def bill_to_row(bill, source=None):
    return bill_to_record(bill, source).row


class ExcelRowTests(unittest.TestCase):
    def test_maps_every_requested_column(self):
        row = bill_to_row(high_voltage_bill(prepaid_applied_twd=-5000), "bill.pdf")

        self.assertEqual(row["Billing Period 計費期間"], "109/12/01~109/12/31")
        self.assertEqual(row["Custom No. 電號"], "00-12-3456-78-9")
        self.assertEqual(row["計費度數_尖峰度數"], 8000)
        self.assertEqual(row["計費度數_週六半尖峰度數"], 3000)
        self.assertEqual(row["轉供度數_半尖峰度數"], 200)
        self.assertEqual(row["轉供度數_離峰度數"], 400)
        self.assertEqual(row["基本電費"], 700000)
        self.assertEqual(row["流動電費"], 120000)
        self.assertEqual(row["功率因數調整費"], -25000)
        self.assertEqual(row["預繳費用"], -5000)
        self.assertEqual(row["稅前應繳總金額"], 761905)
        self.assertEqual(row["應繳總金額"], 800000)
        self.assertEqual(row["來源檔案"], "bill.pdf")
        self.assertEqual(row["頁碼"], 1)

    def test_other_adjustment_is_total_due_minus_itemised_charges(self):
        row = bill_to_row(high_voltage_bill())
        # 800000 - 700000 - 120000 - (-25000): the official sample's only other
        # charge line is 分攤公共電費 5,000.
        self.assertEqual(row["其他調整費用"], 5000)
        self.assertIsNone(row["需人工檢查"])

    def test_prepayment_is_not_counted_twice(self):
        row = bill_to_row(high_voltage_bill(prepaid_applied_twd=-3000, total_amount_twd=797000))
        self.assertEqual(row["預繳費用"], -3000)
        self.assertEqual(row["其他調整費用"], 5000)

    def test_missing_pretax_total_does_not_block_other_adjustment(self):
        row = bill_to_row(high_voltage_bill(pretax_total_twd=None))
        self.assertEqual(row["其他調整費用"], 5000)

    def test_a_minus_the_pre_tax_total_contradicts_is_dropped(self):
        row = bill_to_row(high_voltage_bill(total_amount_twd=-800000))
        self.assertEqual(row["應繳總金額"], 800000)
        self.assertIsNone(row["需人工檢查"])

    def test_a_negative_total_nothing_contradicts_stays_and_is_flagged(self):
        row = bill_to_row(high_voltage_bill(total_amount_twd=-5000))
        self.assertEqual(row["應繳總金額"], -5000)
        self.assertIn("應繳總金額與稅前金額", row["需人工檢查"])

    def test_a_peak_row_not_read_is_flagged_where_others_were(self):
        bill = high_voltage_bill()
        del bill["user_info"]["energy_consumption"]["peak_kwh"]
        del bill["user_info"]["transferred_energy"]["peak_kwh"]
        del bill["user_info"]["transferred_energy"]["semi_peak_kwh"]
        row = bill_to_row(bill)
        self.assertIn("缺少計費度數_尖峰度數", row["需人工檢查"])
        self.assertIn("缺少轉供度數_尖峰度數", row["需人工檢查"])

    def test_transfers_with_a_semi_peak_row_need_no_peak_row(self):
        bill = high_voltage_bill()
        del bill["user_info"]["transferred_energy"]["peak_kwh"]
        self.assertNotIn("缺少轉供度數_尖峰度數", bill_to_row(bill)["需人工檢查"] or "")

    def test_energy_charged_with_no_kwh_read_is_flagged(self):
        bill = high_voltage_bill()
        del bill["user_info"]["energy_consumption"]
        self.assertIn("缺少計費度數", bill_to_row(bill)["需人工檢查"])

    def test_no_kwh_is_right_when_no_energy_was_charged(self):
        bill = high_voltage_bill(energy_charge_twd=0, basic_charge_twd=820000)
        del bill["user_info"]["energy_consumption"]
        self.assertNotIn("缺少計費度數", bill_to_row(bill)["需人工檢查"] or "")

    def test_a_pre_tax_total_not_read_is_the_total_less_its_tax(self):
        row = bill_to_row(high_voltage_bill(pretax_total_twd=None))
        self.assertEqual(row["稅前應繳總金額"], 761905)
        self.assertEqual(row["需人工檢查"], "稅前應繳總金額由應繳總金額推算")

    def test_a_pre_tax_total_not_read_nor_its_tax_is_the_total_over_1_05(self):
        row = bill_to_row(high_voltage_bill(pretax_total_twd=None, business_tax_twd=None, total_amount_twd=800001))
        self.assertEqual(row["稅前應繳總金額"], 761906)  # 761906 + its 5%, 38095, is 800001

    def test_a_pre_tax_total_past_a_prepayment_is_the_total_and_prepayment_less_tax(self):
        row = bill_to_row(high_voltage_bill(pretax_total_twd=None, prepaid_applied_twd=-3000, total_amount_twd=797000))
        self.assertEqual(row["稅前應繳總金額"], 761905)
        self.assertEqual(row["需人工檢查"], "稅前應繳總金額由應繳總金額＋預繳費用推算")

    def test_a_bill_its_prepayment_paid_in_full_still_gets_a_pre_tax_total(self):
        row = bill_to_row(high_voltage_bill(pretax_total_twd=None, prepaid_applied_twd=-800000, total_amount_twd=0))
        self.assertEqual(row["稅前應繳總金額"], 761905)

    def test_a_prepayment_the_tax_does_not_back_works_nothing_out(self):
        row = bill_to_row(high_voltage_bill(pretax_total_twd=None, prepaid_applied_twd=-226, total_amount_twd=797000))
        self.assertIsNone(row["稅前應繳總金額"])
        self.assertIn("缺少稅前應繳總金額", row["需人工檢查"])

    def test_a_pre_tax_total_read_beside_a_prepayment_stays(self):
        row = bill_to_row(high_voltage_bill(prepaid_applied_twd=-3000, total_amount_twd=797000))
        self.assertEqual(row["稅前應繳總金額"], 761905)
        self.assertIsNone(row["需人工檢查"])

    def test_a_misread_pre_tax_total_the_total_and_tax_agree_on_is_replaced(self):
        row = bill_to_row(high_voltage_bill(pretax_total_twd=767905))
        self.assertEqual(row["稅前應繳總金額"], 761905)
        self.assertIn("改用應繳總金額－營業稅（讀到 767,905）", row["需人工檢查"])

    def test_a_misread_total_the_pre_tax_total_and_tax_agree_on_is_replaced(self):
        row = bill_to_row(high_voltage_bill(total_amount_twd=120000))
        self.assertEqual(row["應繳總金額"], 800000)
        self.assertIn("改用稅前應繳總金額＋營業稅（讀到 120,000）", row["需人工檢查"])
        self.assertEqual(row["其他調整費用"], 5000)  # worked out from the total put right

    def test_a_bill_paid_from_a_prepaid_balance_is_what_the_period_cost(self):
        bill = high_voltage_bill(pretax_total_twd=None, business_tax_twd=None, total_amount_twd=0,
                                 previous_prepaid_balance_twd=-900000, current_prepaid_balance_twd=100000)
        row = bill_to_row(bill)
        self.assertEqual(row["應繳總金額"], 800000)
        # The balance used counts as pre-tax.
        self.assertEqual(row["稅前應繳總金額"], 800000)
        self.assertIsNone(row["預繳費用"])
        self.assertEqual(row["其他調整費用"], 5000)
        self.assertIn("使用預繳 800,000（上期 900,000－本期 100,000）", row["需人工檢查"])
        self.assertNotIn("不符", row["需人工檢查"])

    def test_a_bill_partly_paid_from_its_balance_adds_what_it_used_to_both_totals(self):
        bill = high_voltage_bill(total_amount_twd=4010, pretax_total_twd=3820, business_tax_twd=190,
                                 previous_prepaid_balance_twd=-5780, current_prepaid_balance_twd=0)
        row = bill_to_row(bill)
        self.assertEqual((row["應繳總金額"], row["稅前應繳總金額"]), (9790, 9600))
        self.assertNotIn("不符", row["需人工檢查"])

    def test_a_bill_printing_no_later_balance_used_all_of_it(self):
        bill = high_voltage_bill(total_amount_twd=4010, pretax_total_twd=3820, business_tax_twd=190,
                                 previous_prepaid_balance_twd=-5780)
        row = bill_to_row(bill)
        self.assertEqual((row["應繳總金額"], row["稅前應繳總金額"]), (9790, 9600))
        self.assertIn("未讀到本期預繳餘額，視為 0", row["需人工檢查"])

    def test_a_pre_tax_total_not_read_is_the_total_less_tax_before_the_balance_is_added(self):
        bill = high_voltage_bill(total_amount_twd=4010, pretax_total_twd=None, business_tax_twd=190,
                                 previous_prepaid_balance_twd=-5780)
        self.assertEqual(bill_to_row(bill)["稅前應繳總金額"], 9600)

    def test_a_balance_that_grew_or_an_applied_prepayment_adds_nothing(self):
        grew = high_voltage_bill(previous_prepaid_balance_twd=-1000, current_prepaid_balance_twd=6000)
        applied = high_voltage_bill(total_amount_twd=797000, prepaid_applied_twd=-3000,
                                    previous_prepaid_balance_twd=-5000, current_prepaid_balance_twd=2000)
        self.assertEqual(bill_to_row(grew)["應繳總金額"], 800000)
        self.assertEqual(bill_to_row(applied)["應繳總金額"], 797000)

    def test_a_bill_prepayment_paid_prints_no_pre_tax_total(self):
        row = bill_to_row(high_voltage_bill(pretax_total_twd=None, total_amount_twd=0))
        self.assertNotIn("缺少稅前應繳總金額", row["需人工檢查"] or "")

    def test_a_bill_with_no_time_of_use_rows_needs_no_peak(self):
        bill = high_voltage_bill()
        del bill["user_info"]["transferred_energy"]
        self.assertIsNone(bill_to_row(bill)["需人工檢查"])

    def test_a_meter_printed_without_dashes_is_spelled_as_the_others(self):
        bill = high_voltage_bill()
        bill["user_info"]["customer_number"] = "00123456789"
        self.assertEqual(bill_to_row(bill)["Custom No. 電號"], "00-12-3456-78-9")

    def test_detailed_layout_reads_energy_usage_block(self):
        bill = high_voltage_bill()
        bill["energy_usage"] = bill["user_info"].pop("energy_consumption")
        bill["energy_usage"] = {"energy_consumption": bill["energy_usage"]}
        self.assertEqual(bill_to_row(bill)["計費度數_離峰度數"], 1000)

    def test_missing_inputs_leave_other_adjustment_empty_and_flagged(self):
        row = bill_to_row(high_voltage_bill(energy_charge_twd=None))
        self.assertIsNone(row["其他調整費用"])
        self.assertIn("缺少流動電費", row["需人工檢查"])

    def test_missing_power_factor_counts_as_zero_and_is_flagged(self):
        row = bill_to_row(
            high_voltage_bill(power_factor_adjustment_twd=None, pretax_total_twd=830000,
                              total_amount_twd=871500, business_tax_twd=41500)
        )
        self.assertEqual(row["其他調整費用"], 51500)
        self.assertIn("功率因數調整費", row["需人工檢查"])

    def test_flags_reversed_billing_period(self):
        bill = high_voltage_bill()
        bill["user_info"]["billing_period"] = {"start": "113/04/30", "end": "113/04/01"}
        self.assertIn("計費期間起訖異常", bill_to_row(bill)["需人工檢查"])

    def test_flags_total_inconsistent_with_pretax_where_the_tax_backs_neither(self):
        row = bill_to_row(high_voltage_bill(total_amount_twd=120000, business_tax_twd=1710))
        self.assertEqual((row["稅前應繳總金額"], row["應繳總金額"]), (761905, 120000))
        self.assertIn("應繳總金額與稅前金額", row["需人工檢查"])

    def test_flags_total_inconsistent_with_pretax_where_no_tax_was_read(self):
        row = bill_to_row(high_voltage_bill(total_amount_twd=120000, business_tax_twd=None))
        self.assertEqual(row["應繳總金額"], 120000)
        self.assertIn("應繳總金額與稅前金額", row["需人工檢查"])

    def test_misread_tax_does_not_flag_consistent_total(self):
        row = bill_to_row(high_voltage_bill(business_tax_twd=1710))
        self.assertIsNone(row["需人工檢查"])








if __name__ == "__main__":
    unittest.main()
