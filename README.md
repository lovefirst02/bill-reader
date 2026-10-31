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
 │                        └─完整─────────┐
 └─沒有或不可信─┐                         ▼
圖片 ───────────┴→ page.py + engine.py → parser.py → record.py → cli.py → CSV
                   轉正、偵測、讀字       標籤配數值    檢查、標記
```

## 要改什麼，看哪裡

| 想改的事 | 檔案 |
|---|---|
| 輸出欄位、CSV、Log | `cli.py` |
| 「需人工檢查」的規則、讀不到寫 0 | `record.py` |
| 一般版帳單：把值配給欄位、修正讀錯的值 | `parser.py` |
| 表格版帳單（基本資料／計費內容／用電資料） | `table_layout.py` |
| PDF 文字路線、什麼時候信任文字 | `pdf_text.py` |
| 頁面轉正、把畫面交給模型 | `page.py` |
| 跑模型、圖片縮放 | `engine.py` |
| 模型認得哪些字和標籤 | `vocabulary.py` |
| 檔案和頁面的順序、走哪條路線 | `reader.py` |

`parser.py` 不抽 CSV 用不到的欄位。要加新欄位時，先在 `_FIELDS` 和表格版的 `_CHARGES` 加標籤，再到 `record.py` 加欄位。

## 改完怎麼確認

```powershell
uv run python -m unittest discover -s tests
```

有真實帳單時，用 `evaluation/compare.py` 確認改動前後哪些值變了。`files.txt` 每行一個帳單檔案：

```powershell
uv run python evaluation/compare.py record tmp/ref.json tmp/files.txt   # 改之前
uv run python evaluation/compare.py check  tmp/ref.json tmp/files.txt   # 改之後
```

第一次要跑模型比較久，之後會用快取。`tmp/` 和 `samples/` 已被 Git 忽略，帳單都放那裡。

- 測試資料要用假值，不要把真實電號、地址或金額寫進程式或測試。
- 看錯誤時，用 `bills.log` 的「路線」和「讀到」判斷是模型讀錯，還是 `parser.py` 配錯。

## 換模型

1. 在 `bill-models` 發佈新版。
2. 改 `models.lock.json` 的版本和 SHA-256（新版的 `models.json` 裡有）。
3. 重新執行 `scripts/fetch_models.py`，再用 `compare.py` 比較，確實比較好才換。

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
