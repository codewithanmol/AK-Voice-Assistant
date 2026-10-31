@echo off
title AK - gesture control only
cd /d "%~dp0"

echo.
echo   AK  ::  holographic gesture control (voice assistant ke bina)
echo   ------------------------------------------
echo   Point (two fingers) to aim   Pinch to click
echo   Fist to zoom   Swipe to switch structure
echo   Peace to open the command wheel
echo.
echo   Ctrl+Alt+J  vision panel      Ctrl+Alt+K  quit
echo.

python AK\ak_ui.py
if errorlevel 1 (
  echo.
  echo   AK stopped with an error. Log: D:\YOUR_DATA_PATH\ak\ak.log
  pause
)
