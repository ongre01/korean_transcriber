#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class SpeakerTurn:
    start: float
    end: float
    speaker: int


def diarize_audio(
    audio: np.ndarray,
    segmentation_model: Path,
    embedding_model: Path,
    *,
    num_speakers: int = -1,
    cluster_threshold: float = 0.5,
    min_duration_on: float = 0.3,
    min_duration_off: float = 0.5,
    show_progress: bool = True,
) -> list[SpeakerTurn]:
    """Run offline speaker diarization on 16 kHz mono float32 audio."""
    import sherpa_onnx

    segmentation_model = Path(segmentation_model).expanduser().resolve()
    embedding_model = Path(embedding_model).expanduser().resolve()

    if not segmentation_model.is_file():
        raise RuntimeError(
            f'Speaker segmentation model not found: {segmentation_model}\n'
            'Run: python download_models.py diarization'
        )
    if not embedding_model.is_file():
        raise RuntimeError(
            f'Speaker embedding model not found: {embedding_model}\n'
            'Run: python download_models.py diarization'
        )

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(segmentation_model)
            ),
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(embedding_model)
        ),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=num_speakers,
            threshold=cluster_threshold,
        ),
        min_duration_on=min_duration_on,
        min_duration_off=min_duration_off,
    )
    if not config.validate():
        raise RuntimeError(
            'Invalid speaker diarization configuration. '
            'Check the sherpa-onnx models and settings.'
        )

    diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
    samples = np.ascontiguousarray(audio, dtype=np.float32)

    last_percent = -1

    def progress_callback(processed: int, total: int) -> int:
        nonlocal last_percent
        if total <= 0:
            return 0
        percent = min(100, int(processed * 100 / total))
        if percent != last_percent:
            print(f'\r      Diarization: {percent:3d}%', end='', flush=True)
            last_percent = percent
        return 0

    if show_progress:
        result = diarizer.process(samples, callback=progress_callback).sort_by_start_time()
        print('\r      Diarization: 100%')
    else:
        result = diarizer.process(samples).sort_by_start_time()

    turns = [
        SpeakerTurn(
            start=max(0.0, float(item.start)),
            end=max(float(item.end), float(item.start)),
            speaker=int(item.speaker),
        )
        for item in result
        if float(item.end) > float(item.start)
    ]
    return turns
