"""One page picture to a bill: find its blocks, read its lines, parse them."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from PIL import Image, ImageOps

from .blocks import Blocks
from .parser import (
    BillResult,
    OcrLine,
    enrich_bank_autopay_receipt,
    enrich_unruled_tables,
    parse_bill_layout,
    refine_document_type,
)

SCORE_THRESHOLD = 0.45
# A page straighter than this needs nothing done; beyond the other it is no slant.
MIN_SKEW_DEGREES, MAX_SKEW_DEGREES = 0.2, 15.0

_BLOCK_TITLES = {
    "payment_info": "繳費資訊 Payment Info.",
    "user_info": "用戶資訊 Basic Info.",
    "charge_info": "計費內容 Charge Info.",
    "other_info": "其他資訊 Other Info.",
    "invoice_info": "前期發票資訊 Invoice",
    "carrier_info": "載具資訊 Carrier",
    "charge_introduction": "電費計收說明 Charge Introduction",
    "energy_saving_info": "節電資訊 Energy Saving Info.",
    "meter_info": "電表資料 Meter Info.",
}
# Simplified glyphs a misreading can bring, for matching the labels below.
_TRADITIONAL = str.maketrans(
    "调节务号业营种类价时间楼帐单发动应总减码账网输续线馈项户统编规则说币记录处区乡镇县条请联执证机关样余灯压览据汇银厅场厂断缘纳经费约缴额别组数电计税内优离转",
    "調節務號業營種類價時間樓帳單發動應總減碼帳網輸續線饋項戶統編規則說幣記錄處區鄉鎮縣條請聯執證機關樣餘燈壓覽據匯銀廳場廠斷緣納經費約繳額別組數電計稅內優離轉",
)


def _lines_of(result: Mapping[str, Any]) -> list[OcrLine]:
    lines = []
    for text, score, box in zip(result["texts"], result["scores"], result["boxes"]):
        cleaned = str(text).strip()
        if not cleaned or float(score) < SCORE_THRESHOLD:
            continue
        points = box.tolist() if hasattr(box, "tolist") else box
        xs, ys = [float(point[0]) for point in points], [float(point[1]) for point in points]
        lines.append(OcrLine(cleaned, min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)))
    return lines


def find_blocks(image: Image.Image) -> tuple[dict[str, tuple[int, int, int, int]], str, str]:
    """The page's blocks, its kind of document, and how they were found."""
    front = Blocks._high_voltage_block_bounds(image)
    if "invoice_info" in front:
        return front, "high_voltage_bill", "pink_template"
    bank = Blocks._bank_autopay_block_bounds(image)
    if bank:
        return bank, "bank_autopay_receipt", "pink_bank_autopay"
    if front:
        return front, "high_voltage_bill", "pink_template"
    back = Blocks._high_voltage_back_block_bounds(image)
    if back:
        return back, "high_voltage_bill", "pink_template"

    # Not pink: any colour, drawn over as pink.
    normalized = Image.new("RGB", image.size, "white")
    normalized.paste((190, 100, 200), mask=Blocks._colored_mask(image))
    front = Blocks._high_voltage_block_bounds(normalized)
    if "invoice_info" in front:
        return front, "high_voltage_bill", "color_independent"
    bank = Blocks._bank_autopay_block_bounds(normalized)
    if bank:
        return bank, "bank_autopay_receipt", "color_independent"
    if front:
        return front, "high_voltage_bill", "color_independent"
    back = Blocks._high_voltage_back_block_bounds(normalized)
    if back:
        return back, "high_voltage_bill", "color_independent"
    return {}, "unknown", "full_page_fallback"


def _values_read(lines: list[OcrLine]) -> int:
    """How many of a bill's columns these lines fill."""
    from .record import _bill_values

    return sum(value is not None for value in _bill_values(parse_bill_layout(lines)).values())


