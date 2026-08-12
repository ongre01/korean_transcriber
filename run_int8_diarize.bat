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

if not exist "%~dp0models\diarization\sherpa-onnx-pyannote-segmentation-3-0\model.onnx" (
    echo [INFO] Speaker diarization models missing. Downloading now...
    ".venv\Scripts\python.exe" download_models.py diarization
    if errorlevel 1 goto :failed
)

if "%~2"=="" (
    set "SPEAKERS=-1"
) else (
    set "SPEAKERS=%~2"
)

if "%~1"=="" (
    ".venv\Scripts\python.exe" transcribe_npu.py --device NPU --beams 1 --model-dir "%MODEL%" --model-label INT8 --output-suffix "_int8_diarized" --diarize --num-speakers %SPEAKERS%
) else (
    ".venv\Scripts\python.exe" transcribe_npu.py "%~1" --device NPU --beams 1 --model-dir "%MODEL%" --model-label INT8 --output-suffix "_int8_diarized" --diarize --num-speakers %SPEAKERS%
)

echo.
pause
exit /b %errorlevel%

:failed
echo [ERROR] Model preparation failed.
pause
exit /b 1
