Intel/OpenVINO Korean Transcriber v7 - speaker diarization

Models
------
Whisper ASR:
- INT8: OpenVINO/whisper-large-v3-turbo-int8-ov
- FP16: OpenVINO/whisper-large-v3-turbo-fp16-ov

Speaker diarization:
- sherpa-onnx pyannote segmentation 3.0
- 3D-Speaker ERes2Net speaker embedding model

Default execution path
----------------------
- Whisper transcription: Intel NPU via OpenVINO GenAI
- Speaker segmentation: Intel NPU via OpenVINO
- Speaker embedding: CPU via sherpa-onnx
- Speaker clustering: CPU via sherpa-onnx

The pyannote segmentation ONNX model is reshaped to a fixed 10-second input
before OpenVINO compilation so it can be compiled for Intel NPU. If NPU
compilation is unavailable or unsupported for the segmentation model, the
program prints a warning and automatically retries speaker segmentation on
OpenVINO CPU. Speaker labels are then matched to Whisper timestamp segments.

Common transcription settings
-----------------------------
- Intel NPU
- Greedy decoding, num_beams=1
- Korean language token <|ko|>
- 120 second windows with 4 second overlap
- hotwords.txt and initial_prompt.txt
- progress and ETA
- TXT and SRT output

Setup
-----
Run setup.bat once. It creates/reuses .venv, installs the runtime packages,
prepares both Whisper models, and downloads the speaker diarization models.
Existing models are reused.

선택형 Nemotron 엔진
--------------------
GUI의 전사 엔진에서 Nemotron 3.5 Transcribe를 선택할 수 있다. 기본값은
기존 Whisper이며, Nemotron은 별도 설치한 NeMo-Speech.cpp 실행 파일과
IQ4_NL decoder/encoder GGUF를 사용한다. `setup.bat`은 Nemotron 런타임이나
가중치를 내려받지 않는다.

기본 8GB 프리셋에는 NeMo-Speech.cpp 공식 커밋 `536b17a`의 `n_cpu_moe`
지원이 필요하다. `0.3.0+nightly.d8a1abf`는 해당 키를 거부한다. 이 오류가
나면 YAML의 오프로딩 설정을 삭제하지 말고 지원 런타임으로 교체한다.

8GB GPU용 기본 설정은 `nemotron_8gb.yaml`이며 MoE expert를 시스템 RAM에
두고 요청별 최대 29분의 음성을 처리한다. 긴 녹음은 `backend_bridge.py`가
16 kHz 정수 샘플 인덱스로 겹치지 않는 연속 구간에 균등 분할해 순차 전사한다.
구간 수는 `ceil(전체 샘플 수 / 요청별 최대 샘플 수)`이며, 1시간 1분 9초는
기본 설정에서 20분 23초씩 세 구간이 된다. 사용자 YAML은 요청별 최대 1시간이며
실제 처리 가능 길이는 해당 설정의 메모리·맥락 한계에 따른다.
`nemotron.py` 단일 요청 어댑터의 1시간 제한은 유지된다.

구간의 시작 시각을 더해 원본 시간축을 복원하고, 모든 구간이 성공한 뒤
TXT/SRT 각 한 파일로 저장한다. 중간 실패·취소 시 부분 결과를 확정하지 않고
기존 결과를 보존한다. 진행률은 구간 완료 시에만 갱신하며 Whisper ETA는
표시하지 않는다. 경계에서 단어·문맥이 나뉘어 인식 품질이 낮아질 수 있다.
내장 화자 분리는 구간당 최대 8명을 구분하며, 구간마다 겹치지 않는 화자 번호를
부여하므로 전체 결과의 번호는 8개를 넘을 수 있다. 같은 사람의 구간 간 연결은
제공하지 않으며 같은 사람도 다른 번호를 받을 수 있다는 경고를 표시한다.

실제 RAM 사용량·속도·인식 품질은 사용할 PC와 녹음으로 확인해야 한다.
설치, GUI 설정, CLI 예제와 제약은
[Nemotron 엔진 사용 가이드](../docs/Nemotron_엔진_사용_가이드.md)를 참조한다.

NVIDIA 원본 모델만 직접 다운로드하려면 프로젝트 루트에서 실행한다:

```powershell
.\engine\.venv\Scripts\python.exe .\engine\download_nemotron.py
```

