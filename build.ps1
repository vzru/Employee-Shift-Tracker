# Build the one-file Windows .exe.
#
#   .\build.ps1
#
# Steps:
#   1. Rebuild the Tailwind stylesheet (so bundled CSS is current).
#   2. Run PyInstaller against EmployeeShiftTracker.spec using the venv Python.
#
# Result: dist\EmployeeShiftTracker.exe  (single self-contained file).
# Copy that ONE file to the target PC and double-click it. On first run it
# creates a /data folder next to itself and opens the default browser.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    throw "venv not found. Create it: py -3.12 -m venv .venv; then pip install -r requirements.txt"
}

Write-Host "==> Building Tailwind CSS..." -ForegroundColor Cyan
& ".\tools\tailwindcss.exe" -c ".\tailwind.config.js" `
    -i ".\app\static\src\input.css" -o ".\app\static\css\tailwind.css" --minify

Write-Host "==> Cleaning previous build..." -ForegroundColor Cyan
# dist\data holds LIVE payroll data if the exe has ever been run in place.
# Stash it outside dist before wiping dist, then restore it after — never let
# a rebuild destroy real employee/shift data. The restore runs in `finally` so
# a mid-build failure (e.g. the exe still running and locked) can't strand the
# stash outside dist\data.
$dataStash = $null
if (Test-Path ".\dist\data") {
    $dataStash = Join-Path $env:TEMP ("EmployeeShiftTracker_data_" + [guid]::NewGuid())
    Move-Item ".\dist\data" $dataStash
}
try {
    if (Test-Path ".\build") { Remove-Item -Recurse -Force ".\build" }
    if (Test-Path ".\dist")  { Remove-Item -Recurse -Force ".\dist" }

    Write-Host "==> Running PyInstaller (one-file)..." -ForegroundColor Cyan
    & $py -m PyInstaller --noconfirm --clean ".\EmployeeShiftTracker.spec"
} finally {
    if ($dataStash -and (Test-Path $dataStash)) {
        New-Item -ItemType Directory -Force ".\dist" | Out-Null
        Move-Item $dataStash ".\dist\data" -Force
        Write-Host "==> Restored existing dist\data (preserved across rebuild)." -ForegroundColor Cyan
    }
}

$exe = ".\dist\EmployeeShiftTracker.exe"
if (Test-Path $exe) {
    $mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host ""
    Write-Host "BUILD OK -> $exe ($mb MB)" -ForegroundColor Green
    Write-Host "Copy that single file to the target PC and double-click it." -ForegroundColor Green
    if ($dataStash) {
        Write-Host "NOTE: dist\data was preserved. Do not overwrite it if you copy a fresh exe over an existing deployment folder." -ForegroundColor Yellow
    }
} else {
    throw "Build failed: $exe not found."
}
