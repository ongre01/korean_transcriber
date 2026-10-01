#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import gc
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

APP_DIR = Path(__file__).resolve().parent
TARGET_SAMPLE_RATE = 16000
DEFAULT_WINDOW_SECONDS = 120.0
DEFAULT_OVERLAP_SECONDS = 4.0
DEFAULT_HOTWORDS_FILE = APP_DIR / 'hotwords.txt'
DEFAULT_INITIAL_PROMPT_FILE = APP_DIR / 'initial_prompt.txt'
DEFAULT_DIARIZATION_DIR = APP_DIR / 'models' / 'diarization'
DEFAULT_SEGMENTATION_MODEL = DEFAULT_DIARIZATION_DIR / 'sherpa-onnx-pyannote-segmentation-3-0' / 'model.onnx'
DEFAULT_EMBEDDING_MODEL = DEFAULT_DIARIZATION_DIR / '3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx'
# Keep enough decoder space for a useful transcription when initial-prompt
# support becomes available. Whisper's generation config has a 448-token
# decoder limit for the bundled model.
INITIAL_PROMPT_MIN_TRANSCRIPTION_TOKENS = 64


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: int | None = None


@dataclass(frozen=True)
class InitialPromptPlan:
    """Validated initial-prompt metadata for the current safe-mode policy."""

    requested: bool
    token_count: int | None = None
    maximum_token_count: int | None = None
    warning: str | None = None


def choose_file() -> Path | None:
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    filename = filedialog.askopenfilename(
        title='Select recording to transcribe',
        filetypes=[
            ('Audio / video', '*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus *.mp4 *.mov *.mkv *.webm'),
            ('All files', '*.*'),
        ],
    )
    root.destroy()
    return Path(filename) if filename else None


def available_devices() -> list[str]:
    import openvino as ov

    return list(ov.Core().available_devices)


def ensure_device(device: str) -> list[str]:
    devices = available_devices()
    requested = device.upper()
    ok = any(str(d).upper() == requested or str(d).upper().startswith(requested + '.') for d in devices)
    if not ok:
        raise RuntimeError(f'OpenVINO device {device} was not found. Devices: {devices}')
    return devices


def decode_audio_16k_mono(input_path: Path) -> np.ndarray:
    import av
    from av.audio.resampler import AudioResampler
    container = av.open(str(input_path))
    try:
        stream = next((s for s in container.streams if s.type == 'audio'), None)
        if stream is None:
            raise RuntimeError('No audio stream found in the input file.')
        resampler = AudioResampler(format='fltp', layout='mono', rate=TARGET_SAMPLE_RATE)
        chunks: list[np.ndarray] = []
        for frame in container.decode(stream):
            for out_frame in resampler.resample(frame):
                arr = out_frame.to_ndarray()
                if arr.size:
                    chunks.append(np.asarray(arr, dtype=np.float32).reshape(-1))
        try:
            for out_frame in resampler.resample(None):
                arr = out_frame.to_ndarray()
                if arr.size:
                    chunks.append(np.asarray(arr, dtype=np.float32).reshape(-1))
        except Exception:
            pass
        if not chunks:
            raise RuntimeError('Audio decoding produced no samples.')
        audio = np.concatenate(chunks).astype(np.float32, copy=False)
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 1.05:
            audio = audio / peak
        return np.ascontiguousarray(np.clip(audio, -1.0, 1.0), dtype=np.float32)
    finally:
        container.close()


def read_optional_text(path: Path) -> str:
    if not path.exists():
        return ''
    lines = []
    for raw in path.read_text(encoding='utf-8-sig').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        lines.append(line)
    return ', '.join(lines).strip()


def _initial_prompt_token_count(pipe, initial_prompt: str) -> int:
    tokenized = pipe.get_tokenizer().encode(initial_prompt)
    shape = getattr(getattr(tokenized, 'input_ids', None), 'shape', None)
    if not shape:
        raise RuntimeError('the tokenizer did not return input token dimensions')
    token_count = int(shape[-1])
    if token_count < 1:
        raise RuntimeError('the tokenizer returned no input tokens')
    return token_count


