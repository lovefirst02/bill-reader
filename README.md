# bill-reader

把台電帳單（PDF、圖片）讀成 CSV。只用 CPU，不需要 Paddle、Windows OCR 或 GPU。

模型在 [bill-models](https://github.com/lovefirst02/bill-models) 訓練和發佈。這個 Repo 只有讀帳單需要的程式。
網頁版、桌面版和綠電功能在 [taipower-bill-ocr](https://github.com/lovefirst02/taipower-bill-ocr)。

## 開始

需要 [uv](https://docs.astral.sh/uv/)。

```powershell
uv sync
uv run python scripts/fetch_models.py
uv run python -m bill_reader 帳單資料夾 -o bills.csv --details
```

- `bills.csv`：每張帳單一列。**請加 `--details`**，否則沒有「需人工檢查」欄，讀不到的 0 和帳單上真的 0 無法分辨。
- `bills.log`：每頁走哪條路線、為什麼、讀到什麼。內容包含完整帳單資料，不要傳到公開的地方。

## 一頁怎麼被讀

```
PDF ──有可信的文字──→ pdf_text.py ──有缺值──→ 模型只補空格
 │                        │
 │                        └─完整──────────────┐
 └─沒有或不可信─┐                              ▼
圖片 ───────────┴→ page.py + engine.py → parser.py → record.py → cli.py → CSV
                   找區塊、偵測、讀字     標籤配數值    檢查、標記
```

## 要改什麼，看哪裡

| 想改的事 | 檔案 |
|---|---|
| 輸出欄位、CSV、Log | `cli.py` |
| 「需人工檢查」的規則、讀不到寫 0 | `record.py` |
| 認得新的標籤、版面、把值配給欄位 | `parser.py`（最大的檔案，用 `_parse_detailed_high_voltage_layout` 等版面函式找） |
| PDF 文字路線、什麼時候信任文字 | `pdf_text.py` |
| 頁面流程、區塊太窄、重讀漏掉的列 | `page.py` |
| 依顏色找區塊 | `blocks.py` |
| 跑模型、圖片縮放 | `engine.py` |
| 模型認得哪些字和標籤 | `vocabulary.py` |
| 檔案和頁面的順序、走哪條路線 | `reader.py` |

## 改完怎麼確認

```powershell
uv run python -m unittest discover -s tests
```

有真實帳單和正解時，比較改動前後（帳單和正解不要放進 Git，`samples/` 已被忽略）：

```powershell
uv run python evaluation/read_bills.py before.json 帳單1.png 帳單2.png
uv run python evaluation/read_bills.py after.json 帳單1.png 帳單2.png
uv run python evaluation/stats_compare.py out.md samples/truth_rows.json A=before.json B=after.json
```

- 測試資料要用假值，不要把真實電號、地址或金額寫進程式或測試。
- 看錯誤時，用 `bills.log` 的「路線」和「讀到」判斷是模型讀錯，還是 `parser.py` 配錯。

## 換模型

1. 在 `bill-models` 發佈新版。
2. 改 `models.lock.json` 的版本和 SHA-256（新版的 `models.json` 裡有）。
3. 重新執行 `scripts/fetch_models.py`，再做上面的比較，確實比較好才換。

`vocabulary.py` 多了模型沒學過的字時，要回 `bill-models` 重新訓練。

## 打包成 exe

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

輸出在 `dist/`。版本號只改 `src/bill_reader/__init__.py`。

## 已知限制

- 只支援台電帳單。
- 中文文字層是亂碼、掃描圖，或文字被畫成線條的 PDF，只能靠模型，比較慢。
- 表格版的截圖或掃描件仍然很弱，會標記需人工檢查。
- 文字路線只檢查欄位齊全和總額相符，文字對錯欄位、總額又對得上時不會被發現。
- 讀不到的數字會寫成 0，加總前請先看標記。
