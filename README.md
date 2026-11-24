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

在一台常開的電腦上，用 OneDrive 把各 Site 的 Electricity 資料夾同步到本機，再寫一個 `sites.csv`（exe 的 zip 裡附了一份範例，改成自己的路徑、`Enabled` 改成 `Y` 才會監看）：

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
- 新帳單讀完、2 分鐘（`--quiet`）沒有再讀到新的，就寫進主表。檔案持續進來時，每 30 分鐘（`--write-every`）也寫一次，不必等全部讀完。

主表可以直接改：

- 主表不存在時程式會建立；自己先建的 Excel 沒有 `Bills` 工作表或是空的，程式會加上欄位名稱，其他工作表不動。
- 程式只在 `Bills` 最後**新增**列，不會改動已經在主表的列。人工改的值保留；人工刪掉的列不會再加回來。
- 每列用 `ID`（來源檔案內容的 hash 加頁碼）認列，所以改了電號或計費期間也不會重複新增。欄位用名稱找，可以調換順序或加自己的欄位；程式的欄位被改名或刪除時，這份主表停止寫入並記到 log。
- 來源檔內容改了（例如加了螢光筆）會重讀，但不新增列：同一個 Site、路徑和頁碼就是同一列。重讀的值和第一次讀到的不同時，才在 `Changes` 列為「來源檔已更新」。
- `電號 + 計費期間` 已在主表的帳單不新增，列在 `Changes` 工作表。人工改過的值也列在 `Changes`，附上程式原本讀到的值。`Changes` 和 `Files`（每個檔案的狀態）由程式每次重寫，不要在裡面編輯。
- 主表在 Excel 開著時不寫，5 分鐘（`--retry`）後再試。每次寫入前備份，保留最近 10 份。
- 主表不要放圖表、樞紐分析表或圖片：寫入時會遺失。需要的話另開一份 Excel 引用主表。
- 同一份主表只能由一台電腦寫，否則 OneDrive 會產生衝突檔。在 SharePoint 網頁上編輯時程式察覺不到，剛好同時寫入仍可能產生衝突檔。

### 特殊的 Site：用自己寫的 plugin 讀

不是台電帳單的 Site（例如房東轉來的電費單），可以自己寫一個 `.py` 檔來讀，在 `sites.csv` 加一欄 `Plugin` 指向它（相對路徑以 `sites.csv` 所在的資料夾為準）：

```csv
Site,Folder,Master,Enabled,Plugin
Leased,C:\Users\me\Company\Leased - Electricity,C:\Users\me\Company\Energy\master.xlsx,Y,landlord.py
```

檔案裡要有 `read(path, site)`，回傳值決定怎麼處理這個檔案：

| 回傳 | 意思 |
|---|---|
| `None` | 不是這個 plugin 的檔案，照台電帳單讀 |
| `[]` | 是這個 plugin 的檔案，但沒有帳單 |
| `[{...}, ...]` | 一個 dict 一張帳單，key 用主表的欄名 |
| 丟出例外 | 這個檔案讀失敗，錯誤和 plugin 的行號寫進 `Files` 和 log，之後重試 |

- 每一列一定要有 `Billing Period 計費期間`（例如 `114/10/01~114/10/31`）和 `Custom No. 電號`：主表靠這兩個欄位判斷是不是同一張帳單。沒有台電電號的帳單，自己定一個每個月都一樣的名稱，例如 `房東-A棟3F`。
- 金額和度數放數字，不要放 `"1,234"` 這種文字。沒有的欄位不放，寫進主表時是 0。只有總度數時，自己挑一欄放，例如 `計費度數_尖峰度數`。
- 一個檔案有好幾張帳單時，每次讀出來的順序要一樣，或自己給 `頁碼`。`ID`、`Site`、`來源檔案`、`讀取時間` 由程式填。
- 台電帳單的推算（預繳餘額、稅前、其他調整費用）不套用在 plugin 的列：給什麼就寫什麼。
- 主表沒有的欄名（例如 `電費單價`），要自己在主表加上這一欄才會寫入。
- 有 plugin 的 Site 也會看 `.xlsx`、`.xlsm`、`.xls`、`.csv`；plugin 不讀的就記為「沒有帳單頁」。
- exe 用它裡面的 Python 執行 plugin，所以跑監看的電腦不用裝 Python；plugin 只能 import 標準函式庫和 exe 裡有的套件：pdfplumber、pandas、numpy、openpyxl、Pillow、opencv。exe 裡的 pandas 是 3.x，和自己電腦上的版本可能不同，寫好後用 `plugin-test` 在 exe 上跑一次。
- 同一個檔案每次要讀出一樣的結果；不要修改或搬移帳單檔、不要自己寫主表、不要用 `input()` 或連網。要記 log 用 `logging.getLogger("bill_reader.plugin")`，會寫進 `watch.log`。
- 改了 plugin，已經讀過的檔案不會重讀。
- plugin 不要放在 SharePoint 同步的帳單資料夾裡：能改那個資料夾的人，就能在這台電腦上執行程式。

