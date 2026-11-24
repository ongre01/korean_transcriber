# Nemotron 엔진 사용 가이드

이 프로젝트는 기본 Whisper 엔진과 별도로 **NVIDIA Nemotron 3.5 Transcribe 30B-A4B**를 선택할 수 있다. Qt UI와 Python Backend를 유지하면서 로컬 NeMo-Speech.cpp 실행 파일을 호출한다. 이 문서의 확인 기준일은 **2026-11-05**이다. 현재 PC의 런타임 설치와 짧은 음성 추론 검증은 아래의 현재 PC 검증 결과에 기록했다.

## 지원 범위

한국어 전사, 화자 분리, 단어 시간 정보를 기존 결과 화면과 TXT/SRT 출력에 연결한다. 모델은 한국어를 포함한 40개 언어를 지원하고 한 요청에서 최대 1시간의 녹음을 처리한다. [공식 모델 카드](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B)

프로젝트 기본 8GB 설정은 요청별 길이를 29분으로 제한하며, 더 긴 파일은 Python Backend가 자동으로 나누어 순차 전사한 뒤 합친다. 녹음이 끝난 파일을 처리하므로 녹음 중 실시간 자막 용도로 사용하지 않는다. 모델이 제공하는 번역·오디오 질의 기능은 이번 엔진 선택 기능의 범위에 포함하지 않는다.

## 현재 PC와 기본 프리셋

확인한 개발 PC는 Windows, RTX 5060 Laptop GPU 8GB VRAM, 시스템 RAM 32GB급이다. GPU에 모델 전체를 올리는 대신 IQ4_NL 양자화 decoder와 CPU/RAM 오프로딩을 사용하는 구성을 제공한다.

[프로젝트 프리셋](../engine/nemotron_8gb.yaml)은 다음 설정을 사용한다.

```yaml
asr:
  salm:
    max_sequences: 1
    n_ctx_per_sequence: 32768
    max_context: 32768
    n_cpu_moe: 53
    n_batch: 512
    encoder_max_batch: 1
    encoder_ahead_seconds: 600
```

이는 공식 8GB 예시를 따른 출발점이다. 모든 MoE expert를 시스템 RAM에 두므로 CPU와 메모리 속도가 처리 시간에 영향을 준다. 32GB RAM에서의 실제 여유와 성능은 별도 측정이 필요하다. [공식 메모리 설정](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/salm/configuration.md)

기본 프리셋에서는 29분을 넘는 파일도 자동 분할로 처리한다. 사용자 YAML을 지정하면 요청별 최대 길이는 1시간이지만, 실제 처리 가능 길이는 그 설정의 메모리·맥락 한계에 따른다. 사용자 YAML을 지정하는 것만으로 메모리가 늘거나 긴 요청의 성공이 보장되지는 않는다. 한 번의 CLI 요청에 1시간을 넘는 음성을 보내지 않으며, 파일 전체 길이가 1시간을 넘으면 여러 요청으로 나눈다.

## 1. 네이티브 실행 파일 준비

NeMo-Speech.cpp의 **SALM을 지원하는 빌드**를 설치한다. 일반 소형 ASR만 지원하는 이전 실행 파일은 이 모델을 처리할 수 없다. Windows 네이티브 CLI와 CUDA 경로를 제공한다. 공식 설치 문서에서 Windows용 바이너리 또는 소스 빌드를 선택하고 설치 스크립트 내용을 확인한 후 진행한다. 소스 빌드는 Git, CMake, Ninja, Visual Studio 2022 Build Tools와 선택한 CUDA Toolkit/드라이버가 필요하다. [공식 Windows 설치 안내](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/install.md)

