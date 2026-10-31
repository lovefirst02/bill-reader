"""One page picture to a bill: straighten it, read its lines, parse them."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Mapping

import numpy as np
from PIL import Image, ImageOps

from .lines import BillResult, OcrLine
from .parser import parse_bill_layout

SCORE_THRESHOLD = 0.45
# A page straighter than this needs nothing done; beyond the other it is no slant.
MIN_SKEW_DEGREES, MAX_SKEW_DEGREES = 0.2, 15.0


def _lines_of(result: Mapping[str, Any]) -> list[OcrLine]:
    lines = []
    for text, score, box in zip(result["texts"], result["scores"], result["boxes"]):
        cleaned = str(text).strip()
        if not cleaned or float(score) < SCORE_THRESHOLD:
            continue
        points = box.tolist() if hasattr(box, "tolist") else box
        xs, ys = [float(point[0]) for point in points], [float(point[1]) for point in points]
        lines.append(OcrLine(cleaned, min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)))
    return sorted(lines, key=lambda line: (line.y, line.x))


def _read_straight(path: Path, engine: Any) -> BillResult:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    lines = _lines_of(engine.predict(np.ascontiguousarray(np.asarray(image)[:, :, ::-1])))
    result = parse_bill_layout(lines)
    result.ocr_lines = lines
    return result


def read_image(image_path: str | Path, engine: Any) -> BillResult:
    """One bill image as a bill. Rows are matched by height, so a slanted photo is straightened first."""
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
        return _read_straight(straight, engine)


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
