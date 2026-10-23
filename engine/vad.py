"""Lightweight, dependency-free speech-region detection for transcription.

The detector deliberately keeps its result on the source audio timeline.  Callers
must transcribe each returned region independently rather than concatenating
regions, otherwise subtitle and diarization timestamps would be compressed.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import operator
from typing import Iterable

import numpy as np


@dataclass(frozen=True)
class SpeechRegion:
    """A half-open speech interval expressed as source-audio sample offsets."""

    start_sample: int
    end_sample: int

    def __post_init__(self) -> None:
        bounds: list[int] = []
        for name, value in (
            ("start_sample", self.start_sample),
            ("end_sample", self.end_sample),
        ):
            if isinstance(value, bool):
                raise ValueError(f"{name} must be an integer")
            try:
                bounds.append(operator.index(value))
            except TypeError as exc:
                raise ValueError(f"{name} must be an integer") from exc
        if bounds[0] < 0 or bounds[1] < bounds[0]:
            raise ValueError(
                "speech region bounds must satisfy 0 <= start_sample <= end_sample"
            )
        object.__setattr__(self, "start_sample", bounds[0])
        object.__setattr__(self, "end_sample", bounds[1])

    @property
    def sample_count(self) -> int:
        return self.end_sample - self.start_sample

    def to_dict(self) -> dict[str, int]:
        return {
            "start_sample": int(self.start_sample),
            "end_sample": int(self.end_sample),
        }


def _finite_non_negative(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{name} must be a finite number at least 0")
    return value


def merge_speech_regions(regions: Iterable[SpeechRegion]) -> list[SpeechRegion]:
    """Return the sorted union of half-open speech intervals.

    Touching intervals are merged.  Keeping this small primitive public lets
    multi-input analysis combine channel evidence without duplicating the
    source-timeline interval contract used by the single-file VAD.
    """

    values = list(regions)
    if any(not isinstance(region, SpeechRegion) for region in values):
        raise TypeError("regions must contain SpeechRegion values")

    merged: list[SpeechRegion] = []
    for region in sorted(
        values,
        key=lambda item: (item.start_sample, item.end_sample),
    ):
        if region.start_sample < 0 or region.end_sample < region.start_sample:
            raise ValueError(
                "speech region bounds must satisfy 0 <= start_sample <= end_sample"
            )
        if region.sample_count == 0:
            continue
        if not merged or region.start_sample > merged[-1].end_sample:
            merged.append(region)
            continue
        previous = merged[-1]
        merged[-1] = SpeechRegion(
            previous.start_sample,
            max(previous.end_sample, region.end_sample),
        )
    return merged


def _merge_regions(regions: list[SpeechRegion]) -> list[SpeechRegion]:
    """Backward-compatible private alias used by the original detector."""

    return merge_speech_regions(regions)


def _valid_intervals(valid_mask: np.ndarray) -> list[tuple[int, int]]:
    if valid_mask.size == 0:
        return []
    padded = np.pad(valid_mask, (1, 1), constant_values=False)
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [
        (int(start), int(end))
        for start, end in zip(edges[0::2], edges[1::2])
    ]


def _detect_interval(
    samples: np.ndarray,
    sample_rate: int,
    *,
    interval_start: int,
    interval_end: int,
    threshold_db: float,
    min_speech_duration: float,
    min_silence_duration: float,
    padding_duration: float,
    frame_duration: float,
    hop_duration: float,
) -> list[SpeechRegion]:
    """Run the legacy energy detector inside one contiguous valid interval."""

    interval_samples = samples[interval_start:interval_end]
    if not interval_samples.size:
        return []

    frame_samples = max(1, int(round(frame_duration * sample_rate)))
    hop_samples = max(1, int(round(hop_duration * sample_rate)))
    starts = np.arange(0, interval_samples.size, hop_samples, dtype=np.int64)
    ends = np.minimum(starts + frame_samples, interval_samples.size)

    squares = np.square(interval_samples, dtype=np.float64)
    cumulative_energy = np.concatenate(
        (np.zeros(1, dtype=np.float64), np.cumsum(squares))
    )
    mean_energy = (
        cumulative_energy[ends] - cumulative_energy[starts]
    ) / (ends - starts)
    dbfs = 10.0 * np.log10(
        np.maximum(mean_energy, np.finfo(np.float64).tiny)
    )
    speech_indices = np.flatnonzero(dbfs >= threshold_db)
    if not speech_indices.size:
        return []

    min_silence_samples = int(round(min_silence_duration * sample_rate))
    raw_regions: list[SpeechRegion] = []
    for index in speech_indices:
        region = SpeechRegion(
            interval_start + int(starts[index]),
            interval_start + int(ends[index]),
        )
        if (
            raw_regions
            and region.start_sample - raw_regions[-1].end_sample
            <= min_silence_samples
        ):
            previous = raw_regions[-1]
            raw_regions[-1] = SpeechRegion(
                previous.start_sample,
                region.end_sample,
            )
        else:
            raw_regions.append(region)

    min_speech_samples = int(round(min_speech_duration * sample_rate))
    retained = [
        region
        for region in raw_regions
        if region.sample_count >= min_speech_samples
    ]
    padding_samples = int(round(padding_duration * sample_rate))
    return _merge_regions(
        [
            SpeechRegion(
                max(interval_start, region.start_sample - padding_samples),
                min(interval_end, region.end_sample + padding_samples),
            )
            for region in retained
        ]
    )


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
    valid_mask: np.ndarray | None = None,
) -> list[SpeechRegion]:
    """Return energy-detected speech regions using original sample positions.

    ``threshold_db`` is measured in dBFS.  Adjacent voiced runs separated by
    less than ``min_silence_duration`` are merged; then short runs are removed
    and the requested padding is added.  The default values favor retaining
    quiet Korean speech while ignoring long digital silence.  When
    ``valid_mask`` is supplied, each contiguous recorded interval is analyzed
    independently, so missing audio cannot bridge regions or receive padding.
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
    if not np.isfinite(samples).all():
        raise ValueError("audio samples must be finite")

    if valid_mask is None:
        intervals = [(0, samples.size)]
    else:
        mask = np.asarray(valid_mask)
        if mask.ndim != 1:
            raise ValueError("valid_mask must be one-dimensional")
        if mask.shape != samples.shape:
            raise ValueError("valid_mask must have the same shape as audio")
        mask = np.ascontiguousarray(mask, dtype=np.bool_)
        intervals = _valid_intervals(mask)

    if not samples.size:
        return []

    regions: list[SpeechRegion] = []
    for interval_start, interval_end in intervals:
        regions.extend(
            _detect_interval(
                samples,
                sample_rate,
                interval_start=interval_start,
                interval_end=interval_end,
                threshold_db=threshold_db,
                min_speech_duration=min_speech_duration,
                min_silence_duration=min_silence_duration,
                padding_duration=padding_duration,
                frame_duration=frame_duration,
                hop_duration=hop_duration,
            )
        )
    return _merge_regions(regions)


__all__ = [
    "SpeechRegion",
    "detect_speech_regions",
    "merge_speech_regions",
]
