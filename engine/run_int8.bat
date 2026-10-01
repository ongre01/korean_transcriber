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
set "MODEL=%~dp0models\whisper-large-v3-turbo-int8"
if not exist "%MODEL%\openvino_encoder_model.xml" (
    echo [INFO] INT8 model missing. Downloading now...
    ".venv\Scripts\python.exe" download_models.py int8
    if errorlevel 1 goto :failed
)
if "%~1"=="" (
    ".venv\Scripts\python.exe" transcribe_npu.py --device NPU --beams 1 --model-dir "%MODEL%" --model-label INT8 --output-suffix "_int8"
) else (
    ".venv\Scripts\python.exe" transcribe_npu.py "%~1" --device NPU --beams 1 --model-dir "%MODEL%" --model-label INT8 --output-suffix "_int8"
)
echo.
pause
exit /b %errorlevel%
:failed
echo [ERROR] INT8 model preparation failed.
pause
exit /b 1
