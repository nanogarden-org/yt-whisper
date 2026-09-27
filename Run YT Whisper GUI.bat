@echo off
setlocal

rem Launch the GUI from this folder without leaving a console window open.
cd /d "%~dp0"

where pythonw.exe >nul 2>&1
if not errorlevel 1 (
    start "YT Whisper GUI" /b pythonw.exe "%~dp0yt_whisper_gui.py"
    exit /b 0
)

where pyw.exe >nul 2>&1
if not errorlevel 1 (
    start "YT Whisper GUI" /b pyw.exe -3 "%~dp0yt_whisper_gui.py"
    exit /b 0
)

echo Python was not found on PATH.
echo Install Python and enable "Add Python to PATH", then try again.
pause
exit /b 1
