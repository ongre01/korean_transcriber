"""Speech-candidate analysis on an aligned multi-microphone timeline.

This module is the boundary between time alignment and channel-quality
selection.  It deliberately keeps three concepts separate:

* recorded audio, represented by each :class:`AnalysisSignal` validity mask;
* raw energy activity from the existing VAD; and
* likely speech after conservative, diagnosable noise screening.

The distinction prevents missing timeline padding from being called silence
and prevents stationary broadband noise from being asserted as speech merely
because it is loud.  Raw activity is retained as an uncertain candidate so a
later quality or manual-review stage can recover false negatives.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real
import operator
from typing import Iterable, Sequence

import numpy as np

from .align import MultiAudioAlignment
from .pipeline import (
    AnalysisSignal,
    PreparedMultiAudio,
    warp_signal_to_reference_timeline,
)

try:  # Support both ``engine.multimic`` and the application's top-level layout.
    from ..vad import SpeechRegion, detect_speech_regions, merge_speech_regions
except ImportError:  # pragma: no cover - exercised by the application's import layout
    from vad import (  # type: ignore[no-redef]
        SpeechRegion,
        detect_speech_regions,
        merge_speech_regions,
    )


@dataclass(frozen=True)
class SpeechAnalysisConfig:
    """Configuration for VAD union, analysis windows, and noise diagnostics."""

    window_seconds: float = 3.0
    hop_seconds: float = 1.0
    threshold_db: float = -45.0
    min_speech_duration: float = 0.3
    min_silence_duration: float = 0.5
    padding_duration: float = 0.2
    frame_duration: float = 0.03
    vad_hop_duration: float = 0.01
    noise_frame_duration: float = 0.03
    noise_min_region_duration: float = 0.3
    noise_min_frames: int = 3
    noise_max_frames: int = 96
    noise_flatness_threshold: float = 0.45
    noise_zero_crossing_threshold: float = 0.12
    stationary_energy_spread_db: float = 6.0
    dc_ac_ratio_threshold: float = 0.05
    tonal_flatness_threshold: float = 0.10
    tonal_energy_spread_db: float = 6.0

    def __post_init__(self) -> None:
        for name in (
            "window_seconds",
            "hop_seconds",
            "frame_duration",
            "vad_hop_duration",
            "noise_frame_duration",
        ):
            _require_positive(name, getattr(self, name))
        for name in (
            "min_speech_duration",
            "min_silence_duration",
            "padding_duration",
            "noise_min_region_duration",
            "stationary_energy_spread_db",
            "tonal_energy_spread_db",
        ):
            _require_non_negative(name, getattr(self, name))
        if self.hop_seconds > self.window_seconds:
            raise ValueError("hop_seconds must not exceed window_seconds")

        threshold = _finite_real("threshold_db", self.threshold_db)
        if threshold < -100.0 or threshold >= 0.0:
            raise ValueError(
                "threshold_db must be finite, at least -100, and below 0"
            )
        _require_unit_interval(
            "noise_flatness_threshold", self.noise_flatness_threshold
        )
        _require_unit_interval(
            "noise_zero_crossing_threshold",
            self.noise_zero_crossing_threshold,
        )
        _require_unit_interval(
            "dc_ac_ratio_threshold", self.dc_ac_ratio_threshold
        )
        _require_unit_interval(
            "tonal_flatness_threshold", self.tonal_flatness_threshold
        )
        _require_integer_at_least("noise_min_frames", self.noise_min_frames, 1)
        _require_integer_at_least("noise_max_frames", self.noise_max_frames, 1)
        if self.noise_min_frames > self.noise_max_frames:
            raise ValueError("noise_min_frames must not exceed noise_max_frames")

    def to_dict(self) -> dict[str, object]:
        return {
            "window_seconds": float(self.window_seconds),
            "hop_seconds": float(self.hop_seconds),
            "threshold_db": float(self.threshold_db),
            "min_speech_duration": float(self.min_speech_duration),
            "min_silence_duration": float(self.min_silence_duration),
            "padding_duration": float(self.padding_duration),
            "frame_duration": float(self.frame_duration),
            "vad_hop_duration": float(self.vad_hop_duration),
            "noise_frame_duration": float(self.noise_frame_duration),
            "noise_min_region_duration": float(
                self.noise_min_region_duration
            ),
            "noise_min_frames": int(self.noise_min_frames),
            "noise_max_frames": int(self.noise_max_frames),
            "noise_flatness_threshold": float(
                self.noise_flatness_threshold
            ),
            "noise_zero_crossing_threshold": (
                float(self.noise_zero_crossing_threshold)
            ),
            "stationary_energy_spread_db": float(
                self.stationary_energy_spread_db
            ),
            "dc_ac_ratio_threshold": float(self.dc_ac_ratio_threshold),
            "tonal_flatness_threshold": float(
                self.tonal_flatness_threshold
            ),
            "tonal_energy_spread_db": float(self.tonal_energy_spread_db),
        }


@dataclass(frozen=True)
class RegionSpeechDiagnostic:
    """Traceable decision for one raw VAD activity region."""

    region: SpeechRegion
    decision_code: str
    rms_dbfs: float | None
    spectral_flatness: float | None
    zero_crossing_rate: float | None
    energy_spread_db: float | None
    ac_to_total_rms_ratio: float | None
    analyzed_frame_count: int

    @property
    def noise_suspected(self) -> bool:
        return self.decision_code in {
            "constant_signal",
            "stationary_broadband_noise",
            "stationary_tonal_activity",
        }

    @property
    def speech_likely(self) -> bool:
        return self.decision_code == "speech_candidate"

    def to_dict(self) -> dict[str, object]:
        return {
            **self.region.to_dict(),
            "decision_code": self.decision_code,
            "noise_suspected": self.noise_suspected,
            "speech_likely": self.speech_likely,
            "rms_dbfs": self.rms_dbfs,
            "spectral_flatness": self.spectral_flatness,
            "zero_crossing_rate": self.zero_crossing_rate,
            "energy_spread_db": self.energy_spread_db,
            "ac_to_total_rms_ratio": self.ac_to_total_rms_ratio,
            "analyzed_frame_count": self.analyzed_frame_count,
        }


@dataclass(frozen=True)
class InputSpeechEvidence:
    """Speech and availability evidence for one input on the reference axis."""

    input_index: int
    valid_regions: tuple[SpeechRegion, ...]
    activity_regions: tuple[SpeechRegion, ...]
    speech_regions: tuple[SpeechRegion, ...]
    noise_suspected_regions: tuple[SpeechRegion, ...]
    region_diagnostics: tuple[RegionSpeechDiagnostic, ...]
    valid_sample_count: int
    activity_sample_count: int
    speech_sample_count: int
    activity_ratio: float | None
    speech_ratio: float | None
    diagnostic_codes: tuple[str, ...]

    @property
    def available(self) -> bool:
        return self.valid_sample_count > 0

    @property
    def raw_activity_regions(self) -> tuple[SpeechRegion, ...]:
        return self.activity_regions

    @property
    def noise_regions(self) -> tuple[SpeechRegion, ...]:
        return self.noise_suspected_regions

    def to_dict(self) -> dict[str, object]:
        return {
            "input_index": self.input_index,
            "available": self.available,
            "valid_regions": [region.to_dict() for region in self.valid_regions],
            "activity_regions": [
                region.to_dict() for region in self.activity_regions
            ],
            "speech_regions": [
                region.to_dict() for region in self.speech_regions
            ],
            "noise_suspected_regions": [
                region.to_dict() for region in self.noise_suspected_regions
            ],
            "region_diagnostics": [
                diagnostic.to_dict()
                for diagnostic in self.region_diagnostics
            ],
            "valid_sample_count": self.valid_sample_count,
            "activity_sample_count": self.activity_sample_count,
            "speech_sample_count": self.speech_sample_count,
            "activity_ratio": self.activity_ratio,
            "speech_ratio": self.speech_ratio,
            "diagnostic_codes": list(self.diagnostic_codes),
        }


@dataclass(frozen=True)
class SpeechCandidateRegion:
    """A piecewise-union candidate with the input evidence kept explicit."""

    region: SpeechRegion
    activity_input_indices: tuple[int, ...]
    supporting_input_indices: tuple[int, ...]
    valid_input_indices: tuple[int, ...]
    status: str
    diagnostic_codes: tuple[str, ...]

    @property
    def start_sample(self) -> int:
        return self.region.start_sample

    @property
    def end_sample(self) -> int:
        return self.region.end_sample

    @property
    def sample_count(self) -> int:
        return self.region.sample_count

    @property
    def speech_likely(self) -> bool:
        return self.status != "noise_risk"

    @property
    def confirmed(self) -> bool:
        """Compatibility name for consumers separating noise-risk activity."""

        return self.speech_likely

    @property
    def supporting_input_indexes(self) -> tuple[int, ...]:
        return self.supporting_input_indices

    def to_dict(self) -> dict[str, object]:
        return {
            **self.region.to_dict(),
            "activity_input_indices": list(self.activity_input_indices),
            "supporting_input_indices": list(self.supporting_input_indices),
            "valid_input_indices": list(self.valid_input_indices),
            "status": self.status,
            "speech_likely": self.speech_likely,
            "diagnostic_codes": list(self.diagnostic_codes),
        }


@dataclass(frozen=True)
class WindowInputEvidence:
    """Availability and speech state for one input in one feature window."""

    input_index: int
    status: str
    valid_sample_count: int
    activity_sample_count: int
    speech_sample_count: int
    noise_suspected_sample_count: int
    valid_ratio: float
    diagnostic_codes: tuple[str, ...]

    @property
    def available(self) -> bool:
        return self.valid_sample_count > 0

    @property
    def speech_likely(self) -> bool:
        return self.speech_sample_count > 0

    def to_dict(self) -> dict[str, object]:
        return {
            "input_index": self.input_index,
            "status": self.status,
            "available": self.available,
            "speech_likely": self.speech_likely,
            "valid_sample_count": self.valid_sample_count,
            "activity_sample_count": self.activity_sample_count,
            "speech_sample_count": self.speech_sample_count,
            "noise_suspected_sample_count": (
                self.noise_suspected_sample_count
            ),
            "valid_ratio": self.valid_ratio,
            "diagnostic_codes": list(self.diagnostic_codes),
        }


@dataclass(frozen=True)
class SpeechAnalysisWindow:
    """A configurable feature-calculation unit on the reference timeline."""

    region: SpeechRegion
    inputs: tuple[WindowInputEvidence, ...]
    valid_input_indices: tuple[int, ...]
    activity_input_indices: tuple[int, ...]
    speech_input_indices: tuple[int, ...]
    noise_suspected_input_indices: tuple[int, ...]
    candidate_overlap_samples: int
    speech_overlap_samples: int

    @property
    def start_sample(self) -> int:
        return self.region.start_sample

    @property
    def end_sample(self) -> int:
        return self.region.end_sample

    @property
    def sample_count(self) -> int:
        return self.region.sample_count

    @property
    def has_speech_candidate(self) -> bool:
        return self.candidate_overlap_samples > 0

    @property
    def has_likely_speech(self) -> bool:
        return self.speech_overlap_samples > 0

    @property
    def unavailable_input_indices(self) -> tuple[int, ...]:
        valid = set(self.valid_input_indices)
        return tuple(
            item.input_index for item in self.inputs if item.input_index not in valid
        )

    def evidence_for_input(self, input_index: int) -> WindowInputEvidence:
        index = _index("input_index", input_index)
        for item in self.inputs:
            if item.input_index == index:
                return item
        raise IndexError(f"no window evidence for input index {index}")

    def to_dict(self) -> dict[str, object]:
        return {
            **self.region.to_dict(),
            "valid_input_indices": list(self.valid_input_indices),
            "activity_input_indices": list(self.activity_input_indices),
            "speech_input_indices": list(self.speech_input_indices),
            "noise_suspected_input_indices": list(
                self.noise_suspected_input_indices
            ),
            "candidate_overlap_samples": self.candidate_overlap_samples,
            "speech_overlap_samples": self.speech_overlap_samples,
            "has_speech_candidate": self.has_speech_candidate,
            "has_likely_speech": self.has_likely_speech,
            "inputs": [item.to_dict() for item in self.inputs],
        }


@dataclass(frozen=True)
class SpeechAnalysisWarning:
    code: str
    message: str
    input_index: int | None = None
    region: SpeechRegion | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "input_index": self.input_index,
            "start_sample": (
                self.region.start_sample if self.region is not None else None
            ),
            "end_sample": (
                self.region.end_sample if self.region is not None else None
            ),
        }


@dataclass(frozen=True)
class SpeechActivityReport:
    """Complete MM-DEV-007 output on the canonical reference timeline."""

    sample_rate: int
    timeline_sample_count: int
    reference_index: int
    config: SpeechAnalysisConfig
    inputs: tuple[InputSpeechEvidence, ...]
    candidates: tuple[SpeechCandidateRegion, ...]
    speech_regions: tuple[SpeechRegion, ...]
    uncertain_regions: tuple[SpeechRegion, ...]
    windows: tuple[SpeechAnalysisWindow, ...]
    warnings: tuple[SpeechAnalysisWarning, ...]

    @property
    def per_input(self) -> tuple[InputSpeechEvidence, ...]:
        return self.inputs

    @property
    def candidate_regions(self) -> tuple[SpeechCandidateRegion, ...]:
        return self.candidates

    @property
    def union_regions(self) -> tuple[SpeechRegion, ...]:
        """High-recall union, including candidates marked as noise risks."""

        return tuple(
            merge_speech_regions(candidate.region for candidate in self.candidates)
        )

    @property
    def confirmed_regions(self) -> tuple[SpeechRegion, ...]:
        return self.speech_regions

    @property
    def analysis_windows(self) -> tuple[SpeechAnalysisWindow, ...]:
        return self.windows

    def input_evidence(self, input_index: int) -> InputSpeechEvidence:
        index = _index("input_index", input_index)
        for item in self.inputs:
            if item.input_index == index:
                return item
        raise IndexError(f"no speech evidence for input index {index}")

    def to_dict(self) -> dict[str, object]:
        return {
            "sample_rate": int(self.sample_rate),
            "timeline_sample_count": int(self.timeline_sample_count),
            "reference_index": int(self.reference_index),
            "config": self.config.to_dict(),
            "inputs": [item.to_dict() for item in self.inputs],
            "candidates": [item.to_dict() for item in self.candidates],
            "union_regions": [
                region.to_dict() for region in self.union_regions
            ],
            "speech_regions": [
                region.to_dict() for region in self.speech_regions
            ],
            "uncertain_regions": [
                region.to_dict() for region in self.uncertain_regions
            ],
            "windows": [window.to_dict() for window in self.windows],
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


def _finite_real(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _require_positive(name: str, value: object) -> float:
    result = _finite_real(name, value)
    if result <= 0.0:
        raise ValueError(f"{name} must be greater than 0")
    return result


def _require_non_negative(name: str, value: object) -> float:
    result = _finite_real(name, value)
    if result < 0.0:
        raise ValueError(f"{name} must be at least 0")
    return result


def _require_unit_interval(name: str, value: object) -> float:
    result = _finite_real(name, value)
    if result < 0.0 or result > 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return result


def _require_integer_at_least(name: str, value: object, minimum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer at least {minimum}")
    try:
        result = operator.index(value)
    except TypeError as exc:
        raise ValueError(
            f"{name} must be an integer at least {minimum}"
        ) from exc
    if result < minimum:
        raise ValueError(f"{name} must be an integer at least {minimum}")
    return result


def _seconds_to_samples(
    name: str,
    seconds: object,
    sample_rate: int,
    *,
    minimum: int = 0,
) -> int:
    value = _finite_real(name, seconds)
    scaled = value * int(sample_rate)
    if not math.isfinite(scaled) or scaled > np.iinfo(np.int64).max:
        raise ValueError(f"{name} is too large for the analysis sample rate")
    return max(minimum, int(round(scaled)))


def _validate_config_sample_scale(
    config: SpeechAnalysisConfig,
    sample_rate: int,
) -> None:
    for name in (
        "window_seconds",
        "hop_seconds",
        "min_speech_duration",
        "min_silence_duration",
        "padding_duration",
        "frame_duration",
        "vad_hop_duration",
        "noise_frame_duration",
        "noise_min_region_duration",
    ):
        _seconds_to_samples(name, getattr(config, name), sample_rate)


def _index(name: str, value: object) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        return operator.index(value)
    except TypeError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _overlap_samples(
    regions: Iterable[SpeechRegion],
    start_sample: int,
    end_sample: int,
) -> int:
    return sum(
        max(
            0,
            min(end_sample, region.end_sample)
            - max(start_sample, region.start_sample),
        )
        for region in regions
    )


def _overlaps(
    regions: Iterable[SpeechRegion],
    start_sample: int,
    end_sample: int,
) -> bool:
    return any(
        region.start_sample < end_sample and region.end_sample > start_sample
        for region in regions
    )


def _valid_regions(signal: AnalysisSignal) -> tuple[SpeechRegion, ...]:
    return tuple(
        SpeechRegion(start, end)
        for start, end in signal.valid_intervals()
    )


def _region_sample_count(regions: Iterable[SpeechRegion]) -> int:
    return sum(region.sample_count for region in regions)


def _dbfs_from_rms(rms: float) -> float | None:
    if not math.isfinite(rms) or rms <= 0.0:
        return None
    return float(20.0 * math.log10(rms))


def _diagnostic_frame_starts(
    sample_count: int,
    frame_samples: int,
    max_frames: int,
) -> tuple[int, ...]:
    if sample_count <= 0:
        return ()
    if sample_count <= frame_samples:
        return (0,)
    last_start = sample_count - frame_samples
    starts = np.arange(0, last_start + 1, frame_samples, dtype=np.int64)
    if starts[-1] != last_start:
        starts = np.append(starts, last_start)
    if starts.size > max_frames:
        selected = np.linspace(0, starts.size - 1, max_frames, dtype=np.int64)
        starts = starts[selected]
    return tuple(int(value) for value in starts)


def _region_diagnostic(
    signal: AnalysisSignal,
    region: SpeechRegion,
    config: SpeechAnalysisConfig,
) -> RegionSpeechDiagnostic:
    interval = slice(region.start_sample, region.end_sample)
    valid_samples = signal.samples[interval][signal.valid_mask[interval]]
    if valid_samples.size:
        rms = float(
            np.sqrt(np.mean(np.square(valid_samples, dtype=np.float64)))
        )
    else:
        rms = 0.0

    frame_samples = _seconds_to_samples(
        "noise_frame_duration",
        config.noise_frame_duration,
        signal.sample_rate,
        minimum=8,
    )
    frame_dbfs: list[float] = []
    flatness_values: list[float] = []
    crossing_values: list[float] = []
    ac_ratio_values: list[float] = []
    region_samples = signal.samples[interval]
    region_mask = signal.valid_mask[interval]
    for start in _diagnostic_frame_starts(
        region.sample_count,
        frame_samples,
        config.noise_max_frames,
    ):
        end = min(region.sample_count, start + frame_samples)
        values = region_samples[start:end]
        valid = region_mask[start:end]
        if values.size < 8 or np.count_nonzero(valid) < values.size * 0.8:
            continue
        values = values[valid].astype(np.float64, copy=False)
        frame_rms = float(np.sqrt(np.mean(np.square(values))))
        frame_level = _dbfs_from_rms(frame_rms)
        if frame_level is None or frame_level < config.threshold_db:
            continue
        frame_dbfs.append(frame_level)

        centered = values - float(np.mean(values))
        centered_rms = float(np.sqrt(np.mean(np.square(centered))))
        ac_ratio_values.append(
            min(1.0, max(0.0, centered_rms / max(frame_rms, 1.0e-12)))
        )
        if centered_rms <= np.finfo(np.float64).tiny:
            flatness_values.append(0.0)
            crossing_values.append(0.0)
            continue

        windowed = centered * np.hanning(centered.size)
        power = np.square(np.abs(np.fft.rfft(windowed)), dtype=np.float64)
        if power.size > 1:
            power = power[1:]
        mean_power = float(np.mean(power)) if power.size else 0.0
        if mean_power <= np.finfo(np.float64).tiny:
            flatness = 0.0
        else:
            floor = max(
                np.finfo(np.float64).tiny,
                mean_power * 1.0e-12,
            )
            flatness = float(
                np.exp(np.mean(np.log(np.maximum(power, floor))))
                / mean_power
            )
        flatness_values.append(min(1.0, max(0.0, flatness)))

        signs = np.signbit(centered)
        crossings = int(np.count_nonzero(signs[1:] != signs[:-1]))
        crossing_values.append(crossings / max(1, centered.size - 1))

    flatness = (
        float(np.median(flatness_values)) if flatness_values else None
    )
    zero_crossing = (
        float(np.median(crossing_values)) if crossing_values else None
    )
    energy_spread = (
        float(np.percentile(frame_dbfs, 90) - np.percentile(frame_dbfs, 10))
        if frame_dbfs
        else None
    )
    ac_ratio = (
        float(np.median(ac_ratio_values)) if ac_ratio_values else None
    )
    duration = region.sample_count / signal.sample_rate
    enough_stationary_evidence = (
        duration >= config.noise_min_region_duration
        and len(frame_dbfs) >= config.noise_min_frames
        and energy_spread is not None
    )
    constant_signal = (
        enough_stationary_evidence
        and ac_ratio is not None
        and ac_ratio <= config.dc_ac_ratio_threshold
    )
    stationary_broadband_noise = (
        enough_stationary_evidence
        and flatness is not None
        and flatness >= config.noise_flatness_threshold
        and zero_crossing is not None
        and zero_crossing >= config.noise_zero_crossing_threshold
        and energy_spread is not None
        and energy_spread <= config.stationary_energy_spread_db
    )
    stationary_tonal_activity = (
        enough_stationary_evidence
        and not constant_signal
        and flatness is not None
        and flatness <= config.tonal_flatness_threshold
        and energy_spread is not None
        and energy_spread <= config.tonal_energy_spread_db
    )
    if constant_signal:
        decision_code = "constant_signal"
    elif stationary_broadband_noise:
        decision_code = "stationary_broadband_noise"
    elif stationary_tonal_activity:
        decision_code = "stationary_tonal_activity"
    else:
        decision_code = "speech_candidate"
    return RegionSpeechDiagnostic(
        region=region,
        decision_code=decision_code,
        rms_dbfs=_dbfs_from_rms(rms),
        spectral_flatness=flatness,
        zero_crossing_rate=zero_crossing,
        energy_spread_db=energy_spread,
        ac_to_total_rms_ratio=ac_ratio,
        analyzed_frame_count=len(frame_dbfs),
    )


def _analyze_input(
    signal: AnalysisSignal,
    input_index: int,
    config: SpeechAnalysisConfig,
) -> InputSpeechEvidence:
    valid_regions = _valid_regions(signal)
    valid_sample_count = signal.valid_sample_count
    activity_regions = tuple(
        detect_speech_regions(
            signal.samples,
            signal.sample_rate,
            threshold_db=config.threshold_db,
            min_speech_duration=config.min_speech_duration,
            min_silence_duration=config.min_silence_duration,
            padding_duration=config.padding_duration,
            frame_duration=config.frame_duration,
            hop_duration=config.vad_hop_duration,
            valid_mask=signal.valid_mask,
        )
    )
    diagnostics = tuple(
        _region_diagnostic(signal, region, config)
        for region in activity_regions
    )
    speech_regions = tuple(
        merge_speech_regions(
            diagnostic.region
            for diagnostic in diagnostics
            if diagnostic.speech_likely
        )
    )
    noise_regions = tuple(
        merge_speech_regions(
            diagnostic.region
            for diagnostic in diagnostics
            if diagnostic.noise_suspected
        )
    )
    activity_sample_count = _region_sample_count(activity_regions)
    speech_sample_count = _region_sample_count(speech_regions)
    diagnostic_codes: list[str] = []
    if valid_sample_count == 0:
        diagnostic_codes.append("no_valid_audio")
    elif not activity_regions:
        diagnostic_codes.append("no_energy_activity")
    if noise_regions:
        diagnostic_codes.append(
            "noise_only_activity_suspected"
            if not speech_regions
            else "noise_activity_suspected"
        )
        diagnostic_codes.extend(
            diagnostic.decision_code
            for diagnostic in diagnostics
            if diagnostic.noise_suspected
        )
    diagnostic_codes = list(dict.fromkeys(diagnostic_codes))

    return InputSpeechEvidence(
        input_index=input_index,
        valid_regions=valid_regions,
        activity_regions=activity_regions,
        speech_regions=speech_regions,
        noise_suspected_regions=noise_regions,
        region_diagnostics=diagnostics,
        valid_sample_count=valid_sample_count,
        activity_sample_count=activity_sample_count,
        speech_sample_count=speech_sample_count,
        activity_ratio=(
            activity_sample_count / valid_sample_count
            if valid_sample_count
            else None
        ),
        speech_ratio=(
            speech_sample_count / valid_sample_count
            if valid_sample_count
            else None
        ),
        diagnostic_codes=tuple(diagnostic_codes),
    )


def _candidate_attributes(
    evidence: Sequence[InputSpeechEvidence],
    start_sample: int,
    end_sample: int,
) -> tuple[
    tuple[int, ...],
    tuple[int, ...],
    tuple[int, ...],
    str,
    tuple[str, ...],
]:
    activity_inputs = tuple(
        item.input_index
        for item in evidence
        if _overlaps(item.activity_regions, start_sample, end_sample)
    )
    supporting_inputs = tuple(
        item.input_index
        for item in evidence
        if _overlaps(item.speech_regions, start_sample, end_sample)
    )
    valid_inputs = tuple(
        item.input_index
        for item in evidence
        if _overlaps(item.valid_regions, start_sample, end_sample)
    )
    noise_inputs = tuple(
        item.input_index
        for item in evidence
        if _overlaps(item.noise_suspected_regions, start_sample, end_sample)
    )

    codes: list[str] = []
    if len(supporting_inputs) >= 2:
        status = "corroborated"
        codes.append("multiple_input_support")
    elif len(supporting_inputs) == 1:
        status = "single_input"
        codes.append("single_input_support")
    else:
        status = "noise_risk"
        codes.append("noise_like_activity_only")
    if noise_inputs:
        codes.append("noise_suspected_input")
    return (
        activity_inputs,
        supporting_inputs,
        valid_inputs,
        status,
        tuple(codes),
    )


def _build_candidates(
    evidence: Sequence[InputSpeechEvidence],
) -> tuple[SpeechCandidateRegion, ...]:
    endpoints = sorted(
        {
            boundary
            for item in evidence
            for region in item.activity_regions
            for boundary in (region.start_sample, region.end_sample)
        }
        | {
            boundary
            for item in evidence
            for region in item.speech_regions
            for boundary in (region.start_sample, region.end_sample)
        }
        | {
            boundary
            for item in evidence
            for region in item.valid_regions
            for boundary in (region.start_sample, region.end_sample)
        }
    )
    candidates: list[SpeechCandidateRegion] = []
    for start_sample, end_sample in zip(endpoints, endpoints[1:]):
        if end_sample <= start_sample:
            continue
        attributes = _candidate_attributes(
            evidence,
            start_sample,
            end_sample,
        )
        if not attributes[0]:
            continue
        candidate = SpeechCandidateRegion(
            region=SpeechRegion(start_sample, end_sample),
            activity_input_indices=attributes[0],
            supporting_input_indices=attributes[1],
            valid_input_indices=attributes[2],
            status=attributes[3],
            diagnostic_codes=attributes[4],
        )
        if candidates:
            previous = candidates[-1]
            same_evidence = (
                previous.end_sample == candidate.start_sample
                and previous.activity_input_indices
                == candidate.activity_input_indices
                and previous.supporting_input_indices
                == candidate.supporting_input_indices
                and previous.valid_input_indices == candidate.valid_input_indices
                and previous.status == candidate.status
                and previous.diagnostic_codes == candidate.diagnostic_codes
            )
            if same_evidence:
                candidates[-1] = SpeechCandidateRegion(
                    region=SpeechRegion(
                        previous.start_sample,
                        candidate.end_sample,
                    ),
                    activity_input_indices=previous.activity_input_indices,
                    supporting_input_indices=previous.supporting_input_indices,
                    valid_input_indices=previous.valid_input_indices,
                    status=previous.status,
                    diagnostic_codes=previous.diagnostic_codes,
                )
                continue
        candidates.append(candidate)
    return tuple(candidates)


def _window_bounds(
    timeline_sample_count: int,
    window_samples: int,
    hop_samples: int,
) -> tuple[SpeechRegion, ...]:
    """Create fixed-hop full windows plus at most one partial tail window."""

    if timeline_sample_count == 0:
        return ()
    if timeline_sample_count <= window_samples:
        return (SpeechRegion(0, timeline_sample_count),)

    final_start = timeline_sample_count - window_samples
    starts = list(range(0, final_start + 1, hop_samples))
    windows = [
        SpeechRegion(start, start + window_samples)
        for start in starts
    ]
    if windows[-1].end_sample < timeline_sample_count:
        tail_start = starts[-1] + hop_samples
        windows.append(SpeechRegion(tail_start, timeline_sample_count))
    return tuple(windows)


def _build_windows(
    evidence: Sequence[InputSpeechEvidence],
    candidates: Sequence[SpeechCandidateRegion],
    speech_regions: Sequence[SpeechRegion],
    *,
    timeline_sample_count: int,
    sample_rate: int,
    config: SpeechAnalysisConfig,
) -> tuple[SpeechAnalysisWindow, ...]:
    window_samples = _seconds_to_samples(
        "window_seconds",
        config.window_seconds,
        sample_rate,
        minimum=1,
    )
    hop_samples = _seconds_to_samples(
        "hop_seconds",
        config.hop_seconds,
        sample_rate,
        minimum=1,
    )
    bounds = _window_bounds(
        timeline_sample_count,
        window_samples,
        hop_samples,
    )
    candidate_regions = tuple(candidate.region for candidate in candidates)
    windows: list[SpeechAnalysisWindow] = []
    for region in bounds:
        input_windows: list[WindowInputEvidence] = []
        for item in evidence:
            valid_count = _overlap_samples(
                item.valid_regions,
                region.start_sample,
                region.end_sample,
            )
            activity_count = _overlap_samples(
                item.activity_regions,
                region.start_sample,
                region.end_sample,
            )
            speech_count = _overlap_samples(
                item.speech_regions,
                region.start_sample,
                region.end_sample,
            )
            noise_count = _overlap_samples(
                item.noise_suspected_regions,
                region.start_sample,
                region.end_sample,
            )
            codes: list[str] = []
            if valid_count == 0:
                status = "unavailable"
                codes.append("no_valid_audio")
            elif speech_count > 0:
                status = "speech"
            elif noise_count > 0:
                status = "noise_suspected"
                codes.append("noise_only_activity_suspected")
            else:
                status = "no_speech"
            if 0 < valid_count < region.sample_count:
                codes.append("partially_unavailable")
            input_windows.append(
                WindowInputEvidence(
                    input_index=item.input_index,
                    status=status,
                    valid_sample_count=valid_count,
                    activity_sample_count=activity_count,
                    speech_sample_count=speech_count,
                    noise_suspected_sample_count=noise_count,
                    valid_ratio=valid_count / region.sample_count,
                    diagnostic_codes=tuple(codes),
                )
            )

        input_tuple = tuple(input_windows)
        windows.append(
            SpeechAnalysisWindow(
                region=region,
                inputs=input_tuple,
                valid_input_indices=tuple(
                    item.input_index for item in input_tuple if item.available
                ),
                activity_input_indices=tuple(
                    item.input_index
                    for item in input_tuple
                    if item.activity_sample_count > 0
                ),
                speech_input_indices=tuple(
                    item.input_index for item in input_tuple if item.speech_likely
                ),
                noise_suspected_input_indices=tuple(
                    item.input_index
                    for item in input_tuple
                    if item.noise_suspected_sample_count > 0
                ),
                candidate_overlap_samples=_overlap_samples(
                    candidate_regions,
                    region.start_sample,
                    region.end_sample,
                ),
                speech_overlap_samples=_overlap_samples(
                    speech_regions,
                    region.start_sample,
                    region.end_sample,
                ),
            )
        )
    return tuple(windows)


def _validate_aligned_signals(
    signals: Sequence[AnalysisSignal],
    reference_index: int,
) -> tuple[tuple[AnalysisSignal, ...], int]:
    values = tuple(signals)
    if len(values) < 2:
        raise ValueError("at least two aligned analysis signals are required")
    if any(not isinstance(signal, AnalysisSignal) for signal in values):
        raise TypeError("signals must contain AnalysisSignal values")
    base_index = _index("reference_index", reference_index)
    if base_index < 0 or base_index >= len(values):
        raise ValueError("reference_index is outside the input range")
    sample_rate = values[0].sample_rate
    sample_count = values[0].samples.size
    if any(signal.sample_rate != sample_rate for signal in values[1:]):
        raise ValueError("all aligned signals must use the same sample rate")
    if any(signal.samples.size != sample_count for signal in values[1:]):
        raise ValueError("all aligned signals must use the same timeline length")
    return values, base_index


def analyze_aligned_speech(
    signals: Sequence[AnalysisSignal],
    *,
    reference_index: int = 0,
    config: SpeechAnalysisConfig | None = None,
) -> SpeechActivityReport:
    """Analyze already aligned signals and return reference-axis candidates."""

    values, base_index = _validate_aligned_signals(signals, reference_index)
    settings = config if config is not None else SpeechAnalysisConfig()
    if not isinstance(settings, SpeechAnalysisConfig):
        raise TypeError("config must be a SpeechAnalysisConfig")
    _validate_config_sample_scale(settings, values[base_index].sample_rate)

    evidence = tuple(
        _analyze_input(signal, input_index, settings)
        for input_index, signal in enumerate(values)
    )
    candidates = _build_candidates(evidence)
    speech_regions = tuple(
        merge_speech_regions(
            candidate.region
            for candidate in candidates
            if candidate.speech_likely
        )
    )
    uncertain_regions = tuple(
        merge_speech_regions(
            candidate.region
            for candidate in candidates
            if not candidate.speech_likely
        )
    )

    warnings: list[SpeechAnalysisWarning] = []
    for item in evidence:
        if not item.available:
            warnings.append(
                SpeechAnalysisWarning(
                    code="no_valid_audio",
                    message="The input has no recorded samples on the reference timeline.",
                    input_index=item.input_index,
                )
            )
        for diagnostic in item.region_diagnostics:
            if diagnostic.noise_suspected:
                descriptions = {
                    "constant_signal": (
                        "High energy was retained as uncertain activity because "
                        "the waveform is effectively constant or dominated by DC."
                    ),
                    "stationary_broadband_noise": (
                        "High energy was retained as uncertain activity because "
                        "it resembles stationary broadband noise."
                    ),
                    "stationary_tonal_activity": (
                        "High energy was retained as uncertain activity because "
                        "it resembles stationary tonal noise or hum."
                    ),
                }
                warnings.append(
                    SpeechAnalysisWarning(
                        code=diagnostic.decision_code,
                        message=descriptions[diagnostic.decision_code],
                        input_index=item.input_index,
                        region=diagnostic.region,
                    )
                )
    if candidates and not speech_regions:
        warnings.append(
            SpeechAnalysisWarning(
                code="only_noise_like_activity",
                message=(
                    "Energy activity was detected, but none was classified as "
                    "likely speech."
                ),
            )
        )

    timeline_sample_count = values[base_index].samples.size
    windows = _build_windows(
        evidence,
        candidates,
        speech_regions,
        timeline_sample_count=timeline_sample_count,
        sample_rate=values[base_index].sample_rate,
        config=settings,
    )
    return SpeechActivityReport(
        sample_rate=values[base_index].sample_rate,
        timeline_sample_count=timeline_sample_count,
        reference_index=base_index,
        config=settings,
        inputs=evidence,
        candidates=candidates,
        speech_regions=speech_regions,
        uncertain_regions=uncertain_regions,
        windows=windows,
        warnings=tuple(warnings),
    )


def analyze_prepared_speech(
    prepared: PreparedMultiAudio,
    alignment: MultiAudioAlignment,
    *,
    config: SpeechAnalysisConfig | None = None,
) -> SpeechActivityReport:
    """Warp prepared inputs and analyze them on the alignment reference axis.

    Failed or manual-review alignment results are rejected by the existing
    renderer.  They are never silently excluded from automatic integration.
    """

    if not isinstance(prepared, PreparedMultiAudio):
        raise TypeError("prepared must be a PreparedMultiAudio")
    if not isinstance(alignment, MultiAudioAlignment):
        raise TypeError("alignment must be a MultiAudioAlignment")
    if len(prepared.inputs) < 2:
        raise ValueError("at least two prepared inputs are required")
    if (
        alignment.reference_index < 0
        or alignment.reference_index >= len(prepared.inputs)
    ):
        raise ValueError("alignment reference_index is outside the input range")
    if alignment.sample_rate != prepared.analysis_sample_rate:
        raise ValueError("alignment and prepared sample rates must match")
    if len(alignment.inputs) != len(prepared.inputs):
        raise ValueError("alignment must contain one result for every prepared input")
    input_indices = tuple(result.input_index for result in alignment.inputs)
    if sorted(input_indices) != list(range(len(prepared.inputs))):
        raise ValueError("alignment input indexes must cover every prepared input once")
    if any(
        result.reference_index != alignment.reference_index
        for result in alignment.inputs
    ):
        raise ValueError(
            "every alignment result must use the report reference_index"
        )
    if any(
        item.sample_rate != prepared.analysis_sample_rate
        for item in prepared.inputs
    ):
        raise ValueError(
            "every prepared input must use the prepared analysis sample rate"
        )

    reference_result = alignment.result_for_input(alignment.reference_index)
    if (
        not reference_result.success
        or reference_result.offset_samples != 0
        or reference_result.time_scale != 1.0
        or reference_result.correction_applied
    ):
        raise ValueError(
            "the reference input alignment must be an unchanged identity transform"
        )

    timeline_sample_count = prepared.inputs[
        alignment.reference_index
    ].samples.size
    aligned_signals = tuple(
        warp_signal_to_reference_timeline(
            item.analysis,
            alignment.result_for_input(input_index),
            timeline_sample_count,
        )
        for input_index, item in enumerate(prepared.inputs)
    )
    return analyze_aligned_speech(
        aligned_signals,
        reference_index=alignment.reference_index,
        config=config,
    )


def analyze_speech_candidates(
    signals: Sequence[AnalysisSignal],
    *,
    reference_index: int = 0,
    config: SpeechAnalysisConfig | None = None,
) -> SpeechActivityReport:
    """Descriptive alias for :func:`analyze_aligned_speech`."""

    return analyze_aligned_speech(
        signals,
        reference_index=reference_index,
        config=config,
    )


__all__ = [
    "InputSpeechEvidence",
    "RegionSpeechDiagnostic",
    "SpeechActivityReport",
    "SpeechAnalysisConfig",
    "SpeechAnalysisWarning",
    "SpeechAnalysisWindow",
    "SpeechCandidateRegion",
    "WindowInputEvidence",
    "analyze_aligned_speech",
    "analyze_prepared_speech",
    "analyze_speech_candidates",
]
