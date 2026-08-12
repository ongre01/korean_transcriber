#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path
from huggingface_hub import snapshot_download

APP_DIR = Path(__file__).resolve().parent
MODELS_ROOT = APP_DIR / 'models'
MODELS = {
    'int8': {
        'repo': 'OpenVINO/whisper-large-v3-turbo-int8-ov',
        'dir': MODELS_ROOT / 'whisper-large-v3-turbo-int8',
        'label': 'INT8',
    },
    'fp16': {
        'repo': 'OpenVINO/whisper-large-v3-turbo-fp16-ov',
        'dir': MODELS_ROOT / 'whisper-large-v3-turbo-fp16',
        'label': 'FP16',
    },
}


def complete(path: Path) -> bool:
    return all((path / name).exists() for name in [
        'openvino_encoder_model.xml',
        'openvino_decoder_model.xml',
        'generation_config.json',
    ])


def download(key: str) -> None:
    info = MODELS[key]
    path = info['dir']
    label = info['label']
    if complete(path):
        print(f'[{label}] Existing model found; download skipped.')
        print(f'       {path}')
        return
    path.mkdir(parents=True, exist_ok=True)
    print('=' * 74)
    print(f'Downloading Whisper Large-v3-Turbo {label}')
    print(f'Repository : {info["repo"]}')
    print(f'Destination: {path}')
    print('=' * 74)
    snapshot_download(repo_id=info['repo'], local_dir=str(path))
    if not complete(path):
        raise RuntimeError(f'{label} download completed but required model files are missing.')
    print(f'[{label}] Download complete.')


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('precision', nargs='?', choices=['int8', 'fp16', 'both'], default='both')
    args = p.parse_args()
    MODELS_ROOT.mkdir(parents=True, exist_ok=True)
    if args.precision in ('int8', 'both'):
        download('int8')
    if args.precision in ('fp16', 'both'):
        download('fp16')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