def recognize_blocks(
    image_path: Path, *, engine: Any,
) -> tuple[list[OcrLine], dict[str, dict[str, int]], dict[str, list[OcrLine]], str, str]:
    """The page's lines, shared out among its blocks when the blocks lose none of them."""
    import numpy as np

    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    detected, document_type, method = find_blocks(image)
    # One detected rectangle is not enough evidence to crop the page.
    if len(detected) == 1:
        detected, method = {}, "partial_block_fallback"
    page = np.ascontiguousarray(np.asarray(image)[:, :, ::-1])
    found = _lines_of(engine.predict(page))
    if not detected:
        return sorted(found, key=lambda line: (line.y, line.x)), {}, {}, document_type, method

    lines: list[OcrLine] = []
    words_by_block: dict[str, list[OcrLine]] = {}
    for key, (left, top, right, bottom) in detected.items():
        lines.append(OcrLine(_BLOCK_TITLES[key], left, top, right - left, max(1, round((bottom - top) * 0.04)), key))
        inside = [
            OcrLine(line.text, line.x, line.y, line.width, line.height, key)
            for line in found
            if left <= line.x + line.width / 2 <= right and top <= line.y + line.height / 2 <= bottom
        ]
        lines.extend(inside)
        words_by_block[key] = inside
    # Blocks found too narrow leave out what the page read beside them: the values say so.
    if _values_read(found) > _values_read(lines):
        return found, {}, {}, document_type, "blocks_lost_values"
    bounds = {key: {"x": left, "y": top, "width": right - left, "height": bottom - top}
              for key, (left, top, right, bottom) in detected.items()}
    return lines, bounds, words_by_block, document_type, method


# Labels whose value sits to their right on the same row. When the detector
# skips that value, reading just the row again usually finds it.
_RETRY_LABELS = (
    "基本電費",
    "流動電費",
    "功率因數調整費",
    "本期預繳",
    "稅前應繳總金額",
    "應繳總金額",
    "計費期間",
    "尖峰度數",
    "半尖峰度數",
    "週六半尖峰度數",
    "離峰度數",
)
# The payment strip prints values below their labels, so it is not retried.
_RETRY_BLOCKS = {"user_info", "charge_info", "energy_usage", None}


def _compact_label(text: str) -> str:
    return re.sub(r"[\s:：()（）]", "", text.translate(_TRADITIONAL))


def _retry_alias(compact: str) -> str | None:
    """The retry label ``compact`` starts with, allowing one misread glyph."""
    exact = next((alias for alias in _RETRY_LABELS if compact.startswith(alias)), None)
    if exact is not None:
        return exact
    close = [
        alias
        for alias in _RETRY_LABELS
        if len(alias) >= 4
        and len(compact) >= len(alias)
        and sum(a != b for a, b in zip(alias, compact)) == 1
    ]
    return close[0] if len(close) == 1 else None


def _labels_missing_values(lines: Sequence[OcrLine]) -> list[OcrLine]:
    """Known label lines with no number printed to their right."""
    missing = []
    for label in lines:
        compact = _compact_label(label.text)
        alias = _retry_alias(compact)
        if alias is None or label.block_key not in _RETRY_BLOCKS:
            continue
        if re.search(r"\d", compact[len(alias):]):
            continue  # value printed inline with the label
        center = label.y + label.height / 2
        has_value = any(
            other is not label
            and re.search(r"\d", other.text)
            and other.x >= label.x + label.width * 0.5
            and abs(other.y + other.height / 2 - center)
            <= max(label.height, other.height) * 0.6
            for other in lines
        )
        if not has_value:
            missing.append(label)
    return missing


def _retry_missing_rows(
    image: Image.Image,
    lines: list[OcrLine],
    bounds: Mapping[str, Mapping[str, int]],
    *,
    engine: Any,
) -> list[str]:
    """Read again the rows whose value was not detected; returns the recovered labels.

    Detection misses depend on the input image, so the row is tried in colour
    first, then grayscale. New lines go into ``lines`` in the page's coordinates.
    """
    import numpy as np

    recovered = []
    for label in _labels_missing_values(lines):
        block = bounds.get(label.block_key) if label.block_key else None
        right = block["x"] + block["width"] if block else image.width
        left = max(0, round(label.x) - 4)
        top = max(0, round(label.y) - 6)
        box = (left, top, min(image.width, right), min(image.height, round(label.y + label.height) + 6))
        if box[2] - box[0] < 10 or box[3] - box[1] < 10:
            continue
        row = image.crop(box)
        for variant in (row, ImageOps.grayscale(row).convert("RGB")):
            bgr = np.ascontiguousarray(np.asarray(variant)[:, :, ::-1])
            values = [
                line
                for line in _lines_of(engine.predict(bgr))
                if re.search(r"\d", line.text)
                and left + line.x >= label.x + label.width * 0.5
            ]
            if values:
                lines.extend(
                    OcrLine(line.text, left + line.x, top + line.y, line.width, line.height, label.block_key)
                    for line in values
                )
                recovered.append(label.text)
                break
    return recovered


