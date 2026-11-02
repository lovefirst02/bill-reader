# bill-reader

把台電帳單（PDF、圖片）讀成 CSV。只用 CPU，不需要 Paddle、Windows OCR 或 GPU。

模型在 [bill-models](https://github.com/lovefirst02/bill-models) 訓練和發佈。這個 Repo 只有讀帳單需要的程式。
網頁版、桌面版和綠電功能在 [taipower-bill-ocr](https://github.com/lovefirst02/taipower-bill-ocr)。

## 開始

需要 [uv](https://docs.astral.sh/uv/)。

```powershell
uv sync
uv run python scripts/fetch_models.py
uv run python -m bill_reader                                # 開視窗，選輸入和輸出
uv run python -m bill_reader 帳單資料夾 -o bills.csv         # 命令列
```

- `bills.csv`：每張帳單一列，預設包含「來源檔案」「頁碼」「需人工檢查」。命令列可加 `--no-details` 拿掉這三欄，但拿掉後，讀不到的 0 和帳單上真的 0 無法分辨。
- `bills.log`：每頁走哪條路線、為什麼、讀到什麼。內容包含完整帳單資料，不要傳到公開的地方。

## 監看 SharePoint 資料夾，寫進主表

在一台常開的電腦上，用 OneDrive 把各 Site 的 Electricity 資料夾同步到本機，再寫一個 `sites.csv`：

```csv
Site,Folder,Master,Enabled
North,C:\Users\me\Company\North - Electricity,C:\Users\me\Company\Energy\master.xlsx,Y
South,C:\Users\me\Company\South - Electricity,C:\Users\me\Company\Energy\master.xlsx,Y
```

```powershell
uv run bill-reader watch sites.csv           # 一直監看
uv run bill-reader watch sites.csv --once    # 讀完新的、寫完主表就結束
```

- 一行一個 Site，子資料夾也會看。`Master` 相同的 Site 寫進同一份主表。`Enabled` 填 `N` 就略過這行。
- 每個 Site 一個掃描執行緒；帳單由同一個讀取器依序讀，模型只載入一次。
- 檔案要 30 秒（`--settle`）沒變動才讀，避免讀到 OneDrive 還沒下載完的檔案。搬移或改名、內容沒變的檔案不重讀。讀失敗的檔案最多試 3 次（`--attempts`）。
- 主表最快每 30 分鐘（`--write-every`）寫一次，減少和別人同時編輯撞在一起的機會。

主表可以直接改：

- 程式只在 `Bills` 最後**新增**列，不會改動已經在主表的列。人工改的值保留；人工刪掉的列不會再加回來。
- 每列用 `ID`（來源檔案內容的 hash 加頁碼）認列，所以改了電號或計費期間也不會重複新增。欄位用名稱找，可以調換順序或加自己的欄位；程式的欄位被改名或刪除時，這份主表停止寫入並記到 log。
- 來源檔內容改了（例如加了螢光筆）會重讀，但不新增列：同一個 Site、路徑和頁碼就是同一列。重讀的值和第一次讀到的不同時，才在 `Changes` 列為「來源檔已更新」。
- `電號 + 計費期間` 已在主表的帳單不新增，列在 `Changes` 工作表。人工改過的值也列在 `Changes`，附上程式原本讀到的值。`Changes` 和 `Files`（每個檔案的狀態）由程式每次重寫，不要在裡面編輯。
- 主表在 Excel 開著時不寫，下一輪再試。每次寫入前備份，保留最近 10 份。
- 主表不要放圖表、樞紐分析表或圖片：寫入時會遺失。需要的話另開一份 Excel 引用主表。
- 同一份主表只能由一台電腦寫，否則 OneDrive 會產生衝突檔。在 SharePoint 網頁上編輯時程式察覺不到，剛好同時寫入仍可能產生衝突檔。

資料庫、`watch.log` 和備份放在 `%LOCALAPPDATA%\BillReader\watch-…`（`--state` 可改），不放在同步資料夾裡。
要登入就開始，用 Task Scheduler 設成「登入時」執行，不要做成 Windows 服務：OneDrive 只在使用者登入後同步。

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
| 視窗 | `gui.py` |
| 監看資料夾、設定檔、讀過什麼 | `watch.py` |
| 主表怎麼寫、人工修改和重複帳單 | `master.py` |
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

輸出在 `dist/`：`TaipowerBillReader.exe` 是視窗版，`TaipowerBillReaderCLI.exe` 是命令列版。版本號只改 `src/bill_reader/__init__.py`。

## 已知限制

- 只支援台電帳單。模型讀的頁面要認出至少 2 個台電帳單用語（`reader.py` 的 `MIN_LABELS`）才算電費頁，水費單、通知之類會略過；很模糊、只認出 1 個用語的台電帳單也會被略過，`bills.log` 和主表的 `Files` 會寫出來。
- 中文文字層是亂碼、掃描圖，或文字被畫成線條的 PDF，只能靠模型，比較慢。
- 表格版的截圖或掃描件仍然很弱，會標記需人工檢查。
- 文字路線只檢查欄位齊全和總額相符，文字對錯欄位、總額又對得上時不會被發現。
- 讀不到的數字會寫成 0，加總前請先看標記。
