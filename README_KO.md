Intel/OpenVINO Korean Transcriber v6 - INT8 / FP16 comparison

Models
------
INT8: OpenVINO/whisper-large-v3-turbo-int8-ov
FP16: OpenVINO/whisper-large-v3-turbo-fp16-ov

Both modes use the same settings for a fair comparison:
- Intel NPU
- Greedy decoding, num_beams=1
- Korean language token <|ko|>
- 120 second windows with 4 second overlap
- hotwords.txt and initial_prompt.txt
- progress and ETA
- TXT and SRT output

Upgrade from v5
---------------
For an existing v5 installation, extract the drop-in patch over the v5 folder.
Keep your existing .venv and models folder. Run setup.bat once. The existing
INT8 model will be reused and only the missing FP16 model will be downloaded.

Launchers
---------
run_int8.bat
  Output: recording_int8.txt / recording_int8.srt

run_fp16.bat
  Output: recording_fp16.txt / recording_fp16.srt

run_compare.bat
  Select a file once. INT8 runs first, then FP16.

run_fp16_compat.bat
  Same FP16/NPU mode, but sets DISABLE_OPENVINO_GENAI_NPU_L0=1.
  Use only if normal FP16 reports an NPU memory or Level Zero error.

Notes
-----
FP16 requires more model memory than INT8. Accuracy may improve, remain nearly
the same, or occasionally differ in either direction because both are the same
Large-v3-Turbo ASR model with different weight precision. Compare the actual
transcripts for your recording rather than assuming FP16 will always be better.