exe 的 zip 裡附了一份範例 `plugin_example.py`。開始監看前，先用 `plugin-test` 看看讀出什麼（不會寫任何東西）：

```powershell
TaipowerBillReaderCLI.exe plugin-test landlord.py 房東帳單.pdf
```

### 在帳單上自動標記（證據）

`sites.csv` 加一欄 `Evidence`，讀完帳單後，用螢光筆標出寫進主表的值，旁邊加上紅字標籤，查證時可以從主表追回帳單上的位置：

| `Evidence` 填 | 效果 |
|---|---|
| 留空或 `N` | 不標記 |
| `Y` | PDF 直接標在來源檔上；照片另存成 PDF，放在主表旁的 `Evidence\<Site>\` |
| 資料夾路徑 | 來源檔不動，每個檔案另存一份標好的 PDF 到這個資料夾 |

```csv
Site,Folder,Master,Enabled,Plugin,Evidence
North,C:\Users\me\Company\North - Electricity,C:\Users\me\Company\Energy\master.xlsx,Y,,Y
South,C:\Users\me\Company\South - Electricity,C:\Users\me\Company\Energy\master.xlsx,Y,,C:\Users\me\Company\Energy\Evidence\South
```

- 標記：計費期間（標籤用西元日期，例如 `Billing period 2025-01-01 to 2025-01-31`）、稅前應繳總金額（帳單沒印稅前時標應繳總金額）、計費度數和轉供度數的每一列。
- 帳單沒印的值，標出算式用到的數字，標籤寫出算式。例如用預繳餘額扣款的帳單：`Grey Electricity Cost 3,500 = 3,000 + 500 prepaid used`，並標出上期預繳餘額；沒印本期預繳餘額時，頁面上方註明視為 0。
- 標記是 PDF 註解，不是畫在頁面上：用 Acrobat Reader 可以移動、調整大小、刪除或改字。Edge、Chrome 看得到，但通常改不了。標籤用英文，避免閱讀器缺中文字型。
- 直接標在來源檔時，標記附加在檔案尾端，原本的內容一個位元組都不動；改之前另存一份原檔在 `%LOCALAPPDATA%\BillReader\watch-…\originals\`，SharePoint 的版本記錄也留著原本的版本。標過的檔案不會被當成新檔案重讀，已經有程式標記的檔案不再標第二次。
- 來源檔在 Acrobat 裡開著、或讀完後又被改過時，先不標，下一輪再試。
- 另存的證據檔被人改過（例如移動了標記），來源檔更新時不覆蓋。
- 找不到位置的值不標，寫在 `Files` 工作表的「證據」欄；那一欄也寫每個檔案標好了沒有。
- 證據資料夾不能在監看的資料夾裡面，否則證據檔會被當成新帳單。執行監看的帳號要有來源資料夾的寫入權限。
- plugin 讀的帳單（例如房東帳單）不標記。

資料庫、`watch.log`、主表的備份和標記前的原檔放在 `%LOCALAPPDATA%\BillReader\watch-…`（`--state` 可改），不放在同步資料夾裡。

用 exe 時，兩個 exe 都接受 `watch`。視窗版 `TaipowerBillReader.exe` 不開主控台，適合在背景執行，訊息只寫進 `watch.log`。
要登入就開始，用 Task Scheduler 設成「登入時」執行，不要做成 Windows 服務：OneDrive 只在使用者登入後同步。

```powershell
schtasks /Create /TN "Taipower Bill Watch" /SC ONLOGON /RL LIMITED /TR "\"C:\Apps\TaipowerBillReader\TaipowerBillReader.exe\" watch \"C:\Apps\sites.csv\""
```

停止：工作管理員結束 `TaipowerBillReader.exe`，或 `schtasks /End /TN "Taipower Bill Watch"`。讀到一半停掉沒關係，下次會從沒讀完的檔案繼續。

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
