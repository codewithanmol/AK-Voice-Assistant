<#
.SYNOPSIS
  Daily Bhagavad Gita & Shrimad Bhagavatam delivery by AK Voice.
  Run via Windows Task Scheduler every morning or on demand.
.DESCRIPTION
  Speaks today's Gita shloka + Bhagavatam story through AK TTS.
  Tracks state automatically in data/gita_state.json — never repeats.
.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_gita_daily.ps1
  python main.py --once "gita"        
  python main.py --once "bhagavatam"
#>

param(
    [switch]$NoVoice
)

$ScriptDir = Split-Path -Parent $PSCommandPath
$ProjectDir = Split-Path -Parent $ScriptDir
$PythonExe  = "python"
$MainScript = Join-Path $ProjectDir "main.py"

Write-Host "`n" -NoNewline
Write-Host "╔══════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║    ॐ   Daily Bhagavad Gita & Shrimad Bhagavatam — AK   ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan

if (-not $NoVoice) {
    Write-Host "`nSpeaking today's Gita shloka..." -ForegroundColor Yellow
    try {
        & $PythonExe -X utf8 $MainScript --once "gita" 2>&1 | ForEach-Object { Write-Host $_ -ForegroundColor Gray }
    } catch {
        Write-Host "[ERROR] Gita voice failed: $_" -ForegroundColor Red
        & $PythonExe -X utf8 $MainScript --once "gita (typing mode)"
    }

    Start-Sleep -Seconds 3

    Write-Host "`nSpeaking today's Bhagavatam story..." -ForegroundColor Yellow
    try {
        & $PythonExe -X utf8 $MainScript --once "bhagavatam story" 2>&1 | ForEach-Object { Write-Host $_ -ForegroundColor Gray }
    } catch {
        Write-Host "[ERROR] Bhagavatam voice failed: $_" -ForegroundColor Red
    }
} else {
    $gitaText = & $PythonExe -X utf8 -c @"
import sys
sys.path.insert(0, "$ProjectDir")
from AK.gita import build_gita_delivery, get_daily_bhagavatam_story
print(build_gita_delivery(detail='full'))
print()
from AK.gita import _format_story_for_console
story = get_daily_bhagavatam_story()
print(_format_story_for_console(story))
"@ 2>&1 | Out-String
    Write-Host $gitaText -ForegroundColor White
}

Write-Host "`nDaily delivery complete. Hare Krishna!" -ForegroundColor Green
