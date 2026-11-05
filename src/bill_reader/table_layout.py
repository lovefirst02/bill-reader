"""The e-bill site's table layout: 基本資料 and 計費內容 above, 用電資料 and 電表資料 below."""

from __future__ import annotations

import re
from typing import Any

from .lines import BillResult, OcrLine, _compact, _number, _parse_energy_number, _right_hand_energy_value


def is_table_layout(lines: list[OcrLine]) -> bool:
    compact_lines = [_compact(line.text) for line in lines]
    has_required_top = any(text.startswith("基本資料") for text in compact_lines) and any(text.startswith("計費內容") for text in compact_lines)
    has_usage_title = any(text.startswith("用電資料") for text in compact_lines)
    has_meter_title = any(text.startswith("電表資料") for text in compact_lines)
    has_usage_groups = any(text.startswith("計費度數") for text in compact_lines) and any(text.startswith("轉供度數") for text in compact_lines)
    return has_required_top and has_usage_title and (has_meter_title or has_usage_groups)


def _closest_right_line(
    label: OcrLine,
    lines: list[OcrLine],
    *,
    x_max: float = float("inf"),
) -> OcrLine | None:
    label_center_y = label.y + label.height / 2
    tolerance = max(8.0, label.height * 0.7)
    candidates = [
        line
        for line in lines
        if line is not label
        and label.x < line.x < x_max
        and abs((line.y + line.height / 2) - label_center_y) <= tolerance
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda line: (
            abs((line.y + line.height / 2) - label_center_y),
            max(0.0, line.x - (label.x + label.width)),
        ),
    )


def _detailed_amount(text: str) -> int | float | None:
    match = re.search(r"[+-]?\d[\d,.]*", re.sub(r"\s+", "", text))
    if match is None:
        return None
    token = match.group(0)
    if re.match(r"^[+-]?\d\.\d{3},", token):
        token = token.replace(".", ",", 1)
    try:
        return _number(token)
    except ValueError:
        return None


def _roc_date_from_gregorian(value: str) -> str:
    year = int(value[:4]) - 1911
    return f"{year:03d}/{value[4:6]}/{value[6:8]}"


def parse_energy_table(usage_lines: list[OcrLine]) -> dict[str, Any]:
    """The kWh rows of the lower table, each under the group its height falls in."""
    def find_label(alias: str) -> OcrLine | None:
        return next((line for line in usage_lines if _compact(line.text).startswith(_compact(alias))), None)

    billed_group = find_label("計費度數")
    transferred_group = find_label("轉供度數")
    if not (billed_group and transferred_group):
        return {}
    fields: dict[str, Any] = {}
    billed_center = billed_group.y + billed_group.height / 2
    transferred_center = transferred_group.y + transferred_group.height / 2
    # Group captions are vertically centred across multiple rows.  Bias the
    # midpoint slightly toward the earlier group so a boundary-adjacent row
    # becomes the first row of 轉供度數 instead of the last 計費度數 row.
    transfer_start = (billed_center + transferred_center) / 2 - min(billed_group.height, transferred_group.height) / 4
    following_aliases = ("經常尖峰契約", "經常尖峰需量", "半尖峰非夏月需量", "離峰需量", "功率因數", "調整係數", "前期轉供度數")
    current_usage_end = min(
        (line.y for line in usage_lines if any(_compact(line.text).startswith(_compact(alias)) for alias in following_aliases)),
        default=float("inf"),
    )
    component_aliases = (
        ("saturday_semi_peak_kwh", ("週六半尖峰度數", "调六半尖峰度數", "遇六半尖峰度數")),
        ("semi_peak_kwh", ("半尖峰度數",)),
        ("off_peak_kwh", ("離峰度數",)),
        # Some bills print the peak row as 經常(尖峰)度數.
        ("peak_kwh", ("經常尖峰度數", "尖峰度數")),
    )
    for line in sorted(usage_lines, key=lambda item: (item.y, item.x)):
        if line.y >= current_usage_end:
            continue
        compact = _compact(line.text)
        matched = next(
            ((field_key, alias) for field_key, aliases in component_aliases for alias in aliases if compact.startswith(_compact(alias))),
            None,
        )
        if matched is None:
            continue
        field_key, alias = matched
        inline_tail = compact[len(_compact(alias)):]
        value = _parse_energy_number(inline_tail) if inline_tail else None
        if value is None:
            value, _ = _right_hand_energy_value(line, usage_lines, set())
        if value is None:
            continue
        group_key = "energy_consumption" if line.y + line.height / 2 < transfer_start else "transferred_energy"
        fields.setdefault(group_key, {}).setdefault(field_key, value)
    return fields


