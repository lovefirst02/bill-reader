"""Where on the page a value was printed: the box of its line, beside its label.

Ported from the main app's field_locator.py, which also narrows the search by
the bill's blocks; lines here carry no block, so the whole page is searched,
and a value is only marked where it is found as printed.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .lines import OcrLine, _compact

Box = tuple[float, float, float, float]  # x, y, width, height in the page's pixels

_NUMBER = re.compile(r"-?\d[\d,.]*\d|-?\d")  # dots and commas together: OCR mixes them up (1.591,649.0)
_GROUP_HEADINGS = {"energy_consumption": ("計費度數",), "transferred_energy": ("轉供度數",)}


def _center_y(line: OcrLine) -> float:
    return line.y + line.height / 2


def _same_row(a: OcrLine, b: OcrLine) -> bool:
    return abs(_center_y(a) - _center_y(b)) <= max(a.height, b.height) * 0.6


def union(lines: Iterable[OcrLine], pad: float = 4) -> Box:
    lines = list(lines)
    left = min(line.x for line in lines) - pad
    top = min(line.y for line in lines) - pad
    right = max(line.x + line.width for line in lines) + pad
    bottom = max(line.y + line.height for line in lines) + pad
    return (left, top, right - left, bottom - top)


def _readings(token: str) -> list[float]:
    """A printed figure as a number, and as one with a dot read for a thousands comma (1.591,649.0)."""
    readings = []
    for text in (token.replace(",", ""), re.sub(r"[.,](?=\d{3}(?:[.,]|$))", "", token),
                 re.sub(r"[.,](?=\d{3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?$)", "", token).replace(",", ".")):
        try:
            readings.append(float(text))
        except ValueError:
            continue
    return readings


def matches_value(line: OcrLine, value: Any) -> bool:
    """Whether the line shows ``value``: a number as printed, or a text's digits (a period, a meter)."""
    if value is None:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return any(abs(abs(reading) - abs(value)) < 0.01 for token in _NUMBER.findall(line.text) for reading in _readings(token))
    digits = re.sub(r"\D", "", str(value))
    return len(digits) >= 4 and digits in re.sub(r"\D", "", line.text)


def _table_scope(lines: list[OcrLine], group_key: str) -> list[OcrLine] | None:
    """On a table bill, a group's caption sits beside its rows, centred: the groups part halfway between captions.

    The same split as table_layout.parse_energy_table; None when the page is no table bill.
    """
    from .table_layout import is_table_layout

    if not is_table_layout(lines):
        return None
    captions = {
        key: next((line for line in lines if any(_compact(line.text).startswith(alias) for alias in aliases)), None)
        for key, aliases in _GROUP_HEADINGS.items()
    }
    billed, transferred = captions["energy_consumption"], captions["transferred_energy"]
    if billed is None:
        return None
    split = float("inf")
    if transferred is not None:
        split = (_center_y(billed) + _center_y(transferred)) / 2 - min(billed.height, transferred.height) / 4
    top = min((line.y for line in lines if _compact(line.text).startswith("用電資料")), default=0.0)
    if group_key == "energy_consumption":
        return [line for line in lines if top <= _center_y(line) < split]
    return [line for line in lines if _center_y(line) >= split]


def group_scope(lines: list[OcrLine], group_key: str) -> list[OcrLine]:
    """The lines under a kWh group's heading, up to the next group's."""
    table = _table_scope(lines, group_key)
    if table is not None:
        return table
    headings = {
        key: min((line.y for line in lines if any(_compact(line.text).startswith(alias) for alias in aliases)), default=None)
        for key, aliases in _GROUP_HEADINGS.items()
    }
    start = headings.get(group_key)
    if start is None:
        return lines if group_key == "energy_consumption" else []
    end = min((y for y in headings.values() if y is not None and y > start), default=float("inf"))
    return [line for line in lines if start - 2 <= _center_y(line) < end]


def _candidates(label: OcrLine, scope: list[OcrLine]) -> list[OcrLine]:
    beside = [line for line in scope if line is not label and _same_row(line, label) and line.x > label.x]
    below = [
        line for line in scope
        if line is not label and label.y + label.height <= line.y <= label.y + label.height * 3
        and line.x < label.x + label.width and line.x + line.width > label.x
    ]
    return sorted(beside, key=lambda line: line.x) + sorted(below, key=lambda line: line.y)


def locate(lines: Iterable[OcrLine], aliases: tuple[str, ...], value: Any, group_key: str | None = None) -> Box | None:
    """The box of ``value`` and its label, or of the value alone when no label is beside it.

    None when the value is not found as printed: a mark in the wrong place is worse than none.
    """
    if value is None:
        return None
    scope = list(lines)
    if group_key:
        scope = group_scope(scope, group_key)
    labels = [line for line in scope if any(_compact(line.text).startswith(alias) for alias in aliases)]
    for label in sorted(labels, key=lambda line: line.y):
        if matches_value(label, value):
            return union([label])
        candidates = _candidates(label, scope)
        match = next((line for line in candidates if matches_value(line, value)), None)
        if match is not None:
            return union([label, match])
        # A table cell wraps a value over two lines (計費期間 / 109/12/01~ / 109/12/31).
        for first, second in zip(candidates, candidates[1:]):
            if matches_value(OcrLine(first.text + second.text, first.x, first.y, first.width, first.height), value):
                return union([label, first, second])
    if group_key is None:
        alone = [line for line in scope if matches_value(line, value)]
        if len(alone) == 1:
            return union(alone)
    return None
