#!/usr/bin/env python
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def choose_file() -> Path | None:
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    filename = filedialog.askopenfilename(
        title='Select recording for INT8 / FP16 comparison',
        filetypes=[
            ('Audio / video', '*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus *.mp4 *.mov *.mkv *.webm'),
            ('All files', '*.*'),
        ],
    )
    root.destroy()
    return Path(filename) if filename else None


def call(args: list[str]) -> int:
    return subprocess.call([sys.executable] + args)


def main() -> int:
    app = Path(__file__).resolve().parent
    audio = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else choose_file()
    if audio is None:
        return 0
    if not audio.is_file():
        print(f'[ERROR] File not found: {audio}')
        return 2

    rc = call([str(app / 'download_models.py'), 'both'])
    if rc:
        return rc

    common = [str(app / 'transcribe_npu.py'), str(audio), '--device', 'NPU', '--beams', '1']
    tests = [
        ('INT8', app / 'models' / 'whisper-large-v3-turbo-int8', '_int8'),
        ('FP16', app / 'models' / 'whisper-large-v3-turbo-fp16', '_fp16'),
    ]
    for index, (label, model, suffix) in enumerate(tests, start=1):
        print('\n' + '=' * 78)
        print(f'[{index}/2] {label} transcription')
        print('=' * 78)
        rc = call(common + [
            '--model-dir', str(model),
            '--model-label', label,
            '--output-suffix', suffix,
        ])
        if rc:
            print(f'[ERROR] {label} failed with exit code {rc}')
            return rc

    print('\nComparison complete.')
    print(audio.with_name(audio.stem + '_int8.txt'))
    print(audio.with_name(audio.stem + '_fp16.txt'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
