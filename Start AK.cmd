@echo off
title AK - voice assistant
cd /d "%~dp0"

echo.
echo   AK  ::  voice assistant
echo   ------------------------------------------
echo   Sirf voice assistant chalega. Holographic gesture control
echo   alag se on demand chalta hai - jab chahiye bolo:
echo.
echo   "ak chalao"  /  "ak on"     -> gesture control + camera panel ON
echo   "ak band"    /  "ak off"    -> sab band
echo   "ak exit"                   -> AK band
echo.
echo   Visualizer (sand window) voice se react karta hai.
echo.

python main.py
if errorlevel 1 (
  echo.
  echo   AK stopped with an error. Log: D:\YOUR_DATA_PATH\ak\ak.log
  pause
)
