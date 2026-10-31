"""Lines of text to a bill's fields: repair what reading broke, then pair labels with values."""

from __future__ import annotations

import re
from typing import Any, Iterable

from .lines import BillResult, OcrLine, _compact, _number, _parse_energy_number, _right_hand_energy_value
from .table_layout import is_table_layout, parse_energy_table, parse_table_layout

_BLOCK_TITLES = {
    "user_info": re.compile(r"用戶資訊|basicinfo", re.IGNORECASE),
    "charge_info": re.compile(r"計費內容|chargeinfo", re.IGNORECASE),
    "payment_info": re.compile(r"繳費資訊|paymentinfo", re.IGNORECASE),
    "other_info": re.compile(r"其他資訊|otherinfo", re.IGNORECASE),
    "invoice_info": re.compile(r"前期發票資訊|invoice", re.IGNORECASE),
    "carrier_info": re.compile(r"本期載具號碼|carrier", re.IGNORECASE),
    "receipt_stamp": re.compile(r"收款章|receiptstamp", re.IGNORECASE),
    "charge_introduction": re.compile(r"電費計收說明|chargeintroduction", re.IGNORECASE),
    "energy_saving_info": re.compile(r"節電資訊|energysavinginfo", re.IGNORECASE),
    "energy_usage": re.compile(r"用電資料|energyusage", re.IGNORECASE),
    "meter_info": re.compile(r"電表(?:資訊|資料)|meterinfo", re.IGNORECASE),
}

_INLINE_CHARGES = {
    "energy_charge_twd": re.compile(r"流動電費\s*[:：]?\s*([+-]?[\d,.]+)\s*元?"),
    "total_amount_twd": re.compile(r"^\s*(?:應繳總金額|繳費總金額)\s*[:：]?\s*([+-]?[\d,.]+)\s*元?"),
}

_ADJUSTMENT_LABELS = {
    "current_prepaid_balance_twd": ("本期預繳餘額",),
    "previous_prepaid_balance_twd": ("上期預繳餘額",),
    "electricity_price_adjustment_twd": ("電價調整金額",),
    "supplemental_charge_refund_twd": ("補收(退)金額(含停電扣減)", "補收(退)金額"),
}

# What is read from each block, with the wordings bills and misreadings use.
_FIELDS: dict[str, dict[str, tuple[str, ...]]] = {
    "user_info": {"power_factor_percent": ("功率因數",)},
    "charge_info": {
        "basic_charge_twd": ("基本電費約定", "基本電費"),
        "prepaid_applied_twd": ("本期預繳金額", "本期預繳"),
        "energy_charge_twd": ("流動電費", "流動費"),
        "power_factor_adjustment_twd": ("功率因數調整費",),
        "pretax_total_twd": ("稅前應繳總金額",),
        "business_tax_twd": ("營業稅",),
        "total_amount_twd": ("應繳總金額", "繳費總金額", "應繳總金", "總金額", "蟌金額"),
    },
}


def _adjustment_tail(text: str, field: str) -> str | None:
    """Match a complete adjustment label, excluding unrelated suffixes."""
    compact = re.sub(r"[\s()（）]", "", text)
    aliases = "|".join(re.escape(re.sub(r"[()（）]", "", alias))
                       for alias in _ADJUSTMENT_LABELS[field])
    match = re.fullmatch(r"(?:" + aliases + r")[:：]?([+-]?\d[\d,]*(?:\.\d+)?元?)?", compact)
    return (match.group(1) or "") if match else None


def _prepaid_tail(text: str) -> str | None:
    """Match the current applied payment, never a balance or historical label."""
    compact = re.sub(r"\s+", "", text)
    match = re.fullmatch(
        r"(?:本期預繳金額|本期預繳)[:：]?([+-]?\d[\d,]*(?:\.\d+)?元?)?",
        compact,
    )
    return (match.group(1) or "") if match else None


def _parse_value(text: str) -> int | float | None:
    compact_number = re.sub(r"\s+", "", text).replace("。", ".")
    match = re.search(r"[+-]?\d[\d,]*(?:[.·]\d+)?", compact_number)
    return _number(match.group(0)) if match else None


