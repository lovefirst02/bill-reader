"""Where on the page a value was printed: the box of its line, beside its label.

Ported from the main app's field_locator.py, which also narrows the search by
the bill's blocks; lines here carry no block, so the whole page is searched,
and a value is only marked where it is found as printed.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .lines import OcrLine

Box = tuple[float, float, float, float]  # x, y, width, height in the page's pixels

_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_GROUP_HEADINGS = {"energy_consumption": ("計費度數",), "transferred_energy": ("轉供度數",)}


def _compact(text: str) -> str:
    return re.sub(r"[\s：:（）()]", "", text).replace("缴", "繳")


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


def matches_value(line: OcrLine, value: Any) -> bool:
    """Whether the line shows ``value``: a number as printed, or a text's digits (a period, a meter)."""
    if value is None:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        for token in _NUMBER.findall(line.text):
            try:
                if abs(abs(float(token.replace(",", ""))) - abs(value)) < 0.01:
                    return True
            except ValueError:
                continue
        return False
    digits = re.sub(r"\D", "", str(value))
    return len(digits) >= 4 and digits in re.sub(r"\D", "", line.text)


def group_scope(lines: list[OcrLine], group_key: str) -> list[OcrLine]:
    """The lines under a kWh group's heading, up to the next group's."""
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
