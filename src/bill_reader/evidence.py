"""Evidence marks: a highlight over each value the master was given, and a red label naming it.

Marks are PDF annotations, not drawing on the page, so a person can move,
resize or delete them in a PDF reader (Acrobat Reader). A PDF bill is marked
with an incremental update: the marks are appended and the original bytes stay
as they were. A photo becomes a PDF of itself with the marks.

A value the bill does not print, as when a prepaid balance paid part of it,
is marked on the figures it was worked out from, its label giving the sum.
Labels keep off the printed text where they can, and are plain ASCII: a
reader that redraws a comment may lack a Chinese font.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .lines import BillResult
from .locate import Box, locate
from .record import _ENERGY_GROUPS, _TOU_COMPONENTS, _is_number

AUTHOR = "BillReader"
HIGHLIGHT = (1.0, 0.922, 0.231)  # highlighter yellow
HIGHLIGHT_OPACITY = 0.4
LABEL = (0.827, 0.184, 0.184)  # red
FONT_SIZE = 8.0  # on an A4 page; a larger page, as a photo makes, gets larger labels
PHOTO_DPI = 150  # a photo's page: its pixels at this size

PERIOD, TOTAL, PRETAX = "Billing Period 計費期間", "應繳總金額", "稅前應繳總金額"
_KWH = {
    "尖峰度數": (("經常尖峰度數", "尖峰度數"), "peak"),
    "半尖峰度數": (("半尖峰度數",), "semi-peak"),
    "週六半尖峰度數": (("週六半尖峰度數",), "Saturday semi-peak"),
    "離峰度數": (("離峰度數",), "off-peak"),
}

Fraction = tuple[float, float, float, float]


@dataclass(frozen=True)
class Mark:
    """What one mark on page ``page`` shows.

    ``box`` is the highlight, ``label_box`` where its label is written: a mark
    may have either or both. With neither, the label is a note along the page's
    top. Both are in fractions of the page from its top left (x, y, width,
    height), so they do not depend on the size the page was read at. ``label``
    is also the highlight's comment, shown when the pointer is over it.
    """

    page: int
    label: str
    box: Fraction | None = None
    label_box: Fraction | None = None


def _font_size(page_width: float) -> float:
    """The labels' size on a page ``page_width`` points wide."""
    return max(FONT_SIZE, FONT_SIZE * page_width / 595)


def _figure(value: Any) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{int(value):,}"


def _period_label(period: Any) -> str:
    """The period in Gregorian dates, for readers who do not count years from 1912: 114/01/01~114/01/31 is 2025."""
    dates = re.findall(r"(\d{2,4})\D+(\d{1,2})\D+(\d{1,2})", str(period))
    if len(dates) != 2:
        return "Billing period"
    gregorian = [f"{int(year) + 1911 if int(year) < 1911 else int(year)}-{int(month):02d}-{int(day):02d}"
                 for year, month, day in dates]
    return f"Billing period {gregorian[0]} to {gregorian[1]}"


# A label; its highlight, if any; the highlights it names as a group, if any.
_Labelled = tuple[str, Box | None, list[Box] | None]


