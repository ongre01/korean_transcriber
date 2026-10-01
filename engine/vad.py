"""Lightweight, dependency-free speech-region detection for transcription.

The detector deliberately keeps its result on the source audio timeline.  Callers
must transcribe each returned region independently rather than concatenating
regions, otherwise subtitle and diarization timestamps would be compressed.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class SpeechRegion:
    """A half-open speech interval expressed as source-audio sample offsets."""

    start_sample: int
    end_sample: int

    @property
    def sample_count(self) -> int:
        return self.end_sample - self.start_sample


def _finite_non_negative(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{name} must be a finite number at least 0")
    return value


def _merge_regions(regions: list[SpeechRegion]) -> list[SpeechRegion]:
    merged: list[SpeechRegion] = []
    for region in regions:
        if not merged or region.start_sample > merged[-1].end_sample:
            merged.append(region)
            continue
        previous = merged[-1]
        merged[-1] = SpeechRegion(
            previous.start_sample,
            max(previous.end_sample, region.end_sample),
        )
    return merged


def detect_speech_regions(
    audio: np.ndarray,
    sample_rate: int,
    *,
    threshold_db: float = -45.0,
    min_speech_duration: float = 0.3,
    min_silence_duration: float = 0.5,
    padding_duration: float = 0.2,
    frame_duration: float = 0.03,
    hop_duration: float = 0.01,
) -> list[SpeechRegion]:
    """Return energy-detected speech regions using original sample positions.

    ``threshold_db`` is measured in dBFS.  Adjacent voiced runs separated by
    less than ``min_silence_duration`` are merged; then short runs are removed
    and the requested padding is added.  The default values favor retaining
    quiet Korean speech while ignoring long digital silence.
    """

    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    threshold_db = float(threshold_db)
    if not math.isfinite(threshold_db) or threshold_db < -100.0 or threshold_db >= 0.0:
        raise ValueError("threshold_db must be finite, at least -100, and below 0")
    min_speech_duration = _finite_non_negative(
        "min_speech_duration", min_speech_duration)
    min_silence_duration = _finite_non_negative(
        "min_silence_duration", min_silence_duration)
    padding_duration = _finite_non_negative("padding_duration", padding_duration)
    frame_duration = _finite_non_negative("frame_duration", frame_duration)
    hop_duration = _finite_non_negative("hop_duration", hop_duration)
    if frame_duration == 0.0 or hop_duration == 0.0:
        raise ValueError("frame_duration and hop_duration must be greater than 0")

    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    if not samples.size:
        return []
    if not np.isfinite(samples).all():
        raise ValueError("audio samples must be finite")

    frame_samples = max(1, int(round(frame_duration * sample_rate)))
    hop_samples = max(1, int(round(hop_duration * sample_rate)))
    starts = np.arange(0, samples.size, hop_samples, dtype=np.int64)
    ends = np.minimum(starts + frame_samples, samples.size)

    squares = np.square(samples, dtype=np.float64)
    cumulative_energy = np.concatenate((np.zeros(1, dtype=np.float64), np.cumsum(squares)))
    mean_energy = (cumulative_energy[ends] - cumulative_energy[starts]) / (ends - starts)
    dbfs = 10.0 * np.log10(np.maximum(mean_energy, np.finfo(np.float64).tiny))
    speech_indices = np.flatnonzero(dbfs >= threshold_db)
    if not speech_indices.size:
        return []

    min_silence_samples = int(round(min_silence_duration * sample_rate))
    raw_regions: list[SpeechRegion] = []
    for index in speech_indices:
        region = SpeechRegion(int(starts[index]), int(ends[index]))
        if raw_regions and region.start_sample - raw_regions[-1].end_sample <= min_silence_samples:
            previous = raw_regions[-1]
            raw_regions[-1] = SpeechRegion(previous.start_sample, region.end_sample)
        else:
            raw_regions.append(region)

    min_speech_samples = int(round(min_speech_duration * sample_rate))
    retained = [region for region in raw_regions if region.sample_count >= min_speech_samples]
    padding_samples = int(round(padding_duration * sample_rate))
    padded = [
        SpeechRegion(
            max(0, region.start_sample - padding_samples),
            min(samples.size, region.end_sample + padding_samples),
        )
        for region in retained
    ]
    return _merge_regions(padded)
