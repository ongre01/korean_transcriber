@echo off
chcp 65001 >nul
set PYTHONUTF8=1
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Run setup.bat first.
    pause
    exit /b 1
)
if "%~1"=="" (
    ".venv\Scripts\python.exe" run_compare.py
) else (
    ".venv\Scripts\python.exe" run_compare.py "%~1"
)
echo.
pause