def _right_hand_value(label: OcrLine, lines: list[OcrLine]) -> Any | None:
    same_row = [
        line
        for line in lines
        if line is not label
        and line.x > label.x
        and abs(line.y - label.y) <= max(6, label.height * 0.55)
    ]

    def candidate_rank(line: OcrLine) -> tuple[int, int, int]:
        distance_band = round((line.x - label.x) / 5)
        has_explicit_decimal = any(mark in line.text for mark in (".", "·", "。"))
        is_explicitly_negative = re.sub(r"\s+", "", line.text).startswith("-")
        return (distance_band, 0 if is_explicitly_negative else 1, 0 if has_explicit_decimal else 1)

    for candidate in sorted(same_row, key=candidate_rank):
        value = _parse_value(candidate.text)
        if value is not None:
            return value
    return None


def _dedupe_nearby_ocr_lines(lines: list[OcrLine]) -> list[OcrLine]:
    """Collapse duplicate OCR passes without changing the preserved raw lines."""
    unique: list[OcrLine] = []
    for line in sorted(lines, key=lambda item: (item.y, item.x)):
        center_y = line.y + line.height / 2
        center_x = line.x + line.width / 2
        if any(
            _compact(existing.text) == _compact(line.text)
            and abs((existing.y + existing.height / 2) - center_y) <= 4
            and abs((existing.x + existing.width / 2) - center_x) <= 4
            for existing in unique
        ):
            continue
        unique.append(line)
    return unique


