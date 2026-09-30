#!/usr/bin/env python
from __future__ import annotations

import argparse
import shutil
import tarfile
import urllib.request
from pathlib import Path

from huggingface_hub import snapshot_download

APP_DIR = Path(__file__).resolve().parent
MODELS_ROOT = APP_DIR / 'models'
DIARIZATION_ROOT = MODELS_ROOT / 'diarization'

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

SEGMENTATION_URL = (
    'https://github.com/k2-fsa/sherpa-onnx/releases/download/'
    'speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2'
)
SEGMENTATION_ARCHIVE = DIARIZATION_ROOT / 'sherpa-onnx-pyannote-segmentation-3-0.tar.bz2'
SEGMENTATION_DIR = DIARIZATION_ROOT / 'sherpa-onnx-pyannote-segmentation-3-0'
SEGMENTATION_MODEL = SEGMENTATION_DIR / 'model.onnx'

EMBEDDING_URL = (
    'https://github.com/k2-fsa/sherpa-onnx/releases/download/'
    'speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx'
)
EMBEDDING_MODEL = DIARIZATION_ROOT / '3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx'


def complete(path: Path) -> bool:
    return all((path / name).exists() for name in [
        'openvino_encoder_model.xml',
        'openvino_decoder_model.xml',
        'generation_config.json',
    ])


def download_whisper(key: str) -> None:
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


def download_file(url: str, destination: Path) -> None:
    if destination.is_file() and destination.stat().st_size > 0:
        print(f'[SKIP] Existing file: {destination.name}')
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + '.part')
    print(f'Downloading: {destination.name}')
    try:
        with urllib.request.urlopen(url) as response, partial.open('wb') as out:
            shutil.copyfileobj(response, out)
        partial.replace(destination)
    finally:
        if partial.exists():
            partial.unlink()


def download_diarization() -> None:
    DIARIZATION_ROOT.mkdir(parents=True, exist_ok=True)
    print('=' * 74)
    print('Preparing sherpa-onnx speaker diarization models')
    print(f'Destination: {DIARIZATION_ROOT}')
    print('=' * 74)

    if not SEGMENTATION_MODEL.is_file():
        download_file(SEGMENTATION_URL, SEGMENTATION_ARCHIVE)
        print('Extracting speaker segmentation model...')
        with tarfile.open(SEGMENTATION_ARCHIVE, mode='r:bz2') as archive:
            archive.extractall(DIARIZATION_ROOT)
        if not SEGMENTATION_MODEL.is_file():
            raise RuntimeError('Speaker segmentation model extraction failed.')
    else:
        print(f'[SKIP] Existing segmentation model: {SEGMENTATION_MODEL}')

    download_file(EMBEDDING_URL, EMBEDDING_MODEL)
    if not EMBEDDING_MODEL.is_file():
        raise RuntimeError('Speaker embedding model download failed.')

    print('[Diarization] Models ready.')
    print(f'  Segmentation: {SEGMENTATION_MODEL}')
    print(f'  Embedding   : {EMBEDDING_MODEL}')


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        'precision',
        nargs='?',
        choices=['int8', 'fp16', 'both', 'diarization', 'all'],
        default='both',
    )
    args = p.parse_args()
    MODELS_ROOT.mkdir(parents=True, exist_ok=True)

    if args.precision in ('int8', 'both', 'all'):
        download_whisper('int8')
    if args.precision in ('fp16', 'both', 'all'):
        download_whisper('fp16')
    if args.precision in ('diarization', 'all'):
        download_diarization()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
