@echo off
cd /d "%~dp0"
echo AK MIC TEST - local word-by-word STT (Vosk, offline, no cloud)
echo.
echo NOTE: jaan-boojh ke captions AK ke visualizer ke ANDAR automatically
echo chalti hain jab AK start hota hai. Ye window sirf mic/Vosk ki checking
echo ke liye hai. Word-by-word test ke liye sirf bolna shuru karo.
echo Press Ctrl+C to stop.
echo.
python AK\caption_demo.py %*
echo.
pause