# Build TaipowerBillReader.exe into dist\TaipowerBillReader (folder + zip).
# Usage (from the project root):  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

uv sync --group dev
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
uv run python scripts\fetch_models.py
if ($LASTEXITCODE -ne 0) { throw "fetching the models failed" }

Write-Host "==> Running PyInstaller"
uv run pyinstaller packaging\reader.spec --noconfirm --clean --distpath dist --workpath build\pyinstaller
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$app = Join-Path $root "dist\TaipowerBillReader"
$target = Join-Path $app "models"
New-Item -ItemType Directory -Force $target | Out-Null
foreach ($name in (Get-Content models.lock.json -Raw | ConvertFrom-Json).files.PSObject.Properties.Name) {
    Copy-Item (Join-Path $root "models\$name") $target -Force
}

$version = (Select-String -Path src\bill_reader\__init__.py -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
$zip = Join-Path $root "dist\TaipowerBillReader-$version.zip"
if (Test-Path $zip) { Remove-Item $zip }
Start-Sleep -Seconds 5  # antivirus may still hold the fresh files
Compress-Archive -Path $app -DestinationPath $zip
Write-Host "Done: $zip"
