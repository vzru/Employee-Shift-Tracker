# Build the production Tailwind stylesheet using the STANDALONE Tailwind CLI
# (no Node/npm). Run from the repo root:  .\tools\build_css.ps1
# Output: app/static/css/tailwind.css  (committed, so the exe build needs no CLI)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    & ".\tools\tailwindcss.exe" `
        -c ".\tailwind.config.js" `
        -i ".\app\static\src\input.css" `
        -o ".\app\static\css\tailwind.css" `
        --minify
    Write-Host "Built app/static/css/tailwind.css"
} finally {
    Pop-Location
}