def read_image(image_path: str | Path, engine: Any) -> BillResult:
    """One bill image as a bill, straightening it first.

    Rows are matched by height, so a slanted photo pairs labels with the row
    above or below. The lines are put back where they are on the page as given.
    """
    path = Path(image_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    angle = engine.page_angle(path)
    if abs(angle) < MIN_SKEW_DEGREES or abs(angle) > MAX_SKEW_DEGREES:
        return _read_straight(path, engine)
    with Image.open(path) as source:
        page = ImageOps.exif_transpose(source).convert("RGB")
    with TemporaryDirectory(prefix="bill_reader_straight_") as folder:
        straight = Path(folder) / "page.png"
        page.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=(255, 255, 255)).save(straight)
        result = _read_straight(straight, engine)
    centre_x, centre_y = page.width / 2, page.height / 2
    radians = math.radians(angle)
    cos, sin = math.cos(radians), math.sin(radians)
    moved = []
    for line in result.ocr_lines:
        dx, dy = line.x + line.width / 2 - centre_x, line.y + line.height / 2 - centre_y
        moved.append(OcrLine(line.text, centre_x + dx * cos - dy * sin - line.width / 2,
                             centre_y + dx * sin + dy * cos - line.height / 2, line.width, line.height, line.block_key))
    result.ocr_lines = moved
    result["quality"]["straightened_degrees"] = round(angle, 2)
    return result


def _read_straight(path: Path, engine: Any) -> BillResult:
    lines, bounds, words_by_block, document_type, method = recognize_blocks(path, engine=engine)
    with Image.open(path) as source:
        page_image = ImageOps.exif_transpose(source).convert("RGB")
    recovered = _retry_missing_rows(page_image, lines, bounds, engine=engine)
    result = parse_bill_layout(lines)
    result.ocr_lines = lines
    if recovered:
        result["quality"]["row_retry"] = recovered
    # A layout-specific parser can identify a document more precisely than the block finder.
    document_type = result.get("document_type") or document_type
    document_type = refine_document_type(result, lines, document_type)
    result["document_type"] = document_type
    result["quality"]["block_detection"] = method
    for key, block_bounds in bounds.items():
        if key in result["blocks"]:
            result["blocks"][key]["bounds"] = block_bounds
    enrich_unruled_tables(result, words_by_block, bounds)
    if document_type == "bank_autopay_receipt":
        enrich_bank_autopay_receipt(result, words_by_block, bounds)
        payment_fields = result.get("payment_info", {})
        payment_date = payment_fields.pop("due_date", None)
        if payment_date:
            payment_fields["payment_date"] = payment_date
    result["quality"].update({"recognized_line_count": len(lines)})
    return result


def read_pdf_page(pdf_path: str | Path, page_number: int, dpi: int, engine: Any) -> BillResult:
    """One PDF page, drawn as a picture and read."""
    import pdfplumber

    path = Path(pdf_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    with pdfplumber.open(path) as pdf, TemporaryDirectory(prefix="bill_reader_pdf_") as folder:
        if page_number < 1 or page_number > len(pdf.pages):
            raise ValueError(f"page number out of range: {page_number}")
        image_path = Path(folder) / f"page-{page_number}.png"
        pdf.pages[page_number - 1].to_image(resolution=dpi, antialias=True).original.save(image_path)
        bill = read_image(image_path, engine)
    bill["quality"].update({"source": "image_pdf", "page_number": page_number, "dpi": dpi})
    return bill
