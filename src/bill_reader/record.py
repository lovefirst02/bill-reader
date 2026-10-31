"""A parsed page as one row of columns, with what about it needs checking."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .parser import BillResult

_TOU_COMPONENTS = (
    ("尖峰度數", "peak_kwh"),
    ("半尖峰度數", "semi_peak_kwh"),
    ("週六半尖峰度數", "saturday_semi_peak_kwh"),
    ("離峰度數", "off_peak_kwh"),
)
_ENERGY_GROUPS = (
    ("計費度數", "energy_consumption"),
    ("轉供度數", "transferred_energy"),
)

COLUMNS = (
    "Billing Period 計費期間",
    "Custom No. 電號",
    *(f"{group}_{component}" for group, _ in _ENERGY_GROUPS for component, _ in _TOU_COMPONENTS),
    "基本電費",
    "流動電費",
    "功率因數調整費",
    "預繳費用",
    "其他調整費用",
    "稅前應繳總金額",
    "應繳總金額",
    "來源檔案",
    "頁碼",
    "需人工檢查",
)
REVIEW_COLUMN = "需人工檢查"
_TEXT_COLUMNS = {"Billing Period 計費期間", "Custom No. 電號"}
# Columns holding figures: in a written file, one with no value reads 0.
FIGURE_COLUMNS = tuple(key for key in COLUMNS if key not in {*_TEXT_COLUMNS, "來源檔案", "頁碼", REVIEW_COLUMN})


@dataclass
class Issue:
    """A reason a row needs review, with the columns it concerns."""

    text: str
    columns: list[str] = field(default_factory=list)


@dataclass
class BillRecord:
    row: dict[str, Any]
    issues: list[Issue]


def with_zeros(row: Mapping[str, Any]) -> dict[str, Any]:
    """The row as written to a file: no figure read is 0, and 需人工檢查 says which ones."""
    return {**row, **{key: 0 for key in FIGURE_COLUMNS if row.get(key) in (None, "")}}


def _first(bill: BillResult, *paths: str) -> Any:
    for path in paths:
        value = bill.get(path)
        if value is not None:
            return value
    return None


def _format_period(period: Any) -> str | None:
    if isinstance(period, Mapping):
        start, end = period.get("start"), period.get("end")
        if start and end:
            return f"{start}~{end}"
        return start or end
    return period


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _period_issue(period: Any) -> Issue | None:
    if not isinstance(period, str) or "~" not in period:
        return None
    start, end = period.split("~", 1)
    # ROC dates are zero-padded (113/01/01), so string order is date order.
    if start >= end:
        return Issue(f"計費期間起訖異常（{period}）", ["Billing Period 計費期間"])
    return None


def _total_issue(row: Mapping[str, Any], tax: Any) -> Issue | None:
    """The payable total against the pre-tax total plus tax."""
    pretax, total = row["稅前應繳總金額"], row["應繳總金額"]
    if not (_is_number(pretax) and _is_number(total)) or row["預繳費用"]:
        return None
    # Accept either the printed tax or the 5% rate, since the tax line may be misread
    # while the totals themselves are correct.
    expected = round(pretax * 1.05)
    if abs(total - expected) <= 2:
        return None
    if _is_number(tax) and abs(total - (pretax + tax)) <= 1:
        return None
    return Issue(f"應繳總金額與稅前金額＋營業稅不符（預期約 {expected:,}）", ["應繳總金額", "稅前應繳總金額"])


def _drop_misread_minus(row: dict[str, Any]) -> None:
    """A speck before the total can read as a minus: the pre-tax total says when it was."""
    pretax, total = row["稅前應繳總金額"], row["應繳總金額"]
    if _is_number(pretax) and _is_number(total) and total < 0 < pretax and abs(-total - round(pretax * 1.05)) <= 2:
        row["應繳總金額"] = -total


def is_charge_page(bill: BillResult) -> bool:
    """Whether a page carries the bill's charge breakdown."""
    return isinstance(bill.get("charge_info"), Mapping)


def _dashed_meter(number: str) -> str:
    """One meter, one spelling: some layouts print 00123456789, most 00-12-3456-78-9."""
    match = re.fullmatch(r"(\d{2})(\d{2})(\d{4})(\d{2})(\d)", number)
    return "-".join(match.groups()) if match else number


# Where each column's value is stored, as read and as filled in.
_VALUE_PATHS = {
    "Billing Period 計費期間": "user_info.billing_period",
    "Custom No. 電號": "user_info.customer_number",
    "基本電費": "charge_info.basic_charge_twd",
    "流動電費": "charge_info.energy_charge_twd",
    "功率因數調整費": "charge_info.power_factor_adjustment_twd",
    "預繳費用": "charge_info.prepaid_applied_twd",
    "稅前應繳總金額": "charge_info.pretax_total_twd",
    "應繳總金額": "charge_info.total_amount_twd",
    **{f"{group}_{component}": f"user_info.{group_key}.{component_key}"
       for group, group_key in _ENERGY_GROUPS for component, component_key in _TOU_COMPONENTS},
}


