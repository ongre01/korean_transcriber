@echo off
chcp 65001 >nul
set PYTHONUTF8=1
setlocal
cd /d "%~dp0"

echo ==============================================================================
echo  Intel/OpenVINO Korean Transcriber v7 - setup
echo ==============================================================================
echo.
echo This setup prepares Whisper INT8/FP16 and speaker diarization models.
echo Existing models are reused and are not downloaded again.
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found in PATH.
    pause
    exit /b 1
)

echo Python:
python --version
echo.

echo [1/6] Creating/reusing virtual environment...
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto :failed
) else (
    echo       Existing .venv will be reused.
)

echo [2/6] Updating pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed

echo [3/6] Installing/updating runtime packages...
".venv\Scripts\python.exe" -m pip install --upgrade --upgrade-strategy eager -r requirements.txt
if errorlevel 1 goto :failed

echo [4/6] Checking OpenVINO devices...
".venv\Scripts\python.exe" -c "import openvino as ov; import openvino_genai; print('OpenVINO:', ov.__version__); print('Devices:', ov.Core().available_devices)"
if errorlevel 1 goto :failed

echo [5/6] Preparing INT8 and FP16 Whisper models...
".venv\Scripts\python.exe" download_models.py both
if errorlevel 1 goto :failed

echo [6/6] Preparing speaker diarization models...
".venv\Scripts\python.exe" download_models.py diarization
if errorlevel 1 goto :failed

echo.
echo ==============================================================================
echo  Setup complete.
echo  run_int8.bat          = NPU + INT8
echo  run_fp16.bat          = NPU + FP16
echo  run_int8_diarize.bat  = NPU ASR + NPU segmentation + CPU embedding/clustering
echo  run_fp16_diarize.bat  = NPU ASR + NPU segmentation + CPU embedding/clustering
echo  run_fp16_compat.bat   = NPU + FP16 with Level Zero memory workaround
echo  run_compare.bat       = INT8 then FP16 on one selected recording
echo ==============================================================================
pause
exit /b 0

:failed
echo.
echo [ERROR] Setup failed.
pause
exit /b 1
