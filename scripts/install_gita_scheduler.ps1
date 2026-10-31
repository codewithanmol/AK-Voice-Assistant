<#
.SYNOPSIS
  Installs or removes the daily Bhagavad Gita scheduled task.
.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_gita_scheduler.ps1 -Install
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_gita_scheduler.ps1 -Remove
#>

param(
    [switch]$Install,
    [switch]$Remove,
    [string]$Time = "08:00",
    [string]$TaskName = "AK_DailyGita"
)

$ProjectDir = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$TaskFile = Join-Path $ProjectDir "scripts\run_gita_daily.bat"

if ($Remove) {
    Write-Host "Removing scheduled task '$TaskName'..." -ForegroundColor Yellow
    schtasks /DELETE /TN $TaskName /F 2>$null
    Write-Host "Removed." -ForegroundColor Green
    return
}

if (-not (Test-Path -LiteralPath $TaskFile)) {
    Write-Host "[ERROR] run_gita_daily.bat not found at: $TaskFile" -ForegroundColor Red
    exit 1
}

Write-Host "Installing scheduled task '$TaskName' at $Time daily..." -ForegroundColor Cyan
Write-Host "    Task: $TaskFile" -ForegroundColor Gray

try {
    schtasks /CREATE /SC DAILY /TN $TaskName /TR "`"$TaskFile`"" /ST $Time /F
    Write-Host "Scheduled task installed! Runs daily at $Time." -ForegroundColor Green
    Write-Host "    Use 'schtasks /RUN /TN $TaskName' to test it now." -ForegroundColor Yellow
} catch {
    Write-Host "[ERROR] Failed to create task: $_" -ForegroundColor Red
}