def prepare_initial_prompt(pipe, initial_prompt: str) -> InitialPromptPlan:
    """Validate and safely disable an initial prompt for OpenVINO Whisper.

    The current OpenVINO Whisper pipeline raises an inference tensor-range
    error when ``GenerationConfig.initial_prompt`` is populated, including for
    short prompts. Keep the text saved for a future compatible runtime, but do
    not pass it to the pipeline so a transcription can still complete.
    """

    prompt = initial_prompt.strip()
    if not prompt:
        return InitialPromptPlan(requested=False)

    max_length = getattr(pipe.get_generation_config(), 'max_length', None)
    try:
        max_length = int(max_length)
    except (TypeError, ValueError, OverflowError):
        max_length = 0
    maximum_token_count = max(0, max_length - INITIAL_PROMPT_MIN_TRANSCRIPTION_TOKENS)

    try:
        token_count = _initial_prompt_token_count(pipe, prompt)
    except Exception:
        return InitialPromptPlan(
            requested=True,
            maximum_token_count=maximum_token_count or None,
            warning=(
                '초기 프롬프트의 길이를 확인할 수 없어 안전하게 제외했습니다. '
                '프롬프트 내용은 저장되어 있으며 전사는 계속 진행됩니다.'
            ),
        )

    if token_count > maximum_token_count:
        return InitialPromptPlan(
            requested=True,
            token_count=token_count,
            maximum_token_count=maximum_token_count,
            warning=(
                f'초기 프롬프트가 허용 길이를 초과했습니다 '
                f'({token_count}토큰, 최대 {maximum_token_count}토큰). '
                '안전하게 제외하고 전사를 계속 진행합니다.'
            ),
        )

    return InitialPromptPlan(
        requested=True,
        token_count=token_count,
        maximum_token_count=maximum_token_count,
        warning=(
            '현재 OpenVINO Whisper 실행 환경에서는 초기 프롬프트가 입력 텐서 오류를 '
            '일으킬 수 있어 안전하게 제외했습니다. 프롬프트 내용은 저장되어 있으며 '
            '전사는 계속 진행됩니다.'
        ),
    )


def srt_timestamp(seconds: float) -> str:
    total_ms = int(round(max(0.0, float(seconds)) * 1000.0))
    hours, rem = divmod(total_ms, 3600000)
    minutes, rem = divmod(rem, 60000)
    secs, millis = divmod(rem, 1000)
    return f'{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}'