_CHARGES = {
    "basic_charge_twd": ("基本電費",),
    "energy_charge_twd": ("流動電費",),
    "power_factor_adjustment_twd": ("功率因數調整費",),
    "pretax_total_twd": ("稅前應繳總金額",),
    "business_tax_twd": ("營業稅",),
    "total_amount_twd": ("應繳總金額", "繳費總金額"),
}


def parse_table_layout(lines: list[OcrLine], prepaid_tail) -> BillResult:
    """The page's columns; ``prepaid_tail`` tells the applied prepayment from a balance."""
    top = next(line for line in lines if _compact(line.text).startswith("基本資料"))
    charges = next(line for line in lines if _compact(line.text).startswith("計費內容"))
    energy_header = next(line for line in lines if _compact(line.text).startswith("用電資料"))
    meter_header = next((line for line in lines if _compact(line.text).startswith("電表資料")), None)
    column_split = (top.center_x + charges.center_x) / 2
    lower_top = energy_header.y if meter_header is None else min(energy_header.y, meter_header.y)

    user_lines = [line for line in lines if line.y < lower_top and line.center_x < column_split]
    charge_lines = [line for line in lines if line.y < lower_top and line.center_x >= column_split]
    usage_lines = [line for line in lines if line.y >= lower_top and line.center_x < column_split]

    def user_label(alias: str) -> OcrLine | None:
        return next((line for line in user_lines if _compact(line.text).startswith(_compact(alias))), None)

    user_fields: dict[str, Any] = {}
    meter_label = user_label("電號")
    if meter_label is not None:
        parts = re.split(r"[:：]", meter_label.text, maxsplit=1)
        if len(parts) == 2 and parts[1].strip():
            text = parts[1].strip()
        else:
            value_line = _closest_right_line(meter_label, user_lines, x_max=column_split)
            text = value_line.text.strip() if value_line is not None else None
        if text and (match := re.search(r"(?:\d{2}-\d{2}-\d{4}-\d{2}-\d|\d{11})", re.sub(r"\s+", "", text))):
            user_fields["customer_number"] = match.group(0)
        if "customer_number" not in user_fields:
            # A tall cell prints the number at its top, above the label's row.
            for line in user_lines:
                if abs(line.y - meter_label.y) <= meter_label.height * 1.5 and re.fullmatch(r"\d{11}", _compact(line.text)):
                    user_fields["customer_number"] = _compact(line.text)
                    break

    period_line = next((line for line in lines if "billingperiod" in _compact(line.text)), None)
    if period_line is not None and (match := re.search(r"(\d{8})\s*-\s*(\d{8})", period_line.text)):
        user_fields["billing_period"] = {"start": _roc_date_from_gregorian(match.group(1)), "end": _roc_date_from_gregorian(match.group(2))}
    period_label = user_label("計費期間")
    if "billing_period" not in user_fields and period_label is not None:
        text = "".join(
            line.text.strip()
            for line in sorted(user_lines, key=lambda item: (item.y, item.x))
            if line is not period_label
            and line.x > period_label.x + period_label.width
            and period_label.y - 15 <= line.y <= period_label.y + 25
        )
        dates = re.findall(r"(\d{2,3})年(\d{1,2})月(\d{1,2})日", text)
        if len(dates) >= 2:
            user_fields["billing_period"] = {
                "start": "/".join((dates[0][0], dates[0][1].zfill(2), dates[0][2].zfill(2))),
                "end": "/".join((dates[1][0], dates[1][1].zfill(2), dates[1][2].zfill(2))),
            }

    charge_fields: dict[str, Any] = {}
    prepaid = next((line for line in charge_lines if prepaid_tail(line.text) is not None), None)
    if prepaid is not None:
        tail = prepaid_tail(prepaid.text)
        value = _detailed_amount(tail) if tail else None
        if value is None:
            value_line = _closest_right_line(prepaid, charge_lines)
            value = _detailed_amount(value_line.text) if value_line else None
        if value is not None:
            charge_fields["prepaid_applied_twd"] = value
    for field_key, aliases in _CHARGES.items():
        label = next((line for line in charge_lines if any(_compact(line.text).startswith(_compact(alias)) for alias in aliases)), None)
        if label is None:
            continue
        inline_tail = label.text.split("：", 1)[1] if "：" in label.text else ""
        if field_key == "total_amount_twd":
            compact_label = _compact(label.text)
            alias = next(alias for alias in aliases if compact_label.startswith(_compact(alias)))
            inline_tail = compact_label[len(_compact(alias)):]
        value = _detailed_amount(inline_tail) if inline_tail else None
        if value is None:
            value_line = _closest_right_line(label, charge_lines)
            value = _detailed_amount(value_line.text) if value_line else None
        if value is not None:
            charge_fields[field_key] = value

    return BillResult({
        "user_info": user_fields,
        "charge_info": charge_fields,
        "energy_usage": parse_energy_table(usage_lines),
        "quality": {"warnings": []},
    })