**기본 8GB 프리셋은 SALM 지원 외에 `n_cpu_moe` 지원도 필요하다.** 이 기능은 공식 커밋 `536b17a41bef62d2a67a40ed3df61df26a4389fe`에서 추가됐다. `0.3.0+nightly.d8a1abf`는 SALM을 지원하지만 `asr.salm.n_cpu_moe`를 알 수 없는 키로 거부한다. 2026-11-05 확인한 공개 nightly 바이너리도 아직 이 구버전이므로, nightly라는 이름만으로 호환성을 판단하지 않는다. 지원 커밋을 포함한 Windows CUDA 빌드를 사용한다. [CPU 오프로딩을 추가한 공식 변경](https://github.com/NVIDIA/NeMo-Speech.cpp/commit/536b17a41bef62d2a67a40ed3df61df26a4389fe)

`unknown key 'asr.salm.n_cpu_moe'` 오류는 모델이나 입력 음성의 문제가 아니라 런타임과 YAML의 호환성 문제다. 이 키를 삭제하면 8GB 프리셋의 CPU 오프로딩이 사라지므로 설정은 유지하고 실행 파일을 교체한다. 공식 소스의 GPU 빌드에는 CUDA Toolkit 12.8 이상이 필요하며, GPU 드라이버만 설치된 환경에는 컴파일러 `nvcc`가 없다. 모델 변환용 소스와 별도 폴더에서 런타임을 빌드할 수 있다. [공식 빌드 안내](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/536b17a41bef62d2a67a40ed3df61df26a4389fe/docs/build.md)

설치 후 새 PowerShell에서 실행 파일과 지원 옵션을 확인한다.

```powershell
nemo-speech --version
nemo-speech transcribe --help
```

PATH에 없다면 `nemo-speech.exe`의 전체 경로를 사용한다. 설치된 버전과 모델 변환에 사용한 저장소 버전을 기록한다. 릴리스가 최신 SALM 기능보다 뒤처진 경우 공식 문서의 nightly 또는 소스 빌드 안내를 확인한다. 이 프로젝트의 `engine/setup.bat`은 기존 Whisper 환경을 준비하며 Nemotron 런타임이나 가중치를 설치하지 않는다.

Windows에서는 다음 설치 경로도 GUI가 자동으로 찾는다.

```text
%LOCALAPPDATA%\Programs\NeMoSpeech\bin\nemo-speech.exe
```

실행 파일 설정이 비어 있거나 `nemo-speech` 또는 `nemo-speech.exe`이면 PATH를 먼저 확인하고, 찾지 못하면 위 경로를 확인한다. 직접 지정한 다른 경로는 유지한다. 자동 탐색을 포함한 GUI 빌드를 실행해야 하며, Python Backend를 직접 실행할 때는 `--nemotron-executable`에 전체 경로를 지정하거나 PATH를 설정한다.

### 현재 PC의 지원 런타임 빌드 재현

현재 PC에는 `%LOCALAPPDATA%\NeMoSpeech\validation`에 `prepare-runtime-536b17a.ps1`과 `build-runtime-536b17a.ps1` 보조 스크립트를 준비했다. 공식 저장소에 포함된 스크립트는 아니며, 이 PC의 독립 빌드 환경을 재현하기 위한 파일이다.

```powershell
$nemotronValidation = Join-Path $env:LOCALAPPDATA 'NeMoSpeech\validation'
& (Join-Path $nemotronValidation 'prepare-runtime-536b17a.ps1')
& (Join-Path $nemotronValidation 'build-runtime-536b17a.ps1')
```

준비 단계는 NVIDIA CUDA 12.9.1의 Windows NVCC·cudart·CCCL·cuBLAS 압축 파일을 공식 배포 메타데이터의 SHA-256으로 검증하고 작업 폴더에 푼다. 시스템 CUDA 설치 없이 기존 VS2022 Build Tools와 함께 사용한다. 런타임 소스는 `runtime-source-536b17a`에 고정하며, 모델 변환용 `source` 폴더와 GGUF 파일을 수정하지 않는다. [NVIDIA 공식 CUDA 배포 메타데이터](https://developer.download.nvidia.com/compute/cuda/redist/redistrib_12.9.1.json)

빌드는 공식 Windows 빌드 스크립트의 ASR + SALM 구성과 GPU 아키텍처 120을 사용한다. 결과는 `%LOCALAPPDATA%\NeMoSpeech\runtime-536b17a`에 설치하며, 필요한 CUDA DLL과 `build-receipt.json`을 함께 보관한다. 이 영수증 파일에는 공식 소스 커밋과 실행 파일의 SHA-256이 기록된다. 실행 파일은 `bin\nemo-speech.exe`이고 버전 표시는 `0.3.0+local.536b17a`다. 다른 GPU나 PC에서는 공식 빌드 문서에 맞춰 도구와 아키텍처를 조정한다.

한국어 Windows의 기본 코드 페이지에서 일부 공식 소스의 UTF-8 문자열이 잘못 해석되는 문제를 피하도록 보조 빌드 스크립트는 C++ 컴파일 옵션에 `/utf-8`을 지정한다. 소스 파일 자체는 변경하지 않는다.

### 현재 PC 검증 결과

2026-11-05에 `0.3.0+local.536b17a`를 빌드하고 `%LOCALAPPDATA%\Programs\NeMoSpeech`에 적용했다. GUI에 저장된 기존 `bin\nemo-speech.exe` 경로를 그대로 사용한다. 이전 설치 전체는 `%LOCALAPPDATA%\Programs\NeMoSpeech-backup-d8a1abf-20261105`에 보관했다.

기존 IQ4_NL decoder와 Encoder-F16 GGUF를 그대로 사용해 15초 한국어 WAV를 실행했다. 기본 `nemotron_8gb.yaml`의 `n_cpu_moe: 53` 적용이 네이티브 로그에서 확인됐고, 종료 코드 0으로 전사·단어 시간·화자 정보를 얻었다. 공통 Segment 변환과 TXT/SRT 저장도 경고 없이 완료됐다. 설치된 실행 파일과 사용자 초기 문맥 파일을 사용한 `backend_bridge.py` 실행도 `completed` 이벤트와 TXT/SRT 저장까지 성공했다. 이 검증은 짧은 샘플의 실행 호환성 확인이며, 61분 전체 녹음의 인식 품질·처리 시간은 아직 측정하지 않았다. 원본 JSON, 진단 로그와 결과는 `%LOCALAPPDATA%\NeMoSpeech\validation\runtime-536b17a-smoke`에 보관한다.

## 2. 모델 파일 준비

### Python으로 NVIDIA 원본 직접 다운로드

프로젝트 루트에서 다음 명령을 실행한다. 이 PC의 기존 `engine/.venv`에는 다운로드 라이브러리가 설치되어 있다.

```powershell
.\engine\.venv\Scripts\python.exe .\engine\download_nemotron.py
```

기본 저장 위치는 `%LOCALAPPDATA%\NeMoSpeech\checkpoints\NVIDIA-Nemotron-3.5-Transcribe-30B-A4B`다. NVIDIA 원본의 고정 revision을 다운로드하며 진행률을 표시하고, 완료 후 가중치와 토크나이저의 크기 및 SHA-256을 검증한다. 다운로드에 약 68.6GB의 여유 공간이 필요하다. 이미 완료된 파일은 같은 폴더에서 재사용하지만, 전송 중인 파일의 바이트 단위 이어받기는 Hugging Face 라이브러리 버전과 전송 방식에 따라 달라질 수 있다. `.cache/huggingface` 폴더를 유지한다.

```powershell
# 저장 폴더 변경
.\engine\.venv\Scripts\python.exe .\engine\download_nemotron.py --output-dir "C:\Models\Nemotron-original"

# 다운로드 없이 기본 저장 위치 확인
.\engine\.venv\Scripts\python.exe .\engine\download_nemotron.py --dry-run

# 이미 받은 파일만 오프라인 검증
.\engine\.venv\Scripts\python.exe .\engine\download_nemotron.py --verify-only
```

`Ctrl+C`로 중지할 수 있으며, 실패 시 동일한 명령으로 다시 실행한다. 전송 오류는 기본적으로 최대 3회 시도한다. 체크섬 불일치 시에는 `--force-download`로 다시 받을 수 있다. 다른 Python 환경을 사용한다면 `python -m pip install "huggingface-hub>=0.34,<2"`를 먼저 실행한다. [Hugging Face 공식 다운로드 안내](https://huggingface.co/docs/huggingface_hub/guides/download)

이 스크립트는 **약 67.5GB의 BF16 원본 다운로드 전용**이며 GGUF 변환이나 앱 전사를 시작하지 않는다. 다운로드 후 아래의 변환 절차를 진행한다.

런타임 코드와 모델 가중치는 별도다. 필요한 파일은 **IQ4_NL decoder GGUF**와 그 decoder에 대응하는 **Encoder-F16 GGUF** 두 개이며, 같은 디렉터리에 둔다. GUI의 모델 파일에는 decoder를 지정한다. Encoder 파일만 선택하거나 Whisper/OpenVINO 모델 폴더를 지정하면 안 된다. [공식 모델 및 플랫폼 안내](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/salm/models.md)

아래는 **공식 NVIDIA 원본을 직접 변환하는 Windows 경로**다. 현재 작업은 다음 버전과 경로를 기준으로 하며, 이 예시는 변환이나 실제 추론의 완료를 뜻하지 않는다. 공식 변환 소스는 `d8a1abf`로 고정하고, 기존 전사 엔진과 분리한 Python 3.12 가상환경을 사용한다. 실행 런타임은 별도로 앞 절의 CPU 오프로딩 지원 버전을 준비한다. 공식 Python 변환 코드는 수정하지 않는다. [해당 버전의 공식 변환 안내](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/d8a1abf2ea9d11e8f98ad0e10cc2c12ef1cb3a14/docs/model-conversion.md)

| 항목 | 고정 버전 또는 경로 |
|---|---|
| NeMo-Speech.cpp 변환 소스 | `d8a1abf2ea9d11e8f98ad0e10cc2c12ef1cb3a14` |
| 8GB 실행 런타임에 필요한 변경 | `536b17a41bef62d2a67a40ed3df61df26a4389fe`의 `n_cpu_moe` 지원 |
| llama.cpp 하위 모듈 | `bd4f514db14d87fded667787a7a963bfbaa98e89` + NVIDIA 제공 패치 |
| 공식 Hugging Face 체크포인트 revision | `c17667641642c4265ff2b7037dd101b7d4567634` |
| 작업 루트 | `%LOCALAPPDATA%\NeMoSpeech` — 현재 PC에서는 C: 드라이브 |
| 공식 소스 / 변환 가상환경 | 작업 루트의 `source` / `conversion-venv` |
| 원본 / 결과 | 작업 루트의 `checkpoints` / `models` |

원본 `model.safetensors`만 **67,496,013,734바이트(약 67.5GB)**다. 원본, BF16 decoder 중간 파일과 결과를 함께 보관하므로 변환 시 디스크 최대 사용량은 약 160GB로 추정한다. 도구와 캐시 여유까지 **C: 작업 공간에 약 200GB 이상**을 확보하는 구성을 권장한다. 별도 원본 복사나 중복 다운로드 캐시는 추가 공간을 쓴다. 32GB RAM에서는 기본 지연 로딩을 유지하며 `--no-lazy`를 사용하지 않는다. 실제 최대 RAM과 처리 시간은 변환 실행 중 확인해야 한다. [고정 revision의 공식 체크포인트](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B/tree/c17667641642c4265ff2b7037dd101b7d4567634) · [변환 단계의 공식 소스](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/d8a1abf2ea9d11e8f98ad0e10cc2c12ef1cb3a14/conversion/salm.py)

### 원본과 변환 환경 준비

다음 PowerShell 예시는 프로젝트 루트에서 시작한다. 이미 준비된 소스와 가상환경은 다시 만들지 않는다. 프로젝트의 `engine/.venv`는 새 환경을 만드는 Python 실행 파일로만 사용하고, 변환 의존성은 `conversion-venv`에 설치한다.

```powershell
$nemotronWork = Join-Path $env:LOCALAPPDATA 'NeMoSpeech'
$nemotronSource = Join-Path $nemotronWork 'source'
$nemotronVenv = Join-Path $nemotronWork 'conversion-venv'
$nemotronPython = Join-Path $nemotronVenv 'Scripts\python.exe'
$nemotronCheckpoint = Join-Path $nemotronWork 'checkpoints\NVIDIA-Nemotron-3.5-Transcribe-30B-A4B'
$nemotronModels = Join-Path $nemotronWork 'models'

git clone https://github.com/NVIDIA/NeMo-Speech.cpp.git $nemotronSource
git -C $nemotronSource checkout d8a1abf2ea9d11e8f98ad0e10cc2c12ef1cb3a14
git -C $nemotronSource submodule update --init --depth 1 llama.cpp

.\engine\.venv\Scripts\python.exe -m venv $nemotronVenv
& $nemotronPython -m pip install -r (Join-Path $nemotronSource 'requirements.txt')

& (Join-Path $nemotronVenv 'Scripts\hf.exe') download `
  nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B `
  --revision c17667641642c4265ff2b7037dd101b7d4567634 `
  --local-dir $nemotronCheckpoint
```

다운로드가 완료되면 `model.safetensors`의 SHA-256을 확인한다. 기대값은 `03142dde5176c1d4df21ee829642745a1d670ea4d79e1a4684c64d53195acff2`다.

```powershell
Get-FileHash -LiteralPath (Join-Path $nemotronCheckpoint 'model.safetensors') -Algorithm SHA256
```

### Windows 양자화기 빌드

SALM 변환에는 NVIDIA 패치가 적용된 `llama-quantize.exe`가 필요하다. 양자화기는 CPU 전용으로 빌드해도 된다. 현재 PC용 보조 배치 파일은 `%LOCALAPPDATA%\NeMoSpeech\source\build_quantizer_windows.cmd`에 준비하며, 다음 내용으로 재현할 수 있다. 이 배치 파일은 공식 저장소에 포함된 파일이 아니다. 다른 PC에서는 Visual Studio, CMake와 Ninja 설치 경로를 맞춘다. [공식 빌드 안내](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/d8a1abf2ea9d11e8f98ad0e10cc2c12ef1cb3a14/docs/build.md)

```bat
@echo off
setlocal
set "PATH=C:\Program Files (x86)\Microsoft Visual Studio\Installer;C:\Qt\Tools\CMake_64\bin;C:\Qt\Tools\Ninja;%PATH%"
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat" -arch=x64 -host_arch=x64
if errorlevel 1 exit /b %errorlevel%
set "VSLANG=1033"
cd /d "%~dp0"
cmake -DSOURCE_DIR=llama.cpp -DPATCH_DIR=patches -DDEST_DIR=.deps/llama.cpp-patched -P cmake/llama_cpp.cmake
if errorlevel 1 exit /b %errorlevel%
cmake -G Ninja -S .deps/llama.cpp-patched -B llama.cpp/build-quantize -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_SERVER=OFF -DLLAMA_BUILD_TOOLS=ON -DGGML_CUDA=OFF -DGGML_VULKAN=OFF -DGGML_METAL=OFF
if errorlevel 1 exit /b %errorlevel%
cmake --build llama.cpp/build-quantize --target llama-quantize --parallel 4
exit /b %errorlevel%
```

PowerShell에서 배치 파일을 실행하고 생성된 양자화기를 확인한다.

```powershell
& (Join-Path $nemotronSource 'build_quantizer_windows.cmd')
$nemotronQuantizer = Join-Path $nemotronSource 'llama.cpp\build-quantize\bin\llama-quantize.exe'
& $nemotronQuantizer --help
```

### IQ4_NL 변환

이 고정 버전의 Windows 자동 양자화기 탐색은 확장자 없는 실행 파일 이름을 검사한다. 따라서 **`--llama-quantize`에 전체 `.exe` 경로를 지정하고 `--no-build-quantizer`를 함께 전달**한다. 공식 변환 스크립트를 수정할 필요는 없다. 현재 PC에서는 변환에 필요한 심볼릭 링크 생성 권한도 확인했다.

```powershell
New-Item -ItemType Directory -Path $nemotronModels -Force | Out-Null
$nemotronDecoder = Join-Path $nemotronModels 'NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-IQ4_NL.gguf'
Set-Location $nemotronSource
& $nemotronPython .\convert_model.py $nemotronCheckpoint `
  --architecture salm --outtype iq4_nl `
  --outfile $nemotronDecoder `
  --llama-quantize $nemotronQuantizer --no-build-quantizer
```

변환이 성공하면 `models` 폴더에 다음 두 파일이 생성되어야 한다. encoder는 같은 체크포인트의 이름을 사용하며 decoder 파일명의 `IQ4_NL` 부분을 포함하지 않는다.

```text
NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-IQ4_NL.gguf
NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-Encoder-F16.gguf
```

두 파일을 함께 유지하고, 변환 종료 코드와 짧은 음성의 실제 추론을 확인한 뒤 GUI의 모델 경로에 decoder를 지정한다. 이미 변환된 파일을 사용하는 경우에도 모델의 출처, 양자화 형식, decoder/encoder의 대응 관계를 확인한다. 소형 `Nemotron ... Streaming 0.6B` 모델은 이 가이드의 Transcribe 모델과 다르다.

## 3. GUI 설정

처리 옵션에서 전사 엔진을 `Nemotron`으로 선택하고 설정 창에서 다음 값을 지정한다.

| 항목 | 설정 예 |
|---|---|
| Python 실행 파일 | 기존 `engine/.venv/Scripts/python.exe` |
| Nemotron 실행 파일 (`nemotronExecutable`) | 자동 탐색된 경로 또는 설치한 `nemo-speech.exe` 전체 경로 |
| Nemotron 모델 (`nemotronModelFile`) | 준비한 `...-IQ4_NL.gguf` decoder 파일 |
| Nemotron YAML (`nemotronConfigFile`) | 비워 두면 프로젝트의 `engine/nemotron_8gb.yaml` |

한국어 녹음을 선택하고 전사를 시작한다. 화자 분리를 켜면 내장 화자 분리를 사용하며 화자 수는 `Auto`로 요청별 최대 8명까지 구분한다. 자동 분할한 구간마다 다른 화자 번호를 부여하므로 전체 결과의 번호는 8개를 넘을 수 있다. 같은 사람도 다음 구간에서 다른 번호를 받을 수 있으며, 구간 간 동일 인물을 자동으로 연결하지 않는다. 이 제한은 실행 시 경고로 안내한다. 화자 번호는 실제 사람의 신원을 뜻하지 않는다.

Whisper Device/Beam/Window/Overlap, 무음 건너뛰기와 sherpa 전용 설정은 Nemotron에서 사용하지 않는다. Nemotron의 실행 장치와 메모리는 YAML로 조정한다. 핫워드와 초기 문맥을 사용하는 경우에는 전사 지시문의 문맥으로 전달되며 기존 Whisper 옵션과 같은 효과를 보장하지 않는다. Whisper를 다시 선택하면 기존 엔진을 사용할 수 있다.

## 4. CLI로 연결 확인

프로젝트 루트에서 짧은 한국어 테스트 파일로 실행한다. 아래 예시의 입력·모델·실행 파일 경로는 실제 경로로 바꾼다. `--nemotron-config`를 생략하면 기본 8GB 프리셋을 사용한다.

```powershell
.\engine\.venv\Scripts\python.exe .\engine\backend_bridge.py `
  --input "D:\Audio\sample.wav" `
  --engine nemotron `
  --nemotron-executable "nemo-speech" `
  --nemotron-model "D:\Models\Nemotron\NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-IQ4_NL.gguf" `
  --language ko --diarization --num-speakers auto `
  --output-dir "D:\Audio\results"
```

사용자 YAML을 쓰려면 `--nemotron-config "D:\Models\Nemotron\custom.yaml"`을 추가한다. JSON Lines 이벤트는 표준 출력으로, 진단 로그는 표준 오류로 전달된다. 기존 다중 마이크 입력도 전처리된 음성을 동일한 엔진 경로로 전달한다.

Backend는 16 kHz 모노 음성을 준비한 뒤 로컬 CLI를 `--format json`으로 호출한다. 내장 정렬의 단어 시간·화자 정보를 기존 Segment로 변환하며 TXT와 SRT를 저장한다. 공식 CLI에서 단독으로 확인할 때는 `--format json --diarize`를 사용할 수 있다. [공식 SALM CLI 안내](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/salm/README.md)

## 긴 녹음의 자동 분할과 결과 병합

GUI와 `backend_bridge.py` 모두 같은 자동 분할을 사용한다. 기본 프리셋은 구간당 최대 29분, 사용자 YAML은 구간당 최대 1시간이다. 한도 이내의 짧은 녹음은 한 번의 요청으로 처리한다. `nemotron.py`의 단일 요청 어댑터에는 최대 1시간 제한이 유지된다.

Bridge는 16 kHz 음성의 전체 샘플 수를 기준으로 필요한 구간 수를 계산하고, 겹치지 않는 연속 구간에 균등하게 배분한다. 구간 수는 `ceil(전체 샘플 수 / 구간별 최대 샘플 수)`이며 구간 길이 차이는 최대 한 샘플이다. 따라서 **1시간 1분 9초 녹음은 기본 프리셋에서 20분 23초씩 세 구간**으로 나누어 순서대로 처리한다. 원본 파일을 수정하거나 사용자가 따로 파일을 나눌 필요는 없다.

각 구간의 전사 시간에 해당 구간의 시작 시각을 더하므로 화면과 결과 파일에는 원본 녹음의 시간이 표시된다. 다중 마이크 입력은 전처리로 만든 음성의 기준 시간축을 그대로 유지한다. 모든 구간이 성공해야 전체 결과를 TXT 한 파일과 SRT 한 파일로 저장한다. 중간 실패 또는 취소 시 부분 결과를 최종 파일로 남기지 않고 기존 결과를 보존한다. 취소하면 실행 중인 네이티브 프로세스도 종료하며 다음 구간은 시작하지 않는다.

진행률은 구간이 완료될 때만 갱신한다. 구간 내부의 실제 추론 진행률이나 Whisper 방식의 예상 남은 시간은 표시하지 않는다. 구간 경계에 겹침이나 자동 본문 중복 제거를 적용하지 않으므로 단어·문맥이 나뉘어 경계 부근의 인식 품질이 낮아질 수 있다. 화자 번호도 구간마다 새로 부여하며, 구간 간 동일 인물 연결은 제공하지 않는다.

## 결과 확인과 문제 해결

| 상황 | 확인할 내용 |
|---|---|
| 실행 파일을 찾지 못함 | 아래 절차로 설치 위치를 확인하고 설정의 실행 파일 경로를 지정 |
| 디코더 GGUF 모델 파일을 선택하라는 오류 | 실행 파일과 별도로 준비한 decoder GGUF를 모델 항목에 지정 |
| 모델을 불러오지 못함 | decoder와 대응 encoder가 함께 있는지, 양자화 형식이 IQ4_NL인지 |
| `unknown key 'asr.salm.n_cpu_moe'` | `536b17a`의 CPU 오프로딩 지원을 포함한 런타임으로 교체; YAML의 해당 키는 유지 |
| 메모리 부족 또는 매우 느린 처리 | 남은 VRAM/RAM, 다른 GPU 작업, 기본 8GB YAML 적용 여부 |
| 29분 초과 오류가 계속 표시됨 | 자동 분할을 지원하는 최신 `engine/backend_bridge.py`를 실행하는지 Python Backend 경로 확인 |
| 자동 분할 또는 구간별 화자 번호 경고 | 긴 녹음을 순차 처리하는 정상 안내; 같은 사람이 다음 구간에서 다른 번호를 받을 수 있음 |
| 정확한 시간 정렬을 얻지 못했다는 경고 | 해당 요청의 전체 본문은 그 음성 구간 길이의 단일 Segment로 보존되고 원본 시간축에 표시됨; 그 구간의 정확한 자막 시간과 화자 정보는 사용할 수 없음 |
| JSON 해석 오류 | CLI의 SALM JSON 지원 버전과 표준 오류 로그 |

`Nemotron 실행 파일을 찾을 수 없습니다`가 나오면 아직 모델 추론을 시작하지 못한 상태다. PowerShell에서 설치 경로를 먼저 확인한다.

```powershell
$nemotronProgram = Join-Path $env:LOCALAPPDATA 'Programs\NeMoSpeech\bin\nemo-speech.exe'
Test-Path -LiteralPath $nemotronProgram
& $nemotronProgram --version
& $nemotronProgram help transcribe
```

`Test-Path`가 `False`이면 런타임을 해당 위치에 설치하거나 실제 설치 위치를 찾는다. `True`이고 도움말이 정상 출력되면 **설정 → Nemotron 설정 → nemo-speech 실행 파일**에서 그 파일을 선택하고 저장한다. 기존에 잘못 지정한 전체 경로는 자동으로 덮어쓰지 않으므로 직접 수정한다. 업데이트한 GUI를 다시 실행하면 기본 실행 파일 이름으로 저장된 설정도 위 설치 경로에서 찾는다.

실행 파일을 찾았더라도 **디코더 GGUF 모델**이 비어 있으면 전사할 수 없다. 런타임 설치는 모델 다운로드를 포함하지 않으므로, 2절의 decoder와 대응 encoder를 준비한 후 decoder 경로를 별도로 지정한다. 요청별 길이 제한은 기본 설정 29분, 사용자 YAML 최대 1시간이며 더 긴 파일은 Bridge가 자동으로 나눈다. 따라서 사용자 YAML로 제한을 우회할 필요 없이 기본 설정으로 긴 파일을 선택할 수 있다. 사용자 YAML의 메모리가 부족하면 자동 분할된 요청도 실패할 수 있다.

설치 후 실제 검증은 30초~2분의 한국어 음성부터 시작한다. 같은 파일의 Whisper 결과와 전사 누락, 이름·숫자, 화자 전환, 자막 시간을 비교하고 처리 시간과 최대 메모리 사용량을 기록한다. 긴 파일에서는 분할 경계 부근의 누락과 문맥 변화도 확인한다. 모의 CLI를 이용한 자동 테스트 통과는 실제 모델의 실행 속도나 인식 품질 검증을 대신하지 않는다. 서버 GPU의 대량 처리 성능 수치를 이 노트북의 예상 속도로 사용하지 않는다.

## 라이선스

모델은 [OpenMDW 1.1](https://openmdw.ai/license/1-1/)이며 상업적 이용을 허용한다. 모델 자료를 재배포할 때는 라이선스와 해당 저작권·출처 고지를 보존해야 한다. [NeMo-Speech.cpp 코드](https://github.com/NVIDIA/NeMo-Speech.cpp)는 별도의 Apache 2.0 라이선스이며 포함된 제3자 구성 요소의 고지도 확인한다.
