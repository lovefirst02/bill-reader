"""An example plugin: a landlord's PDF listing each tenant's receipt.

Copy it next to sites.csv, change it for your landlord's bill, and name it in
the site's Plugin column. Try it before watching:

    TaipowerBillReaderCLI.exe plugin-test landlord.py bill.pdf

The values below are made up.
"""

import re

import pdfplumber

TAX_ID = "12345678"   # our company's 統一編號 on the landlord's receipt rows
UNIT = "A棟3F"        # a name for this lease, kept the same every month: it stands for 電號


def _cells(path):
    """Every table row on every page, as text."""
    with pdfplumber.open(path) as pdf:
        return [[(cell or "").replace("\n", " ").strip() for cell in row]
                for page in pdf.pages for table in page.extract_tables() for row in table if any(row)]


def _number(text):
    text = re.sub(r"[,\s]", "", text or "")
    if not text:
        return None
    return float(text) if "." in text else int(text)


def _right_of(rows, keyword):
    """The number in the cell right of the first cell holding ``keyword``."""
    for row in rows:
        for i, cell in enumerate(row[:-1]):
            if keyword in cell:
                return _number(row[i + 1])
    return None


def _period(rows):
    """The first date range in the tables, as 114/10/01~114/10/31."""
    for row in rows:
        found = re.search(r"(\d{3,4})[/.-](\d{1,2})[/.-](\d{1,2})\s*[~～-]\s*(\d{3,4})[/.-](\d{1,2})[/.-](\d{1,2})", " ".join(row))
        if found:
            y1, m1, d1, y2, m2, d2 = (int(part) for part in found.groups())
            roc = lambda y: y - 1911 if y > 1911 else y  # noqa: E731
            return f"{roc(y1):03d}/{m1:02d}/{d1:02d}~{roc(y2):03d}/{m2:02d}/{d2:02d}"
    return None


def read(path, site):
    if path.suffix.lower() != ".pdf":
        return None
    rows = _cells(path)
    header = next((i for i, row in enumerate(rows) if any("收據編號" in cell for cell in row)), None)
    if header is None:
        return None  # not this landlord's bill: read it as a Taipower bill

    period = _period(rows)
    if period is None:
        raise ValueError("找不到計費期間")
    price = _right_of(rows, "電費單價")
    pretax = total = 0
    # 收據編號、公司名稱、統一編號、戶別、未稅金額、稅額、電費總計
    for row in rows[header + 1:]:
        if len(row) >= 7 and row[2] == TAX_ID:
            pretax += _number(row[4]) or 0
            total += _number(row[6]) or 0
    if not pretax:
        raise ValueError(f"找不到統一編號 {TAX_ID} 的收據列")

    return [{
        "Billing Period 計費期間": period,
        "Custom No. 電號": f"房東-{UNIT}",
        "稅前應繳總金額": pretax,
        "應繳總金額": total,
        # The landlord prints no kWh: worked out from the amount, in a column of our choosing.
        "計費度數_尖峰度數": round(pretax / price, 2) if price else None,
        "需人工檢查": f"房東帳單：度數＝未稅金額 {pretax:,} ÷ 電費單價 {price}",
    }]
