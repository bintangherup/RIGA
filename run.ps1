$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# --- venv setup ---
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    python -m venv .venv
}

# --- install / sync deps (quiet) ---
& .\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
& .\.venv\Scripts\pip.exe install -q -r requirements.txt

# --- ensure playwright chromium is installed (one-time, ~300MB) ---
$chromiumMarker = ".venv\.playwright-chromium-installed"
if (-not (Test-Path $chromiumMarker)) {
    Write-Host "Installing Playwright Chromium (one-time, ~300MB)..." -ForegroundColor Yellow
    & .\.venv\Scripts\python.exe -m playwright install chromium
    if ($LASTEXITCODE -eq 0) {
        New-Item -ItemType File -Path $chromiumMarker -Force | Out-Null
    } else {
        Write-Host "Playwright install failed - /scrape-x will not work until resolved." -ForegroundColor Red
    }
}

# --- discover LAN IPs ---
$ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' -and $_.PrefixOrigin -in 'Dhcp', 'Manual' } |
    Select-Object IPAddress, InterfaceAlias

Write-Host ""
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "  RIGA Trading Dashboard" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "  This machine:  http://localhost:8501" -ForegroundColor Green
foreach ($entry in $ips) {
    $url = "http://$($entry.IPAddress):8501"
    Write-Host ("  {0,-13} {1}" -f ($entry.InterfaceAlias + ":"), $url) -ForegroundColor Green
}
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host ""

# --- firewall hint if rule missing ---
$fwRule = Get-NetFirewallRule -DisplayName "RIGA Dashboard" -ErrorAction SilentlyContinue
if (-not $fwRule) {
    Write-Host "NOTE: No firewall rule found for port 8501." -ForegroundColor Yellow
    Write-Host "      LAN devices will probably be blocked until you run (one-time, as admin):" -ForegroundColor Yellow
    Write-Host "        .\setup-firewall.ps1" -ForegroundColor Yellow
    Write-Host ""
}

# --- launch streamlit, explicitly bound to all interfaces ---
& .\.venv\Scripts\streamlit.exe run app.py `
    --server.address 0.0.0.0 `
    --server.port 8501 `
    --server.headless true `
    --browser.gatherUsageStats false
