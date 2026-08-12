Intel/OpenVINO Korean Transcriber v7 - speaker diarization

Models
------
Whisper ASR:
- INT8: OpenVINO/whisper-large-v3-turbo-int8-ov
- FP16: OpenVINO/whisper-large-v3-turbo-fp16-ov

Speaker diarization:
- sherpa-onnx pyannote segmentation 3.0
- 3D-Speaker ERes2Net speaker embedding model

Whisper transcription runs on the selected OpenVINO device (default: Intel NPU).
Speaker diarization runs separately with sherpa-onnx on CPU and is matched to
Whisper timestamp segments afterwards.

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

Launchers
---------
run_int8.bat
  NPU + INT8 transcription only.
  Output: recording_int8.txt / recording_int8.srt

run_fp16.bat
  NPU + FP16 transcription only.
  Output: recording_fp16.txt / recording_fp16.srt

run_int8_diarize.bat
  NPU + INT8 transcription, followed by CPU speaker diarization.
  Output: recording_int8_diarized.txt / recording_int8_diarized.srt

run_fp16_diarize.bat
  NPU + FP16 transcription, followed by CPU speaker diarization.
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

Use --num-speakers -1 for automatic clustering. When automatic clustering is
used, --speaker-threshold controls how aggressively speakers are separated.

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
previous transcription-only behavior. The diarization stage is independent of
Whisper, so a diarization failure does not require changing the OpenVINO model.

Speaker labels identify clusters (화자 1, 화자 2, ...), not real names. For
recordings where the number of participants is known, supplying the exact count
with the second launcher argument generally gives the clustering algorithm more
information than automatic speaker-count detection.
