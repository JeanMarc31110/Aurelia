$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=========================================="
Write-Host "       INSTALLATION AURELIA V5"
Write-Host "=========================================="

$python = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $python = "py"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $python = "python"
}

if (-not $python) {
    Write-Host "Python n'est pas installe."
    Start-Process "https://www.python.org/downloads/windows/"
    Read-Host "Installez Python puis relancez ce programme. Appuyez sur Entree"
    exit 1
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

if (-not (Test-Path ".venv")) {
    & $python -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\pip.exe" install -r requirements.txt

Write-Host ""
Write-Host "Installation terminee."
Write-Host "Lancez LANCER_AURELIA.bat"
Read-Host "Appuyez sur Entree"