def _bill_values(bill: BillResult) -> dict[str, Any]:
    """Every column read from the bill."""
    values = {column: bill.get(path) for column, path in _VALUE_PATHS.items()}
    values["Billing Period 計費期間"] = _format_period(_first(bill, "user_info.billing_period", "payment_info.billing_period"))
    customer_number = _first(bill, "user_info.customer_number", "payment_info.customer_number")
    values["Custom No. 電號"] = _dashed_meter(str(customer_number)) if customer_number else None
    for group, group_key in _ENERGY_GROUPS:
        for component, component_key in _TOU_COMPONENTS:
            # The table layout keeps the kWh groups in its own block.
            values[f"{group}_{component}"] = _first(bill, f"user_info.{group_key}.{component_key}", f"energy_usage.{group_key}.{component_key}")
    return values


def _store(bill: BillResult, path: str, value: Any) -> None:
    *parents, last = path.split(".")
    node = bill
    for key in parents:
        node = node.setdefault(key, {})
    node[last] = value


def fill_missing(bill: BillResult, other: BillResult) -> list[str]:
    """What ``bill`` lacks, from another reading of the page: the columns filled. Its own values stay."""
    ours, theirs = _bill_values(bill), _bill_values(other)
    filled = [column for column, value in theirs.items() if value is not None and ours[column] is None]
    for column in filled:
        _store(bill, _VALUE_PATHS[column], theirs[column])
    tax = other.get("charge_info.business_tax_twd")
    if tax is not None and bill.get("charge_info.business_tax_twd") is None:
        _store(bill, "charge_info.business_tax_twd", tax)
    return filled


def bill_to_record(bill: BillResult, source: str | None = None) -> BillRecord:
    """One page as the columns, plus what needs checking.

    A value not read stays ``None`` here; only a written file shows it as 0.
    """
    issues: list[Issue] = []
    row: dict[str, Any] = dict.fromkeys(COLUMNS)
    row.update(_bill_values(bill))
    tax = bill.get("charge_info.business_tax_twd")

    if period_issue := _period_issue(row["Billing Period 計費期間"]):
        issues.append(period_issue)

    _drop_misread_minus(row)
    missing = recompute_other_adjustment(row)
    if missing:
        issues.append(Issue(f"其他調整費用無法計算：缺少{'、'.join(missing)}", [*missing, "其他調整費用"]))
    elif not _is_number(row["功率因數調整費"]):
        issues.append(Issue("未辨識功率因數調整費，其他調整費用以 0 計算", ["功率因數調整費"]))

    if total_issue := _total_issue(row, tax):
        issues.append(total_issue)

    for label in ("Billing Period 計費期間", "Custom No. 電號", "應繳總金額", "稅前應繳總金額"):
        # A bill its prepayment paid in full prints a total of 0 and no pre-tax total.
        printed_none = label == "稅前應繳總金額" and row["應繳總金額"] == 0
        if row[label] is None and not printed_none:
            issues.append(Issue(f"缺少{label}", [label]))

    # A file shows these as 0, so a bill charged for energy with none read must say so.
    billed = [f"計費度數_{label}" for label, _ in _TOU_COMPONENTS]
    if row["流動電費"] and all(row[key] is None for key in billed):
        issues.append(Issue("缺少計費度數", billed))

    # A time-of-use bill always prints its billed peak row, even at 0; its transfers
    # leave the peak row out in months that have a semi-peak row instead.
    for group, _ in _ENERGY_GROUPS:
        peak = f"{group}_尖峰度數"
        replaced = group == "轉供度數" and row[f"{group}_半尖峰度數"] is not None
        if row[peak] is None and row[f"{group}_離峰度數"] is not None and not replaced:
            issues.append(Issue(f"缺少{peak}", [peak]))

    # Parser warnings describe inferred values.
    issues.extend(Issue(warning) for warning in bill.get("quality.warnings") or [])
    row["來源檔案"] = source
    row["頁碼"] = bill.get("quality.page_number")
    row[REVIEW_COLUMN] = "；".join(issue.text for issue in issues) or None
    return BillRecord(row, issues)


# The charge lines are printed tax-inclusive and add up to 應繳總金額, so the
# remainder after the itemised columns is everything else under 計費內容.
OTHER_ADJUSTMENT_REQUIRED = ("應繳總金額", "基本電費", "流動電費")
OTHER_ADJUSTMENT_INPUTS = (*OTHER_ADJUSTMENT_REQUIRED, "功率因數調整費", "預繳費用")


def recompute_other_adjustment(row: dict[str, Any]) -> list[str]:
    """Set 其他調整費用 from the row; returns the required columns that are missing.

    A missing power factor adjustment or prepayment counts as 0, since many
    bills do not print those lines.
    """
    missing = [key for key in OTHER_ADJUSTMENT_REQUIRED if not _is_number(row.get(key))]
    if missing:
        row["其他調整費用"] = None
        return missing
    total, basic, energy, power_factor, prepaid = (
        row.get(key) if _is_number(row.get(key)) else 0 for key in OTHER_ADJUSTMENT_INPUTS
    )
    row["其他調整費用"] = round(total - basic - energy - power_factor - prepaid, 2)
    return []