def clock(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f'{hours:d}:{minutes:02d}:{secs:02d}'
    return f'{minutes:02d}:{secs:02d}'


def print_progress(processed: float, total: float, index: int, count: int, started: float) -> None:
    ratio = 1.0 if total <= 0 else min(1.0, processed / total)
    width = 28
    filled = min(width, int(round(width * ratio)))
    bar = '#' * filled + '-' * (width - filled)
    elapsed = max(0.0, time.perf_counter() - started)
    eta_text = ''
    if 0.0 < ratio < 1.0:
        eta = max(0.0, elapsed * (1.0 / ratio - 1.0))
        eta_text = f' ETA {clock(eta)}'
    elif ratio >= 1.0:
        eta_text = ' ETA 00:00'
    print(
        f'\r      [{bar}] {ratio * 100:5.1f}%  {clock(processed)} / {clock(total)}  '
        f'window {index}/{count}{eta_text}',
        end='', flush=True,
    )


def transcribe_windows(
    pipe,
    config,
    audio: np.ndarray,
    duration: float,
    window: float,
    overlap: float,
    *,
    progress_callback: Callable[[float, float, int, int], None] | None = None,
    console_progress: bool = True,
):
    count = max(1, math.ceil(duration / window))
    segments: list[Segment] = []
    started = time.perf_counter()

    def report_progress(processed: float, index: int) -> None:
        if console_progress:
            print_progress(processed, duration, index, count, started)
        if progress_callback is not None:
            progress_callback(processed, duration, index, count)

    report_progress(0.0, 0)

    for idx in range(count):
        core_start = idx * window
        core_end = min(duration, (idx + 1) * window)
        infer_start = max(0.0, core_start - (overlap if idx > 0 else 0.0))
        infer_end = min(duration, core_end + (overlap if idx + 1 < count else 0.0))
        sample_start = int(round(infer_start * TARGET_SAMPLE_RATE))
        sample_end = int(round(infer_end * TARGET_SAMPLE_RATE))
        chunk = audio[sample_start:sample_end]

        result = pipe.generate(chunk.tolist(), config)
        chunks = list(result.chunks) if getattr(result, 'chunks', None) else []

        if chunks:
            for c in chunks:
                txt = str(c.text).strip()
                if not txt:
                    continue
                start = min(duration, infer_start + max(0.0, float(c.start_ts)))
                end = min(duration, infer_start + max(float(c.end_ts), float(c.start_ts) + 0.05))
                midpoint = (start + end) / 2.0
                is_last = idx == count - 1
                if midpoint < core_start:
                    continue
                if not is_last and midpoint >= core_end:
                    continue
                if is_last and midpoint > core_end:
                    continue
                segments.append(Segment(start, max(end, start + 0.05), txt))
        else:
            texts = getattr(result, 'texts', None)
            fallback = str(texts[0]).strip() if texts else str(result).strip()
            if fallback:
                segments.append(Segment(core_start, core_end, fallback))

        report_progress(core_end, idx + 1)

    if console_progress:
        print()
    segments.sort(key=lambda s: (s.start, s.end))
    return segments


def assign_speakers(segments: list[Segment], turns) -> None:
    """Assign each ASR segment to the diarization turn with the largest time overlap."""
    for seg in segments:
        best_speaker = None
        best_overlap = 0.0
        seg_midpoint = (seg.start + seg.end) / 2.0

        for turn in turns:
            overlap = max(0.0, min(seg.end, turn.end) - max(seg.start, turn.start))
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = turn.speaker

        if best_speaker is None:
            for turn in turns:
                if turn.start <= seg_midpoint <= turn.end:
                    best_speaker = turn.speaker
                    break

        seg.speaker = best_speaker


def speaker_label(speaker: int | None) -> str:
    return '화자 ?' if speaker is None else f'화자 {speaker + 1}'


def build_text(segments: list[Segment], diarized: bool) -> str:
    if not diarized:
        return '\n'.join(s.text for s in segments if s.text.strip()).strip()

    lines: list[str] = []
    marker = object()
    previous_speaker: object | int | None = marker
    for seg in segments:
        if not seg.text.strip():
            continue
        if seg.speaker != previous_speaker:
            if lines:
                lines.append('')
            lines.append(f'[{clock(seg.start)}] {speaker_label(seg.speaker)}')
            previous_speaker = seg.speaker
        lines.append(seg.text.strip())
    return '\n'.join(lines).strip()


def save_outputs(
    input_path: Path,
    segments: list[Segment],
    duration: float,
    suffix: str,
    *,
    diarized: bool,
    output_dir: Path | None = None,
):
    stem = input_path.stem + suffix
    destination = input_path.parent if output_dir is None else Path(output_dir)
    txt_path = destination / (stem + '.txt')
    srt_path = destination / (stem + '.srt')
    text = build_text(segments, diarized)

    txt_path.write_text(text.strip() + '\n', encoding='utf-8-sig')
    blocks = []
    index = 1
    for seg in segments:
        if not seg.text.strip():
            continue
        subtitle = seg.text.strip()
        if diarized:
            subtitle = f'[{speaker_label(seg.speaker)}] {subtitle}'
        blocks.append(f'{index}\n{srt_timestamp(seg.start)} --> {srt_timestamp(seg.end)}\n{subtitle}\n')
        index += 1
    if not blocks and text.strip():
        blocks.append(f'1\n{srt_timestamp(0)} --> {srt_timestamp(max(duration, 0.5))}\n{text.strip()}\n')
    srt_path.write_text('\n'.join(blocks), encoding='utf-8-sig')
    return txt_path, srt_path


def load_whisper_pipeline(model_dir: Path, device: str, model_label: str = ''):
    import openvino_genai as ov_genai

    pipeline_options = {}
    if device.upper().startswith('NPU') or device.upper().startswith('GPU'):
        safe_label = (model_label or 'model').lower()
        safe_device = device.upper().replace('.', '_')
        cache_dir = APP_DIR / f'.ov_cache_{safe_label}_{safe_device}'
        cache_dir.mkdir(parents=True, exist_ok=True)
        pipeline_options['CACHE_DIR'] = str(cache_dir)
    return ov_genai.WhisperPipeline(str(model_dir), device, **pipeline_options)


def configure_generation(pipe, language: str, beams: int, hotwords: str):
    config = pipe.get_generation_config()
    config.language = language
    config.task = 'transcribe'
    config.return_timestamps = True
    config.num_beams = beams
    config.num_beam_groups = 1
    config.num_return_sequences = 1
    config.do_sample = False
    if hotwords:
        config.hotwords = hotwords
    # Do not touch ``initial_prompt`` until the OpenVINO Whisper implementation
    # can handle it without the reproducible tensor-range inference failure.
    # Assigning even an empty value activates the faulty runtime path.
    return config


def parse_args():
    p = argparse.ArgumentParser(description='OpenVINO Whisper Large-v3-Turbo Korean transcription')
    p.add_argument('input', nargs='?')
    p.add_argument('--model-dir', required=True)
    p.add_argument('--model-label', default='')
    p.add_argument('--device', default='NPU')
    p.add_argument('--beams', type=int, default=1)
    p.add_argument('--language', default='<|ko|>')
    p.add_argument('--window-seconds', type=float, default=DEFAULT_WINDOW_SECONDS)
    p.add_argument('--overlap-seconds', type=float, default=DEFAULT_OVERLAP_SECONDS)
    p.add_argument('--output-suffix', default='')
    p.add_argument('--hotwords-file', default=str(DEFAULT_HOTWORDS_FILE))
    p.add_argument('--initial-prompt-file', default=str(DEFAULT_INITIAL_PROMPT_FILE))
    p.add_argument(
        '--diarize',
        action='store_true',
        help='Enable hybrid speaker diarization (OpenVINO segmentation + CPU embedding/clustering)',
    )
    p.add_argument('--num-speakers', type=int, default=-1, help='Known number of speakers, or -1 to detect automatically')
    p.add_argument('--speaker-threshold', type=float, default=0.5, help='Clustering threshold when speaker count is unknown')
    p.add_argument('--diarization-segmentation-model', default=str(DEFAULT_SEGMENTATION_MODEL))
    p.add_argument('--diarization-embedding-model', default=str(DEFAULT_EMBEDDING_MODEL))
    p.add_argument(
        '--diarization-device',
        default='NPU',
        help='OpenVINO device for speaker segmentation (default: NPU)',
    )
    p.add_argument(
        '--diarization-no-fallback',
        action='store_true',
        help='Fail instead of falling back to CPU when diarization segmentation cannot use the requested device',
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.beams < 1:
        print('[ERROR] --beams must be at least 1.')
        return 2
    if args.window_seconds < 30:
        print('[ERROR] --window-seconds must be at least 30.')
        return 2
    if args.overlap_seconds < 0 or args.overlap_seconds >= args.window_seconds / 2:
        print('[ERROR] Invalid overlap.')
        return 2
    if args.num_speakers == 0 or args.num_speakers < -1:
        print('[ERROR] --num-speakers must be -1 or at least 1.')
        return 2
    if not 0.0 < args.speaker_threshold <= 1.0:
        print('[ERROR] --speaker-threshold must be in (0, 1].')
        return 2

    input_path = Path(args.input).expanduser().resolve() if args.input else choose_file()
    if input_path is None:
        print('Cancelled.')
        return 0
    if not input_path.is_file():
        print(f'[ERROR] Input file not found: {input_path}')
        return 2

    model_dir = Path(args.model_dir).expanduser().resolve()
    if not model_dir.exists():
        print(f'[ERROR] Model directory not found: {model_dir}')
        return 3

    hotwords = read_optional_text(Path(args.hotwords_file).expanduser().resolve())
    initial_prompt = read_optional_text(Path(args.initial_prompt_file).expanduser().resolve())

    try:
        print('=' * 78)
        print(' Korean Transcriber v7 - optional speaker diarization')
        print('=' * 78)
        print(f'Input      : {input_path}')
        print(f'Model      : {model_dir}')
        if args.model_label:
            print(f'Precision  : {args.model_label}')
        print(f'Device     : {args.device}')
        print(f'Beam       : {args.beams}')
        print(f'Window     : {args.window_seconds:.0f} sec')
        print(f'Overlap    : {args.overlap_seconds:.1f} sec')
        print(f'Hotwords   : {"ON" if hotwords else "OFF"}')
        print(f'Init prompt: {"REQUESTED (safe mode)" if initial_prompt else "OFF"}')
        if args.diarize:
            print(
                'Diarization: ON '
                f'(segmentation {args.diarization_device.upper()}, embedding/clustering CPU)'
            )
            speaker_count = 'auto' if args.num_speakers < 0 else str(args.num_speakers)
            print(f'Speakers   : {speaker_count}')
        else:
            print('Diarization: OFF')
        print()

        total_started = time.perf_counter()
        devices = ensure_device(args.device)
        steps = 6 if args.diarize else 5
        print(f'[1/{steps}] Device check OK: {devices}')

        print(f'[2/{steps}] Decoding and resampling audio to 16 kHz mono...')
        audio = decode_audio_16k_mono(input_path)
        duration = len(audio) / TARGET_SAMPLE_RATE
        print(f'      Audio length: {clock(duration)} ({duration / 60.0:.1f} min)')

        print(f'[3/{steps}] Loading/compiling model on {args.device}...')
        pipe = load_whisper_pipeline(model_dir, args.device, args.model_label)
        print('      Model ready.')

        initial_prompt_plan = prepare_initial_prompt(pipe, initial_prompt)
        config = configure_generation(
            pipe,
            args.language,
            args.beams,
            hotwords,
        )
        if hotwords:
            print(f'      Hotwords: {hotwords[:160]}{"..." if len(hotwords) > 160 else ""}')
        if initial_prompt_plan.warning:
            print(f'      [WARNING] {initial_prompt_plan.warning}')

        mode = 'Beam Search' if args.beams > 1 else 'Greedy'
        print(f'[4/{steps}] Transcribing Korean ({mode}, beams={args.beams})...')
        transcribe_started = time.perf_counter()
        segments = transcribe_windows(pipe, config, audio, duration, args.window_seconds, args.overlap_seconds)
        elapsed = time.perf_counter() - transcribe_started
        rtf = elapsed / duration if duration > 0 else 0.0
        print(f'      Transcription time: {clock(elapsed)} (RTF {rtf:.2f}x)')

        if args.diarize:
            # Release the large Whisper pipeline before loading the much smaller
            # segmentation model on the NPU. This avoids keeping both compiled
            # networks resident at the same time on memory-constrained NPUs.
            del pipe
            del config
            gc.collect()

            print(
                f'[5/{steps}] Separating speakers '
                f'(segmentation {args.diarization_device.upper()}, embedding/clustering CPU)...'
            )
            from diarization import diarize_audio
            turns = diarize_audio(
                audio,
                Path(args.diarization_segmentation_model),
                Path(args.diarization_embedding_model),
                num_speakers=args.num_speakers,
                cluster_threshold=args.speaker_threshold,
                device=args.diarization_device,
                fallback_to_cpu=not args.diarization_no_fallback,
            )
            assign_speakers(segments, turns)
            speakers = sorted({turn.speaker for turn in turns})
            print(f'      Speaker turns: {len(turns)}, detected speakers: {len(speakers)}')
            save_step = 6
        else:
            save_step = 5

        print(f'[{save_step}/{steps}] Saving TXT/SRT...')
        txt_path, srt_path = save_outputs(input_path, segments, duration, args.output_suffix, diarized=args.diarize)
        total_elapsed = time.perf_counter() - total_started

        print()
        print('=' * 78)
        print('Done')
        print(f'TXT : {txt_path}')
        print(f'SRT : {srt_path}')
        print(f'Total: {clock(total_elapsed)}')
        print('=' * 78)
        return 0
    except KeyboardInterrupt:
        print('\nStopped by user.')
        return 130
    except Exception as exc:
        print('\n[ERROR]')
        print(str(exc))
        if args.model_label.upper() == 'FP16' and args.device.upper().startswith('NPU'):
            print()
            print('If this is an NPU memory/Level Zero error, try run_fp16_compat.bat.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
