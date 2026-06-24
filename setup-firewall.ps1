# One-time setup: allow inbound TCP 8501 so other devices on your LAN can reach
# the dashboard. Run this once as Administrator.
#
# To run: right-click PowerShell -> "Run as Administrator", then:
#   cd D:\Bintang\RIGA
#   .\setup-firewall.ps1
#
# To undo: Remove-NetFirewallRule -DisplayName "RIGA Dashboard"

$ErrorActionPreference = "Stop"

# Self-elevate if not already admin
$current = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $current.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Not running as Administrator. Re-launching with elevation..." -ForegroundColor Yellow
    Start-Process -FilePath "powershell.exe" -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"" -Verb RunAs
    exit
}

$ruleName = "RIGA Dashboard"
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Firewall rule '$ruleName' already exists. Nothing to do." -ForegroundColor Green
} else {
    New-NetFirewallRule `
        -DisplayName $ruleName `
        -Description "Allow inbound TCP 8501 for the RIGA Streamlit trading dashboard." `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort 8501 `
        -Profile Private, Domain | Out-Null
    Write-Host "Created firewall rule '$ruleName' for TCP 8501 (Private + Domain profiles)." -ForegroundColor Green
    Write-Host "Other devices on your LAN can now reach the dashboard at http://<this-machine-ip>:8501" -ForegroundColor Green
}

Write-Host ""
Read-Host "Done. Press Enter to close"
