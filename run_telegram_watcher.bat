@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Project virtual environment was not found.
    echo Run: python -m venv .venv
    echo Then run: .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

REM Display Dua using PowerShell for proper Arabic text handling
PowerShell -NoProfile -Command "Get-Content -Path '%~dp0dua.txt' -Encoding UTF8 | Write-Host"
echo.
echo.
set /p dummy="Press ENTER to start the watcher..."
echo.

".venv\Scripts\python.exe" -u app.py --interval 30 --telegram-only
if errorlevel 1 (
    echo.
    echo Watcher stopped with an error.
    pause
    exit /b 1
)
