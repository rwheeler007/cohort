# BOSS Communications Service Launcher
# Usage: .\start_service.ps1

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BossRoot = (Resolve-Path "$ScriptDir\..\..").Path

Write-Host "[>>] Starting BOSS Communications Service..." -ForegroundColor Cyan
Write-Host "[*]  Service directory: $ScriptDir"
Write-Host "[*]  BOSS root: $BossRoot"

# Check for .env file
if (-not (Test-Path "$ScriptDir\.env")) {
    Write-Host "[!] No .env file found. Copy .env.example to .env and configure it." -ForegroundColor Yellow
    Write-Host "[*] Continuing with environment defaults..."
}

# Check Python
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "[X] Python not found in PATH" -ForegroundColor Red
    exit 1
}

# Check dependencies
Write-Host "[*] Checking dependencies..."
python -c "import fastapi, uvicorn" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] Missing dependencies. Installing from requirements.txt..." -ForegroundColor Yellow
    pip install -r "$ScriptDir\requirements.txt"
}

# Launch the service
Write-Host "[>>] Launching on http://localhost:8001" -ForegroundColor Green
Set-Location $ScriptDir
python service.py
