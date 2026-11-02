"""Every page of a list of files: from a PDF's own text where it can be trusted, by the models otherwise."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .parser import BillResult
from .record import BillRecord, bill_to_record, fill_missing, is_charge_page
from .vocabulary import NORMAL_LABELS, normal, split_label

PDF_SUFFIXES = {".pdf"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
SUPPORTED_SUFFIXES = PDF_SUFFIXES | IMAGE_SUFFIXES


def find_bill_files(folder: str | Path) -> list[Path]:
    """Every bill file under ``folder``, sorted by path."""
    return sorted(path for path in Path(folder).rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES)


def count_pages(path: Path) -> int:
    if path.suffix.lower() not in PDF_SUFFIXES:
        return 1
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return len(pdf.pages)


MIN_LABELS = 2  # a Taipower page read by the models shows at least 3; a water bill or a notice, 0 or 1


def labels_read(bill: BillResult) -> int:
    """How many of a Taipower bill's own words the models found on the page."""
    found = set()
    for line in getattr(bill, "ocr_lines", None) or []:
        text = normal(line.text)
        if text in NORMAL_LABELS:
            found.add(text.rstrip(":"))
        elif split := split_label(text):
            found.add(split[0].rstrip(":"))
    return len(found)


def has_bill_values(bill: BillResult) -> bool:
    """A charge page with something of a bill on it: a page of other tables may parse as an empty one.

    Read by the models, it must also show a few of a Taipower bill's words, as a
    water bill with its own total does not; a PDF's text is only used when its
    charges are labelled already (pdf_text.py).
    """
    if bill.get("quality.source") != "pdf_text" and labels_read(bill) < MIN_LABELS:
        return False
    return is_charge_page(bill) and any(
        bill.get(path) is not None
        for path in (
            "charge_info.basic_charge_twd", "charge_info.energy_charge_twd", "charge_info.pretax_total_twd",
            "charge_info.total_amount_twd", "user_info.customer_number", "payment_info.customer_number",
        )
    )


class PageReader:
    """Reads one page at a time, loading the models only if a page needs them."""

    def __init__(self, dpi: int = 200) -> None:
        self.dpi = dpi
        self._loaded: Any | None = None

    def _engine(self) -> Any:
        if self._loaded is None:
            from .engine import OnnxEngine, model_dir

            folder = model_dir()
            if folder is None:
                raise RuntimeError("Models not found: run python scripts/fetch_models.py, or set BILL_READER_MODELS")
            self._loaded = OnnxEngine(folder)
        return self._loaded

    def _read_with(self, engine: Any, path: Path, page_number: int | None) -> BillResult:
        from .page import read_image, read_pdf_page

        if path.suffix.lower() in PDF_SUFFIXES:
            return read_pdf_page(path, page_number, self.dpi, engine)
        return read_image(path, engine)

    def _fill_gaps(self, bill: BillResult, path: Path, page_number: int | None) -> None:
        """What a PDF's text lacks, from the models; what they read is flagged as such."""
        quality = bill["quality"]
        try:
            engine = self._engine()
            read = self._read_with(engine, path, page_number)
        except Exception as error:  # noqa: BLE001 - the text's values still stand
            quality["pdf_text"]["fill_failed"] = f"{type(error).__name__}: {error}"
            return
        filled = fill_missing(bill, read)
        quality["pdf_text"].update(filled=filled, model_lines=[line.text for line in getattr(read, "ocr_lines", None) or []])
        model = getattr(engine, "last_read", None)
        quality["model_read"] = model if isinstance(model, dict) else None
        if filled:
            quality["warnings"] = [*(quality.get("warnings") or []), f"{'、'.join(filled)} 由模型補值"]

    def read(self, path: Path, page_number: int | None) -> BillResult:
        notes: dict[str, Any] = {}
        if path.suffix.lower() in PDF_SUFFIXES:
            from .pdf_text import read_page

            bill = read_page(path, page_number, self.dpi, notes)
            if bill is not None:
                bill["quality"]["pdf_text"] = notes
                if notes.get("missing"):
                    self._fill_gaps(bill, path, page_number)
                return bill
        engine = self._engine()
        bill = self._read_with(engine, path, page_number)
        model = getattr(engine, "last_read", None)
        if notes.get("words") or isinstance(model, dict):
            quality = bill.setdefault("quality", {})
            quality["pdf_text"] = notes
            quality["model_read"] = model if isinstance(model, dict) else None
        return bill


@dataclass
class PageDone:
    path: Path
    page_number: int | None
    record: BillRecord | None  # None when the page has no charge breakdown
    bill: BillResult | None = None


@dataclass
class FileFinished:
    path: Path
    error: str | None = None


def iter_pages(files: list[Path], reader: PageReader) -> Iterator[PageDone | FileFinished]:
    """Every page of every file; a file that fails is reported and the rest go on."""
    for path in files:
        try:
            is_pdf = path.suffix.lower() in PDF_SUFFIXES
            for page_number in range(1, count_pages(path) + 1):
                page = page_number if is_pdf else None
                bill = reader.read(path, page)
                record = bill_to_record(bill, path.name) if is_charge_page(bill) else None
                yield PageDone(path, page, record, bill)
        except Exception as error:  # noqa: BLE001 - report and keep going
            yield FileFinished(path, f"{type(error).__name__}: {error}")
            continue
        yield FileFinished(path)
