@echo off
title AK Voice Assistant (auto-start)
rem ---------------------------------------------------------------------
rem Launcher used by the Windows scheduled task "AK Voice Assistant".
rem Starts AK in always-listening mode and writes everything to
rem logs\ak_autostart.log so a failure can be read after the fact.
rem ---------------------------------------------------------------------
cd /d "%~dp0"
set AK_NOPAUSE=1
if not exist logs mkdir logs
set "LOG=logs\ak_autostart.log"

set "PY="
where python >nul 2>nul
if %errorlevel%==0 set "PY=python"
if not defined PY (
    where py >nul 2>nul
    if %errorlevel%==0 set "PY=py -3"
)
if not defined PY (
    echo [%date% %time%] [x] Python not found - AK cannot start.>>%LOG%
    exit /b 1
)

rem ---------- dependencies: only touch pip when something is missing ----------
%PY% -c "import sounddevice, numpy, edge_tts, faster_whisper, vosk, speech_recognition" >nul 2>nul
if errorlevel 1 (
    echo [%date% %time%] missing dependency, installing from requirements.txt...>>%LOG%
    rem pyaudio needs portaudio to build and is not used, so drop it.
    %PY% -c "import pathlib; p=pathlib.Path('requirements.txt'); ls=[l.rstrip() for l in p.read_text(encoding='utf-8').splitlines() if l.strip() and not l.lstrip().lower().startswith(('#','pyaudio'))]; pathlib.Path('ak_reqs_tmp.txt').write_text(chr(10).join(ls), encoding='utf-8')" >>%LOG% 2>&1
    %PY% -m pip install --quiet --disable-pip-version-check -r ak_reqs_tmp.txt >>%LOG% 2>&1
    if exist ak_reqs_tmp.txt del ak_reqs_tmp.txt
)

echo [%date% %time%] AK starting (always listening)...>>%LOG%
%PY% main.py --no-wake >>%LOG% 2>&1
echo [%date% %time%] AK stopped.>>%LOG%
exit /b 0