def _energy_groups(
    lines: list[OcrLine],
    value_candidates: list[OcrLine] | None = None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Parse repeated energy labels only within their printed group heading."""
    group_labels = {
        "energy_consumption": ("計費度數", "计费度数", "十費度數"),
        # OCR sometimes drops 轉 or reads it as 專. Keep the printed heading
        # as a boundary even when the first billed component is unreadable.
        "transferred_energy": ("轉供度數", "转供度数", "專供度數", "供度數"),
    }
    headers = [
        (group_key, line)
        for group_key, aliases in group_labels.items()
        for line in lines
        if any(
            _compact(line.text).startswith(_compact(alias))
            for alias in aliases
        )
    ]
    component_specs = (
        (
            "saturday_semi_peak_kwh",
            (
                "週六半尖峰度數",
                "週六半尖蜂度數",
                "週六半尖峰度数",
                "周六半尖峰度数",
                "周六半尖蜂度数",
            ),
        ),
        ("semi_peak_kwh", ("半尖峰度數", "半尖峰度数", "半尖蜂度数")),
        ("off_peak_kwh", ("離峰度數", "離峰度数", "离峰度数")),
        (
            "peak_kwh",
            (
                "經常尖峰度數",
                "經常尖蜂度數",
                "经常尖峰度数",
                "经常尖蜂度数",
                "尖峰度數",
                "尖蜂度數",
                "尖峰度数",
                "尖蜂度数",
            ),
        ),
        ("discount_kwh", ("優惠度數", "優惠度数", "优惠度数")),
    )
    warnings: list[str] = []
    header_keys = {group_key for group_key, _ in headers}
    if (
        "energy_consumption" in header_keys
        and "transferred_energy" not in header_keys
    ):
        peak_aliases = next(
            aliases
            for field_key, aliases in component_specs
            if field_key == "peak_kwh"
        )
        billed_header_y = min(
            line.y
            for group_key, line in headers
            if group_key == "energy_consumption"
        )
        peak_rows = sorted(
            (
                line
                for line in lines
                if line.y > billed_header_y
                and any(
                    _compact(line.text).startswith(_compact(alias))
                    for alias in peak_aliases
                )
            ),
            key=lambda line: (line.y, line.x),
        )
        if len(peak_rows) >= 2:
            second_peak = peak_rows[1]
            inferred_y = second_peak.y - max(1.0, second_peak.height * 0.25)
            headers.append(
                (
                    "transferred_energy",
                    OcrLine(
                        "轉供度數（推定）",
                        second_peak.x,
                        inferred_y,
                        second_peak.width,
                        1),
                )
            )
            warnings.append("transferred_energy_heading_inferred")

    grouped: dict[str, dict[str, Any]] = {}
    for group_key, header in headers:
        following_headers = [
            candidate.y for _, candidate in headers if candidate.y > header.y
        ]
        bottom = min(following_headers, default=float("inf"))
        section = [line for line in lines if header.y < line.y < bottom]
        value_section = _dedupe_nearby_ocr_lines(
            [
                line
                for line in (value_candidates or lines)
                if header.y < line.y < bottom
            ]
        )
        values: dict[str, Any] = {}
        used_candidate_ids: set[int] = set()
        component_rows: list[tuple[OcrLine, str, str]] = []
        for line in section:
            compact_text = _compact(line.text)
            for field_key, aliases in component_specs:
                matching_alias = next(
                    (
                        alias
                        for alias in aliases
                        if compact_text.startswith(_compact(alias))
                    ),
                    None,
                )
                if matching_alias is None:
                    continue
                component_rows.append((line, field_key, matching_alias))
                break
        for line, field_key, matching_alias in sorted(
            component_rows, key=lambda item: (item[0].y, item[0].x)
        ):
            if field_key in values:
                continue
            compact_text = _compact(line.text)
            alias_text = _compact(matching_alias)
            inline_tail = compact_text[len(alias_text):]
            value = (
                _parse_energy_number(inline_tail)
                if inline_tail and "%" not in line.text and "％" not in line.text
                else None
            )
            candidate = None
            if value is None:
                value, candidate = _right_hand_energy_value(
                    line,
                    value_section,
                    used_candidate_ids,
                )
            if value is not None:
                values[field_key] = value
                if candidate is not None:
                    used_candidate_ids.add(id(candidate))
        if values:
            grouped[group_key] = values
    return grouped, warnings


def _row_text(label: OcrLine, lines: list[OcrLine]) -> str:
    same_row = [
        line
        for line in lines
        if line is not label
        and line.x > label.x
        and abs(line.y - label.y) <= max(6, label.height * 0.55)
    ]
    return " ".join([label.text, *(line.text for line in sorted(same_row, key=lambda item: item.x))])


def _dates(text: str) -> list[str]:
    compact = re.sub(r"\s+", "", text)
    return re.findall(r"\d{2,3}/\d{1,2}/\d{1,2}", compact)


def _normalized_date(text: str) -> str | None:
    match = re.search(
        r"(?<!\d)(\d{2,3})\s*(?:[/.-]|\s)\s*(\d{1,2})"
        r"\s*(?:[/.-]|\s)\s*(\d{1,2})(?!\d)",
        text,
    )
    if match is None:
        return None
    return "/".join(match.groups())


def _looks_like_unknown_header(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return bool(re.search(r"(?:info(?:rmation)?|資訊)\.?$", compact, re.IGNORECASE))


# Other printed wordings of the kWh rows and their headings.
_LABEL_WORDINGS = (
    (re.compile(r"^(經常尖峰|週六半尖峰|半尖峰|尖峰|離峰|經常)用電度數"), r"\1度數"),
    (re.compile(r"^(?:本期|本月|當期)?轉供(?:電量|電度|度數)"), "轉供度數"),
    (re.compile(r"^(?:本期|本月|當期)?(?:用電度數|計費電度)(?=[(（]度[)）]|/?Energy)"), "計費度數"),
)


_METER = re.compile(r"(?<!\d)\d{2}-\d{2}-\d{4}-\d{2}-\d(?!\d)")


_ROC_DATE = r"(\d{2,3})[/.年](\d{1,2})[/.月](\d{1,2})日?"


_PERIOD = re.compile(_ROC_DATE + r"[至~～\-－]" + _ROC_DATE)


_PERIOD_START = re.compile(_ROC_DATE + r"[至~～\-－]$")


_PERIOD_END = re.compile(r"^" + _ROC_DATE + r"$")


_WRAPPED_START = re.compile(_ROC_DATE + r"(?:[至~～\-－]\d{0,3}年?\d{0,2}月?)?$")


_WRAPPED_REST = re.compile(r"[至~～\-－]?\d{0,3}")


_DATE_PIECE = re.compile(r"[\d年月日]+")


# Charges print one decimal and rates four: three after a point is a thousands
# comma misread (2.068.925, or 2068.925 with one of them lost).
_POINT_FOR_COMMA = re.compile(r"^(\*{0,6})(\d{1,3}(?:\.\d{3}){2,}|\d{4,}\.\d{3})(元?)$")


def _canonical_label(line: OcrLine) -> OcrLine:
    text = re.sub(r"\s+", "", line.text)
    # The English half of the heading survives what garbles its Chinese.
    if re.search(r"Energy\s*Consum", text, re.IGNORECASE) and not text.startswith("計費度數"):
        return OcrLine("計費度數(度)/EnergyConsumption(kWh)", line.x, line.y, line.width, line.height)
    figure = _POINT_FOR_COMMA.match(text)
    if figure:
        return OcrLine(figure[1] + figure[2].replace(".", ",") + figure[3], line.x, line.y, line.width, line.height)
    for pattern, wording in _LABEL_WORDINGS:
        replaced, count = pattern.subn(wording, text)
        if count:
            return OcrLine(replaced, line.x, line.y, line.width, line.height)
    return line


def _long_enough(start: tuple[str, ...], end: tuple[str, ...]) -> bool:
    """A billing period runs weeks; two dates days apart are a reading and a charge date."""
    days = lambda groups: int(groups[0]) * 365 + int(groups[1]) * 30 + int(groups[2])
    return 20 <= days(end) - days(start) <= 100


def _on_reading_row(line: OcrLine, lines: list[OcrLine]) -> bool:
    """Beside 本次抄表日 or 下次抄表日: a date here is never the end of a period."""
    return any("抄表日" in re.sub(r"\s+", "", other.text) and abs(other.y - line.y) < line.height / 2 for other in lines)


def _join_wrapped_period(lines: list[OcrLine]) -> list[OcrLine]:
    """A narrow cell breaks a period anywhere: 115年07月01日至11 above 5年07月31日."""
    flat = lambda line: re.sub(r"\s+", "", line.text)
    for line in lines:
        if not _WRAPPED_START.search(flat(line)):
            continue
        rest = sorted((other for other in lines if other is not line and other.x > line.x
                       and abs(other.y - line.y) < line.height / 2 and _WRAPPED_REST.fullmatch(flat(other))),
                      key=lambda other: other.x)
        below = sorted((other for other in lines if 0 < other.y - line.y < line.height * 2.5
                        and abs(other.x - line.x) < line.height * 2), key=lambda other: other.y)
        if below and _on_reading_row(below[0], lines):
            continue
        end = below[:1]
        # Figures and words in two fonts sit at two heights: 5, 07, 31 and a little lower 年, 月, 日.
        if below and _DATE_PIECE.fullmatch(flat(below[0])):
            end = sorted((other for other in lines if _DATE_PIECE.fullmatch(flat(other))
                          and 0 <= other.y - below[0].y < below[0].height * 0.8
                          and line.x - line.height * 2 < other.x < line.x + line.width), key=lambda other: other.x)
        attempts = [([line, *rest, *end], None), ([line, *end], None)]
        # The "11" of the end's year can go unread at the break: the start says what it was.
        head = re.sub(r"\d{1,3}$", "", "".join(flat(part) for part in (line, *rest)))
        start, tail = _PERIOD_START.search(head), re.match(r"\d{0,3}年(.*)", "".join(flat(part) for part in end))
        if start and tail:
            attempts.append(([line, *rest, *end], f"{head}{start.group(1)}年{tail.group(1)}"))
        for parts, spelled in attempts:
            joined = spelled or "".join(flat(part) for part in parts)
            match = _PERIOD.search(joined)
            if match and _long_enough(match.groups()[:3], match.groups()[3:]):
                whole = OcrLine(joined, line.x, line.y, line.width, line.height)
                return [whole if item is line else item for item in lines if not any(item is part for part in parts[1:])]
    return lines


def _fill_from_page(result: BillResult, lines: list[OcrLine]) -> None:
    """A 電號 or period its label could not find (a heading cut off, misread): by its look.

    A 電號's pattern is found nowhere else on a bill, and the other date on a
    bill never comes as a range.
    """
    def has(key: str) -> bool:
        return any(isinstance(result.get(block), dict) and result[block].get(key) for block in ("user_info", "payment_info"))

    def store(key: str, value: Any) -> None:
        result.setdefault("user_info", {})[key] = value

    flat = [(re.sub(r"\s+", "", line.text), line) for line in lines]
    if not has("customer_number"):
        meters = {match.group(0) for text, _ in flat for match in _METER.finditer(text)}
        if len(meters) == 1:
            store("customer_number", meters.pop())
    if has("billing_period"):
        return
    date = lambda groups: f"{groups[0]}/{groups[1].zfill(2)}/{groups[2].zfill(2)}"

    for text, _ in flat:
        match = _PERIOD.search(text)
        if match and _long_enough(match.groups()[:3], match.groups()[3:]):
            store("billing_period", {"start": date(match.groups()[:3]), "end": date(match.groups()[3:])})
            return
    # Written on two lines: 109/12/01~ above 109/12/31.
    for text, line in flat:
        start = _PERIOD_START.search(text)
        if not start:
            continue
        for other_text, other in flat:
            end = _PERIOD_END.match(other_text)
            if end and 0 < other.y - line.y < line.height * 2.5 and abs(other.x - line.x) < line.width \
                    and _long_enough(start.groups(), end.groups()) and not _on_reading_row(other, lines):
                store("billing_period", {"start": date(start.groups()), "end": date(end.groups())})
                return


_KWH_ROWS = ("尖峰度數", "半尖峰度數", "週六半尖峰度數", "離峰度數")


def _restore_kwh_headings(lines: list[OcrLine]) -> list[OcrLine]:
    """The kWh rows of a heading OCR missed still say what they are.

    Bills print 計費度數 and then 轉供度數, each with the same rows, so the second
    time a row is printed it starts 轉供度數. Without any row seen twice, or with
    a heading in the way, nothing is added.
    """
    has_billed = any(re.sub(r"\s+", "", line.text).startswith("計費度數") for line in lines)
    has_transfer = any(re.sub(r"\s+", "", line.text).startswith("轉供度數") for line in lines)
    if has_billed and has_transfer:
        return lines
    rows = sorted((line for line in lines if re.sub(r"\s+", "", line.text) in _KWH_ROWS), key=lambda line: line.y)
    if not rows:
        return lines
    seen: set[str] = set()
    second = None
    for line in rows:
        text = re.sub(r"\s+", "", line.text)
        if text in seen:
            second = line
            break
        seen.add(text)
    heading = lambda text, row: OcrLine(text, row.x, row.y - row.height * 1.1, row.width, row.height)
    added = []
    first = rows[0]
    if not has_billed and not (has_transfer and second is None):
        added.append(heading("計費度數(度)", first))
    if not has_transfer and second is not None:
        added.append(heading("轉供度數(度)", second))
    return [*lines, *added]


def parse_bill_layout(lines: Iterable[OcrLine]) -> BillResult:
    ordered = _restore_kwh_headings(_join_wrapped_period([_canonical_label(line) for line in lines]))
    result = _parse_layout(ordered)
    _fill_from_page(result, ordered)
    return result


def _find_headers(ordered: list[OcrLine]) -> list[tuple[str, OcrLine]]:
    """Every block heading on the page; an unknown one still ends the block above it."""
    headers: list[tuple[str, OcrLine]] = []
    for line in ordered:
        compact_text = _compact(line.text)
        for block_key, title_pattern in _BLOCK_TITLES.items():
            if len(compact_text) <= 30 and title_pattern.search(compact_text):
                headers.append((block_key, line))
                break
        else:
            if _looks_like_unknown_header(line.text):
                headers.append((f"unknown_{sum(key.startswith('unknown_') for key, _ in headers) + 1}", line))
    return headers


def _share_out(ordered: list[OcrLine], headers: list[tuple[str, OcrLine]]) -> dict[str, list[OcrLine]]:
    """Each line under the heading above it that it sits below, or else the nearest."""
    header_ids = {id(header) for _, header in headers}
    assigned: dict[str, list[OcrLine]] = {key: [] for key, _ in headers}
    for line in ordered:
        if id(line) in header_ids:
            continue
        candidates = [
            (abs(line.center_x - header.center_x) + 0.25 * (line.y - header.y), key, header)
            for key, header in headers
            if line.y > header.y
        ]
        if not candidates:
            continue
        overlapping = [item for item in candidates if item[2].x <= line.center_x <= item[2].x + item[2].width]
        if overlapping:
            _, block_key, _ = max(overlapping, key=lambda item: item[2].y)
        else:
            _, block_key, _ = min(candidates, key=lambda item: item[0])
        assigned[block_key].append(line)
    return assigned


def _read_labelled(fields: dict[str, Any], field_key: str, aliases: tuple[str, ...], scoped: list[OcrLine], ordered: list[OcrLine]) -> None:
    """The first of these lines that holds the label, with the value after it or to its right."""
    for line in scoped:
        if field_key == "prepaid_applied_twd" and _prepaid_tail(line.text) is None:
            continue
        compact_text = _compact(line.text)
        alias = next((alias for alias in aliases if _compact(alias) in compact_text), None)
        if alias is None:
            continue
        if field_key == "total_amount_twd" and not compact_text.startswith(_compact(alias)):
            continue
        inline_tail = compact_text[compact_text.find(_compact(alias)) + len(_compact(alias)):]
        value = _parse_value(inline_tail) if inline_tail else None
        if value is None:
            value = _right_hand_value(line, ordered)
        if value is not None:
            fields[field_key] = value
            break


def _period_of(user_lines: list[OcrLine], ordered: list[OcrLine]) -> dict[str, str] | None:
    source = next((line for line in user_lines if any(_compact(label) in _compact(line.text) for label in ("計費期間", "費期間"))), None)
    if source is None:
        return None
    values = _dates(_row_text(source, ordered))
    if len(values) < 2:
        values = []
        # The rows under the period hold other dates: a reading date is not its end.
        readings = [line.y for line in user_lines if "抄表日" in _compact(line.text) and line.y > source.y]
        for line in sorted(user_lines, key=lambda item: (item.y, item.x)):
            if readings and line.y >= min(readings) - source.height / 2:
                break
            if not (
                source.y < line.y <= source.y + max(100, source.height * 5)
                and line.x >= source.x - source.width * 0.2
                and line.x <= source.x + source.width * 1.5
            ):
                continue
            value = _normalized_date(line.text)
            if value and value not in values:
                values.append(value)
    return {"start": values[0], "end": values[1]} if len(values) >= 2 else None


def _repair_charges(charges: dict[str, Any], user: dict[str, Any], charge_lines: list[OcrLine], ordered: list[OcrLine], warnings: list[str]) -> None:
    """What the bill's own arithmetic says about values misread or not read."""
    basic_charge, energy_charge = charges.get("basic_charge_twd"), charges.get("energy_charge_twd")
    if isinstance(basic_charge, (int, float)) and isinstance(energy_charge, (int, float)) and basic_charge < 1000:
        standalone = [_parse_value(line.text) for line in charge_lines if re.fullmatch(r"\s*[+-]?\d[\d,]*(?:[.·。]\d+)?\s*", line.text)]
        plausible = [value for value in standalone if isinstance(value, (int, float)) and 1000 <= value < energy_charge]
        if plausible:
            charges["basic_charge_twd"] = plausible[0]
            warnings.append("basic_charge_recovered_from_scan")

    consumption = user.get("energy_consumption", {})
    if isinstance(consumption, dict):
        components = [consumption.get(key) for key in ("semi_peak_kwh", "saturday_semi_peak_kwh", "off_peak_kwh")]
        discount_kwh = consumption.get("discount_kwh")
        if (
            all(isinstance(value, (int, float)) for value in components)
            and isinstance(discount_kwh, (int, float))
            and abs(discount_kwh - sum(components)) < 0.01
            and consumption.get("peak_kwh") != 0
        ):
            consumption["peak_kwh"] = 0
            warnings.append("peak_kwh_recovered_from_period_sum")

    power_factor, adjustment = user.get("power_factor_percent"), charges.get("power_factor_adjustment_twd")
    if isinstance(power_factor, (int, float)) and power_factor > 80 and isinstance(adjustment, (int, float)) and adjustment > 0:
        charges["power_factor_adjustment_twd"] = -adjustment
        warnings.append("power_factor_adjustment_sign_recovered")

    total = charges.get("total_amount_twd")
    has_adjustments = any(
        _prepaid_tail(line.text) is not None or any(_adjustment_tail(line.text, key) is not None for key in _ADJUSTMENT_LABELS)
        for line in charge_lines
    )
    if total is None and not has_adjustments:
        amounts = [_parse_value(line.text) for line in ordered if "元" in line.text and re.search(r"\d\s*(?:[,，]\s*\d{3})+", line.text)]
        plausible = [value for value in amounts if isinstance(value, (int, float)) and (not isinstance(energy_charge, (int, float)) or value >= energy_charge)]
        if plausible:
            charges["total_amount_twd"] = total = max(plausible)
            warnings.append("total_recovered_from_payment_summary")
    # A total not read at all is the pretax total and tax, when both were read.
    # A total that was read stays: 本期預繳 makes it rightly differ from them.
    pretax, tax = charges.get("pretax_total_twd"), charges.get("business_tax_twd")
    if (
        total is None
        and isinstance(pretax, (int, float)) and pretax > 0
        and isinstance(tax, (int, float)) and tax > 0
        and "prepaid_applied_twd" not in charges
    ):
        charges["total_amount_twd"] = total = round(pretax + tax)
        warnings.append("total_worked_out_from_pretax")
    if (
        not has_adjustments
        # Only where the energy charge is the whole bill: on a bill with other
        # charges, this would put the energy charge in as the total.
        and "basic_charge_twd" not in charges
        and isinstance(energy_charge, (int, float))
        and isinstance(total, (int, float))
        and 0 <= total < energy_charge * 0.1
        and abs(round(energy_charge) - energy_charge) <= 0.5
    ):
        charges["total_amount_twd"] = round(energy_charge)
        warnings.append("total_reconstructed_from_charge")


def _parse_layout(lines: Iterable[OcrLine]) -> BillResult:
    ordered = sorted(lines, key=lambda line: (line.y, line.x))
    if is_table_layout(ordered):
        return parse_table_layout(ordered, _prepaid_tail)
    headers = _find_headers(ordered)
    fields: dict[str, dict[str, Any]] = {key: {} for key, _ in headers}
    if headers:
        assigned = _share_out(ordered, headers)
        for line in assigned.get("charge_info", []):
            for field_key, pattern in _INLINE_CHARGES.items():
                if match := pattern.search(line.text):
                    fields["charge_info"][field_key] = _number(match.group(1))
    elif ordered:
        # No heading was read: every line may belong to any block.
        left, top = min(line.x for line in ordered), min(line.y for line in ordered) - 1
        headers = [(key, OcrLine("", left, top, 1, 1)) for key in ("user_info", "charge_info")]
        fields = {key: {} for key, _ in headers}
        assigned = {key: list(ordered) for key, _ in headers}
    else:
        assigned = {}

    for block_key, specs in _FIELDS.items():
        if block_key in fields:
            for field_key, aliases in specs.items():
                _read_labelled(fields[block_key], field_key, aliases, assigned[block_key], ordered)

    # The detailed usage table is independently recognizable even when the
    # page-level document classifier misses a title or billing-period marker.
    if "energy_usage" in fields:
        fields["energy_usage"].update(parse_energy_table(assigned["energy_usage"]))

    warnings: list[str] = []
    if "user_info" in fields:
        user_lines = assigned["user_info"]
        user_header = next(header for key, header in headers if key == "user_info")
        sibling_starts = [
            header.x
            for key, header in headers
            if key != "user_info" and header.x > user_header.x and abs(header.y - user_header.y) <= max(user_header.height, header.height) * 2
        ]
        right_edge = min(sibling_starts, default=float("inf"))
        groups, group_warnings = _energy_groups(user_lines, [line for line in ordered if line.x < right_edge])
        fields["user_info"].update(groups)
        warnings.extend(group_warnings)
        if period := _period_of(user_lines, ordered):
            fields["user_info"]["billing_period"] = period

    _repair_charges(fields.get("charge_info", {}), fields.get("user_info", {}), assigned.get("charge_info", []), ordered, warnings)

    if "payment_info" in fields:
        compact_lines = [re.sub(r"\s+", "", line.text) for line in assigned["payment_info"]]
        number = next((match.group(0) for text in compact_lines if (match := re.search(r"\d{1,2}-\d{2}-\d{4}-\d{2}-\d", text))), None)
        if number:
            fields["payment_info"]["customer_number"] = number
    return BillResult({**fields, "quality": {"warnings": warnings}})