def _labelled(bill: BillResult, row: Mapping[str, Any]) -> tuple[list[_Labelled], list[str]]:
    """The labels for a page and the boxes of what they name, in the page's pixels; and what was not found."""
    lines = list(getattr(bill, "ocr_lines", None) or [])
    marks: list[_Labelled] = []
    missing: list[str] = []

    def mark(label: str, aliases: tuple[str, ...], value: Any) -> None:
        box = locate(lines, aliases, value)
        if box is None:
            missing.append(label)
        else:
            marks.append((label, box, None))

    if row.get(PERIOD):
        mark(_period_label(row[PERIOD]), ("計費期間",), row[PERIOD])

    pretax, total = row.get(PRETAX), row.get(TOTAL)
    printed_pretax = bill.get("charge_info.pretax_total_twd")
    printed_total = bill.get("charge_info.total_amount_twd")
    if printed_total is None:
        printed_total = bill.get("payment_info.total_amount_twd")
    tax = bill.get("charge_info.business_tax_twd")
    used = total - printed_total if _is_number(total) and _is_number(printed_total) and total != printed_total else 0
    if _is_number(pretax):
        cost = f"Grey Electricity Cost {_figure(pretax)}"
        if _is_number(printed_pretax):
            mark(cost + (f" = {_figure(printed_pretax)} + {_figure(used)} prepaid used" if used else ""),
                 ("稅前應繳總金額",), printed_pretax)
        elif _is_number(printed_total):
            if printed_total == 0:
                worked = "0"
            elif _is_number(tax):
                worked = f"{_figure(printed_total)} - tax {_figure(tax)}"
            else:
                worked = f"{_figure(printed_total)} / 1.05"
            if used:
                worked = f"({worked}) + {_figure(used)} prepaid used"
            mark(f"{cost} = {worked}", ("應繳總金額", "繳費總金額"), printed_total)
        else:
            missing.append(cost)
    if used:
        before = bill.get("charge_info.previous_prepaid_balance_twd")
        after = bill.get("charge_info.current_prepaid_balance_twd")
        if _is_number(before):
            left = abs(after) if _is_number(after) else 0
            mark(f"Prepaid used {_figure(used)} = balance {_figure(abs(before))} - {_figure(left)} left",
                 ("上期預繳餘額",), before)
        if _is_number(after):
            mark(f"Prepaid balance left {_figure(abs(after))}", ("本期預繳餘額",), after)
        else:
            marks.append(("No later prepaid balance printed: counted as 0", None, None))

    # Each kWh row is highlighted and the group gets one label: a label per row would hide the next row.
    for group, group_key in _ENERGY_GROUPS:
        kind = "Electricity Usage" if group_key == "energy_consumption" else "Transferred"
        rows: list[tuple[str, Box]] = []
        total_kwh = 0
        for component, _ in _TOU_COMPONENTS:
            value = row.get(f"{group}_{component}")
            if _is_number(value) and value:
                aliases, name = _KWH[component]
                box = locate(lines, aliases, value, group_key)
                if box is None:
                    missing.append(f"{kind} kWh {name}")
                else:
                    rows.append((f"{kind} kWh {name} {_figure(value)}", box))
                total_kwh += value
        if rows:
            marks.append((f"{kind} {_figure(total_kwh)} kWh", None, [box for _, box in rows]))
            marks += [(label, box, None) for label, box in rows]
    return marks, missing


def _turned_back(box: Box, size: tuple[float, float], degrees: float) -> Box:
    """A box on a picture turned by ``degrees`` (PIL's rotate, about its centre), on the picture as it was."""
    x, y, width, height = box
    cx, cy = size[0] / 2, size[1] / 2
    angle = math.radians(degrees)
    cos, sin = math.cos(angle), math.sin(angle)
    points = []
    for px, py in ((x, y), (x + width, y), (x, y + height), (x + width, y + height)):
        dx, dy = px - cx, py - cy
        points.append((cx + dx * cos - dy * sin, cy + dx * sin + dy * cos))
    left, top = min(p[0] for p in points), min(p[1] for p in points)
    return (left, top, max(p[0] for p in points) - left, max(p[1] for p in points) - top)


def _overlap(a: Fraction, b: Fraction) -> float:
    width = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    height = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(0.0, width) * max(0.0, height)


def _union(boxes: list[Fraction]) -> Fraction:
    left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
    return (left, top, max(b[0] + b[2] for b in boxes) - left, max(b[1] + b[3] for b in boxes) - top)


def _place_label(near: Fraction, size: tuple[float, float], taken: list[Fraction]) -> Fraction:
    """Where a label of ``size`` beside ``near`` hides the least printed text and other marks.

    Tried in turn: above, below, right, left, then further up or down; the first
    that hides nothing is taken, else the one that hides least.
    """
    x, y, width, height = near
    label_width, label_height = size
    gap = label_height * 0.15
    spots = [
        (x, y - label_height - gap), (x, y + height + gap),
        (x + width + gap, y + (height - label_height) / 2), (x - label_width - gap, y + (height - label_height) / 2),
        (x + width - label_width, y - label_height - gap),
        (x, y - 2 * label_height - gap), (x, y + height + label_height + gap),
    ]
    best: tuple[float, Fraction] | None = None
    for left, top in spots:
        left = min(max(0.0, left), 1.0 - label_width)
        top = min(max(0.0, top), 1.0 - label_height)
        spot = (left, top, label_width, label_height)
        hidden = sum(_overlap(spot, box) for box in taken)
        if hidden == 0:
            return spot
        if best is None or hidden < best[0]:
            best = (hidden, spot)
    assert best is not None
    return best[1]