약 67.5GB이며 기본 저장 위치는 `%LOCALAPPDATA%\NeMoSpeech\checkpoints` 아래다.
`--output-dir`로 위치를 바꾸고 `--verify-only`로 기존 파일의 체크섬을 확인할 수 있다.
이 스크립트는 원본 다운로드 전용이며 GGUF 변환은 사용 가이드의 별도 절차다.

Qt GUI 설정
-----------
GUI는 엔진 폴더를 찾아 `engine/.venv/Scripts/python.exe`와
`engine/models/whisper-large-v3-turbo-int8`을 기본값으로 사용한다.
기존 설정에 일반 명령 `python`이 저장되어 있으면 엔진 가상환경으로 전환한다.
직접 지정한 Python 실행 파일과 사용자 모델 경로는 유지한다.
환경 변수 `PYTHON`을 지정한 경우에는 기본 Python 선택에 해당 값을 사용한다.

`ModuleNotFoundError: No module named 'numpy'`가 발생하면 GUI 설정의
Python 경로를 확인한다. `setup.bat`은 일반 Python이 아닌 `.venv`에 패키지를
설치한다. 설정에서 위 가상환경 실행 파일을 선택하고, 가상환경이 없거나
패키지가 누락되었다면 `setup.bat`을 실행한 뒤 GUI를 다시 시작한다.
모델 폴더에는 빌드 출력 폴더의 `debug/engine/models/...` 대신 실제 엔진
폴더 아래의 모델 경로를 지정한다. `setup.bat` 완료 메시지에 두 경로가 표시된다.

Launchers
---------
run_int8.bat
  NPU + INT8 transcription only.
  Output: recording_int8.txt / recording_int8.srt

run_fp16.bat
  NPU + FP16 transcription only.
  Output: recording_fp16.txt / recording_fp16.srt

run_int8_diarize.bat
  NPU + INT8 transcription, followed by hybrid speaker diarization.
  Speaker segmentation uses NPU by default; embedding/clustering use CPU.
  Output: recording_int8_diarized.txt / recording_int8_diarized.srt

run_fp16_diarize.bat
  NPU + FP16 transcription, followed by hybrid speaker diarization.
  Speaker segmentation uses NPU by default; embedding/clustering use CPU.
  Output: recording_fp16_diarized.txt / recording_fp16_diarized.srt

run_compare.bat
  Select a file once. INT8 runs first, then FP16.

run_fp16_compat.bat
  Same FP16/NPU mode, but sets DISABLE_OPENVINO_GENAI_NPU_L0=1.
  Use only if normal FP16 reports an NPU memory or Level Zero error.

Speaker count
-------------
The diarization launchers detect the speaker count automatically by default.
If the number of speakers is known, pass it as the second argument:

  run_int8_diarize.bat recording.m4a 2
  run_fp16_diarize.bat meeting.wav 4

The equivalent Python options are:

  --diarize
  --num-speakers 2
  --speaker-threshold 0.5
  --diarization-device NPU

Use --num-speakers -1 for automatic clustering. When automatic clustering is
used, --speaker-threshold controls how aggressively speakers are separated.

Diarization device
------------------
Speaker segmentation uses Intel NPU by default:

  --diarization-device NPU

To force speaker segmentation to CPU:

  --diarization-device CPU

By default an NPU compile/device failure falls back to OpenVINO CPU. To require
NPU execution and stop instead of falling back:

  --diarization-device NPU --diarization-no-fallback

The large Whisper pipeline is released before speaker segmentation is compiled,
so the Whisper model and diarization segmentation model do not need to remain
resident on the NPU at the same time.

Diarized output
---------------
TXT groups consecutive transcript segments by speaker, for example:

  [00:00] 화자 1
  안녕하세요. 회의를 시작하겠습니다.

  [00:05] 화자 2
  네, 진행 상황부터 말씀드리겠습니다.

SRT keeps the original Whisper timestamp and prefixes each subtitle with the
assigned speaker label, for example:

  [화자 2] 네, 진행 상황부터 말씀드리겠습니다.

Notes
-----
Speaker diarization is optional. Existing run_int8.bat and run_fp16.bat keep the
previous transcription-only behavior.

Speaker labels identify clusters (화자 1, 화자 2, ...), not real names. For
recordings where the number of participants is known, supplying the exact count
with the second launcher argument generally gives the clustering algorithm more
information than automatic speaker-count detection.
