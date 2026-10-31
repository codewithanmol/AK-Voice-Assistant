@echo off
REM ================================================
REM  Daily Bhagavad Gita & Bhagavatam — AK
REM  Run via Windows Task Scheduler (e.g. 8 AM)
REM ================================================
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\run_gita_daily.ps1" -NoVoice
