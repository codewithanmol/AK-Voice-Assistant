@echo off
title AK Mobile Server
cd /d "%~dp0"
echo ==============================================
echo   AK MOBILE SERVER - phone se AK se baat karo
echo ==============================================
echo.
rem Agar server pehle se chal raha hai (autostart se) to dobara mat chalao
netstat -ano | findstr /r /c:":4097 " | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 (
    echo Server pehle se chal raha hai (port 4097 busy).
    echo Phone app me URL daalo aur password encrypted vault me hai.
    pause
    exit /b 0
)
echo Server LAN pe khulega - ipconfig se apna IP dekho (192.168.x.x).
echo Remote access ke liye Tailscale (100.x.x.x) bhi chalega.
echo Password pehli baar console pe print hoga; baad me dekhne ke liye:
echo   type security\server_secret.txt
echo.
python ak_server.py --port 4097
echo.
pause