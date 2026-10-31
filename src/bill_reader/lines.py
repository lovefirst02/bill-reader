"""Lines of text with their places on the page, and what the layouts share."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OcrLine:
    text: str
    x: float
    y: float
    width: float
    height: float

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2


class BillResult(dict):
    """A parsed page, its fields addressable by dotted path."""

    # The lines it was parsed from, kept outside the dict; the log lists them.
    ocr_lines: list[OcrLine] | tuple[OcrLine, ...] = ()

    def get(self, path: str, default: Any = None) -> Any:
        current: Any = self
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = dict.__getitem__(current, part)
        return current


def _compact(text: str) -> str:
    text = text.translate(
        str.maketrans(
            {
                "计": "計",
                "电": "電",
                "费": "費",
                "数": "數",
                "经": "經",
                "转": "轉",
                "离": "離",
                "周": "週",
                "优": "優",
                "内": "內",
                "缴": "繳",
                "税": "稅",
                "组": "組",
                "别": "別",
                "额": "額",
                "约": "約",
            }
        )
    ).replace("·", ".")
    return re.sub(r"[^0-9A-Za-z\u3400-\u9fff.+-]", "", text).lower()


def _number(value: str) -> int | float:
    parsed = float(value.replace(",", "").replace("·", "."))
    return int(parsed) if parsed.is_integer() else parsed


def _parse_energy_number(text: str) -> int | float | None:
    """Accept a standalone kWh value, excluding OCR noise from other units."""
    compact = re.sub(r"\s+", "", text).replace("。", ".").replace("·", ".")
    if any(marker in compact for marker in ("%", "％", "元", "年", "月", "日")):
        return None
    match = re.fullmatch(r"([+]?[\d,]+(?:\.\d+)?)(?:度|kwh)?", compact, re.IGNORECASE)
    return _number(match.group(1)) if match else None


def _right_hand_energy_value(
    label: OcrLine,
    lines: list[OcrLine],
    used_candidate_ids: set[int],
) -> tuple[int | float | None, OcrLine | None]:
    """Pair a kWh label by vertical alignment before horizontal distance."""
    label_center_y = label.y + label.height / 2
    vertical_tolerance = max(6.0, label.height * 0.45)
    candidates: list[tuple[float, float, OcrLine, int | float]] = []
    for line in lines:
        if line is label or id(line) in used_candidate_ids or line.x <= label.x:
            continue
        value = _parse_energy_number(line.text)
        if value is None:
            continue
        vertical_distance = abs(
            (line.y + line.height / 2) - label_center_y
        )
        if vertical_distance > vertical_tolerance:
            continue
        horizontal_distance = max(0.0, line.x - (label.x + label.width))
        candidates.append((vertical_distance, horizontal_distance, line, value))
    if not candidates:
        return None, None
    _, _, candidate, value = min(
        candidates,
        key=lambda item: (item[0], item[1]),
    )
    return value, candidate