def page_marks(bill: BillResult, row: Mapping[str, Any], page: int) -> tuple[list[Mark], list[str]]:
    """The marks for one bill page, and the labels whose value was not found as printed."""
    quality = bill.get("quality") or {}
    size = quality.get("image_size")
    if not size:
        return [], ["page size unknown"]
    turned = quality.get("turned_degrees")
    points = 72 / (quality.get("dpi") or PHOTO_DPI)  # a pixel of the page, in points as it is written

    def fraction(box: Box) -> Fraction:
        if turned:
            box = _turned_back(box, size, turned)
        return (box[0] / size[0], box[1] / size[1], box[2] / size[0], box[3] / size[1])

    labelled, missing = _labelled(bill, row)
    font = _font_size(size[0] * points)
    rows_of_groups = {id(box) for _, _, rows in labelled for box in rows or ()}
    highlights = {id(box): fraction(box) for _, box, _ in labelled if box is not None}
    # Labels keep off the printed text and every highlight.
    taken = [fraction((line.x, line.y, line.width, line.height)) for line in getattr(bill, "ocr_lines", None) or []]
    taken += list(highlights.values())
    marks = []
    for label, box, rows in labelled:
        label_size = (_label_width(label, font) / (size[0] * points), (font * 1.5) / (size[1] * points))
        if rows:  # a group's label, beside the group
            spot = _place_label(_union([highlights[id(row_box)] for row_box in rows]), label_size, taken)
            taken.append(spot)
            marks.append(Mark(page, label, None, spot))
        elif box is None:
            marks.append(Mark(page, label))
        elif id(box) in rows_of_groups:  # a row of a group: its label is only the highlight's comment
            marks.append(Mark(page, label, highlights[id(box)]))
        else:
            spot = _place_label(highlights[id(box)], label_size, taken)
            taken.append(spot)
            marks.append(Mark(page, label, highlights[id(box)], spot))
    return marks, missing


def to_json(marks: list[Mark]) -> list[dict[str, Any]]:
    return [asdict(mark) for mark in marks]


def from_json(items: list[Mapping[str, Any]]) -> list[Mark]:
    def box(value: Any) -> Fraction | None:
        return tuple(value) if value else None  # type: ignore[return-value]

    return [Mark(item["page"], item["label"], box(item.get("box")), box(item.get("label_box"))) for item in items]


# ---------------------------------------------------------------------- writing
def _ascii(text: str) -> str:
    return text.encode("ascii", "replace").decode("ascii")


def _literal(text: str) -> str:
    return "(" + _ascii(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def _label_width(text: str, size: float) -> float:
    return len(text) * size * 0.52 + 4  # Helvetica's mean width, and a little room


def _stream(writer: Any, content: str, width: float, height: float, resources: Any) -> Any:
    from pypdf.generic import ArrayObject, DecodedStreamObject, FloatObject, NameObject

    stream = DecodedStreamObject()
    stream.set_data(content.encode("latin-1"))
    stream.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Form"),
        NameObject("/BBox"): ArrayObject([FloatObject(0), FloatObject(0), FloatObject(width), FloatObject(height)]),
        NameObject("/Resources"): resources,
    })
    return writer._add_object(stream)


def _annotation(subtype: str, rect: tuple[float, float, float, float], label: str, extra: dict, appearance: Any) -> Any:
    from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject

    annotation = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject(subtype),
        NameObject("/Rect"): ArrayObject([FloatObject(value) for value in rect]),
        NameObject("/Contents"): TextStringObject(label),
        NameObject("/T"): TextStringObject(AUTHOR),
        NameObject("/NM"): TextStringObject(f"billreader-{uuid.uuid4().hex[:12]}"),
        NameObject("/F"): NumberObject(4),  # printed
        NameObject("/AP"): DictionaryObject({NameObject("/N"): appearance}),
    })
    annotation.update(extra)
    return annotation


