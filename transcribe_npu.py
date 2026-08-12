#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import math
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

APP_DIR = Path(__file__).resolve().parent
TARGET_SAMPLE_RATE = 16000
DEFAULT_WINDOW_SECONDS = 120.0
DEFAULT_OVERLAP_SECONDS = 4.0
DEFAULT_HOTWORDS_FILE = APP_DIR / 'hotwords.txt'
DEFAULT_INITIAL_PROMPT_FILE = APP_DIR / 'initial_prompt.txt'


@dataclass
class Segment:
    start: float
    end: float
    text: str


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


def ensure_device(device: str) -> list[str]:
    import openvino as ov

    devices = list(ov.Core().available_devices)
    requested = device.upper()
    ok = any(
        str(d).upper() == requested or str(d).upper().startswith(requested + '.')
        for d in devices
    )
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
    if 0.0 < ratio < 1.0:
        eta = max(0.0, elapsed * (1.0 / ratio - 1.0))
        eta_text = f' ETA {clock(eta)}'
    else:
        eta_text = ' ETA 00:00' if ratio >= 1.0 else ''
    print(
        f'\r      [{bar}] {ratio * 100:5.1f}%  '
        f'{clock(processed)} / {clock(total)}  '
        f'window {index}/{count}{eta_text}',
        end='',
        flush=True,
    )


def transcribe_windows(pipe, config, audio: np.ndarray, duration: float, window: float, overlap: float):
    count = max(1, math.ceil(duration / window))
    segments: list[Segment] = []
    started = time.perf_counter()
    print_progress(0.0, duration, 0, count, started)

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

        print_progress(core_end, duration, idx + 1, count, started)

    print()
    segments.sort(key=lambda s: (s.start, s.end))
    full_text = '\n'.join(s.text for s in segments if s.text.strip()).strip()
    return full_text, segments


def save_outputs(input_path: Path, text: str, segments: list[Segment], duration: float, suffix: str):
    stem = input_path.stem + suffix
    txt_path = input_path.with_name(stem + '.txt')
    srt_path = input_path.with_name(stem + '.srt')

    txt_path.write_text(text.strip() + '\n', encoding='utf-8-sig')
    blocks = []
    for idx, seg in enumerate(segments, start=1):
        if not seg.text.strip():
            continue
        blocks.append(
            f'{idx}\n{srt_timestamp(seg.start)} --> {srt_timestamp(seg.end)}\n{seg.text.strip()}\n'
        )
    if not blocks and text.strip():
        blocks.append(f'1\n{srt_timestamp(0)} --> {srt_timestamp(max(duration, 0.5))}\n{text.strip()}\n')
    srt_path.write_text('\n'.join(blocks), encoding='utf-8-sig')
    return txt_path, srt_path


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
        print(' Korean Transcriber v6 - INT8 / FP16 comparison')
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
        print(f'Init prompt: {"ON" if initial_prompt else "OFF"}')
        print()

        total_started = time.perf_counter()
        devices = ensure_device(args.device)
        print(f'[1/5] Device check OK: {devices}')

        print('[2/5] Decoding and resampling audio to 16 kHz mono...')
        audio = decode_audio_16k_mono(input_path)
        duration = len(audio) / TARGET_SAMPLE_RATE
        print(f'      Audio length: {clock(duration)} ({duration / 60.0:.1f} min)')

        print(f'[3/5] Loading/compiling model on {args.device}...')
        import openvino_genai as ov_genai

        pipeline_options = {}
        if args.device.upper().startswith('NPU') or args.device.upper().startswith('GPU'):
            safe_label = (args.model_label or 'model').lower()
            safe_device = args.device.upper().replace('.', '_')
            cache_dir = APP_DIR / f'.ov_cache_{safe_label}_{safe_device}'
            cache_dir.mkdir(parents=True, exist_ok=True)
            pipeline_options['CACHE_DIR'] = str(cache_dir)

        pipe = ov_genai.WhisperPipeline(str(model_dir), args.device, **pipeline_options)
        print('      Model ready.')

        config = pipe.get_generation_config()
        config.language = args.language
        config.task = 'transcribe'
        config.return_timestamps = True
        config.num_beams = args.beams
        config.num_beam_groups = 1
        config.num_return_sequences = 1
        config.do_sample = False
        if hotwords:
            config.hotwords = hotwords
            print(f'      Hotwords: {hotwords[:160]}{"..." if len(hotwords) > 160 else ""}')
        if initial_prompt:
            config.initial_prompt = initial_prompt
            print(f'      Initial prompt: {initial_prompt[:160]}{"..." if len(initial_prompt) > 160 else ""}')

        mode = 'Beam Search' if args.beams > 1 else 'Greedy'
        print(f'[4/5] Transcribing Korean ({mode}, beams={args.beams})...')
        transcribe_started = time.perf_counter()
        full_text, segments = transcribe_windows(
            pipe, config, audio, duration, args.window_seconds, args.overlap_seconds
        )
        elapsed = time.perf_counter() - transcribe_started
        rtf = elapsed / duration if duration > 0 else 0.0
        print(f'      Transcription time: {clock(elapsed)} (RTF {rtf:.2f}x)')

        print('[5/5] Saving TXT/SRT...')
        txt_path, srt_path = save_outputs(
            input_path, full_text, segments, duration, args.output_suffix
        )
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