def _highlight(writer: Any, rect: tuple[float, float, float, float], label: str) -> Any:
    from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject

    width, height = rect[2] - rect[0], rect[3] - rect[1]
    color = " ".join(f"{part:.3f}" for part in HIGHLIGHT)
    resources = DictionaryObject({NameObject("/ExtGState"): DictionaryObject({NameObject("/GS"): DictionaryObject({
        NameObject("/ca"): FloatObject(HIGHLIGHT_OPACITY), NameObject("/CA"): FloatObject(HIGHLIGHT_OPACITY)})})})
    appearance = _stream(writer, f"q /GS gs {color} rg 0 0 {width:.2f} {height:.2f} re f Q", width, height, resources)
    rgb = ArrayObject([FloatObject(part) for part in HIGHLIGHT])
    return _annotation("/Square", rect, label, {
        NameObject("/C"): rgb, NameObject("/IC"): rgb, NameObject("/CA"): FloatObject(HIGHLIGHT_OPACITY),
        NameObject("/BS"): DictionaryObject({NameObject("/W"): NumberObject(0)}),
    }, appearance)


def _text(writer: Any, rect: tuple[float, float, float, float], label: str, font_size: float) -> Any:
    from pypdf.generic import DictionaryObject, NameObject, TextStringObject

    width, height = rect[2] - rect[0], rect[3] - rect[1]
    color = " ".join(f"{part:.3f}" for part in LABEL)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding")})
    resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/Helv"): font})})
    content = (f"q 1 1 1 rg 0 0 {width:.2f} {height:.2f} re f Q "
               f"BT /Helv {font_size:.1f} Tf {color} rg 2 {height * 0.28:.2f} Td {_literal(label)} Tj ET")
    appearance = _stream(writer, content, width, height, resources)
    return _annotation("/FreeText", rect, _ascii(label), {
        NameObject("/DA"): TextStringObject(f"/Helv {font_size:.1f} Tf {color} rg"),
    }, appearance)


def has_marks(path: Path) -> bool:
    """Whether a PDF already carries marks this program made."""
    from pypdf import PdfReader

    for page in PdfReader(path).pages:
        for annotation in page.get("/Annots") or []:
            if annotation.get_object().get("/T") == AUTHOR:
                return True
    return False


def _place(page: Any, marks: list[Mark], writer: Any, number: int) -> int:
    """Adds a page's marks; the number of annotations added."""
    box = page.cropbox
    left, top, width, height = float(box.left), float(box.top), float(box.width), float(box.height)

    def rect(fraction: Fraction) -> tuple[float, float, float, float]:
        fx, fy, fw, fh = fraction
        return (left + fx * width, top - (fy + fh) * height, left + (fx + fw) * width, top - fy * height)

    added = notes = 0
    font = _font_size(width)
    label_height = font * 1.5
    for mark in marks:
        if mark.box is not None:
            writer.add_annotation(number, _highlight(writer, rect(mark.box), mark.label))
            added += 1
        if mark.label_box is not None:
            writer.add_annotation(number, _text(writer, rect(mark.label_box), mark.label, font))
            added += 1
        elif mark.box is None:  # a note for the page, along its top
            notes += 1
            x, y = left + 8, top - 8 - label_height * notes
            writer.add_annotation(number, _text(writer, (x, y, x + _label_width(mark.label, font), y + label_height), mark.label, font))
            added += 1
    return added


def mark_pdf(source: Path, target: Path, marks: list[Mark]) -> int:
    """``source`` with the marks added, written to ``target`` as an incremental update; the annotations added.

    A page turned by /Rotate is left unmarked: where its text was read is not where it is drawn.
    """
    from pypdf import PdfWriter

    writer = PdfWriter(source, incremental=True)
    added = 0
    for number, page in enumerate(writer.pages, start=1):
        on_page = [mark for mark in marks if mark.page == number]
        if on_page and not int(page.get("/Rotate", 0)) % 360:
            added += _place(page, on_page, writer, number - 1)
    with target.open("wb") as stream:
        writer.write(stream)
    return added


def mark_photo(source: Path, target: Path, marks: list[Mark]) -> int:
    """A PDF of the photo, upright as it is shown, with the marks; the annotations added."""
    import tempfile

    from PIL import Image, ImageOps
    from pypdf import PdfWriter

    with Image.open(source) as image:
        upright = ImageOps.exif_transpose(image).convert("RGB")
    with tempfile.TemporaryDirectory(prefix="bill_reader_evidence_") as folder:
        page = Path(folder) / "page.pdf"
        upright.save(page, "PDF", resolution=PHOTO_DPI)
        writer = PdfWriter(page)
        added = _place(writer.pages[0], [mark for mark in marks if mark.page == 1], writer, 0)
        with target.open("wb") as stream:
            writer.write(stream)
    return added
