"""Speech-candidate analysis and quality scoring on an aligned timeline.

This module is the boundary between time alignment and channel-quality
selection.  It deliberately keeps three concepts separate:

* recorded audio, represented by each :class:`AnalysisSignal` validity mask;
* raw energy activity from the existing VAD; and
* likely speech after conservative, diagnosable noise screening.

The distinction prevents missing timeline padding from being called silence
and prevents stationary broadband noise from being asserted as speech merely
because it is loud.  Raw activity is retained as an uncertain candidate while
the quality layer records normalized evidence without selecting a channel;
selection and hysteresis remain the responsibility of the next pipeline stage.
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
    warp_prepared_inputs,
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
class QualityScoringConfig:
    """Normalization thresholds and weights for channel-quality scoring.

    The defaults are deliberately isolated here because they are initial
    heuristic values, not measured quality guarantees.  RMS is used only to
    estimate signal/noise power and to reject effectively muted input; it is
    never a positive score component by itself.  No clarity proxy is enabled
    until its cost and reliability are validated on representative recordings.
    """

    frame_duration: float = 0.03
    minimum_frame_valid_ratio: float = 0.8
    minimum_snr_frames: int = 3
    energy_floor_dbfs: float = -100.0
    minimum_signal_dbfs: float = -75.0
    snr_bad_db: float = 0.0
    snr_good_db: float = 30.0
    clipping_threshold: float = 0.98
    clipping_min_run_duration: float = 0.000125
    clipping_plateau_tolerance: float = 1.0e-5
    clipping_full_penalty_ratio: float = 0.02
    noise_full_penalty_ratio: float = 0.5
    gap_full_penalty_ratio: float = 0.1
    validity_weight: float = 0.15
    speech_weight: float = 0.30
    snr_weight: float = 0.55
    clipping_penalty_weight: float = 0.65
    invalid_penalty_weight: float = 0.35
    gap_penalty_weight: float = 0.25
    noise_penalty_weight: float = 0.35

    def __post_init__(self) -> None:
        _require_positive("frame_duration", self.frame_duration)
        minimum_frame_valid_ratio = _require_unit_interval(
            "minimum_frame_valid_ratio",
            self.minimum_frame_valid_ratio,
        )
        if minimum_frame_valid_ratio == 0.0:
            raise ValueError("minimum_frame_valid_ratio must be greater than 0")
        _require_integer_at_least(
            "minimum_snr_frames",
            self.minimum_snr_frames,
            1,
        )

        energy_floor = _finite_real(
            "energy_floor_dbfs",
            self.energy_floor_dbfs,
        )
        minimum_signal = _finite_real(
            "minimum_signal_dbfs",
            self.minimum_signal_dbfs,
        )
        if energy_floor < -300.0:
            raise ValueError("energy_floor_dbfs must be at least -300")
        if energy_floor >= minimum_signal:
            raise ValueError(
                "energy_floor_dbfs must be below minimum_signal_dbfs"
            )
        if minimum_signal >= 0.0:
            raise ValueError("minimum_signal_dbfs must be below 0")

        snr_bad = _finite_real("snr_bad_db", self.snr_bad_db)
        snr_good = _finite_real("snr_good_db", self.snr_good_db)
        if snr_bad >= snr_good:
            raise ValueError("snr_bad_db must be below snr_good_db")
        if not math.isfinite(snr_good - snr_bad):
            raise ValueError("SNR normalization range is too large")

        clipping_threshold = _require_unit_interval(
            "clipping_threshold",
            self.clipping_threshold,
        )
        if clipping_threshold == 0.0:
            raise ValueError("clipping_threshold must be greater than 0")
        _require_positive(
            "clipping_min_run_duration",
            self.clipping_min_run_duration,
        )
        plateau_tolerance = _require_unit_interval(
            "clipping_plateau_tolerance",
            self.clipping_plateau_tolerance,
        )
        if plateau_tolerance == 0.0:
            raise ValueError(
                "clipping_plateau_tolerance must be greater than 0"
            )
        for name in (
            "clipping_full_penalty_ratio",
            "noise_full_penalty_ratio",
            "gap_full_penalty_ratio",
        ):
            value = _require_unit_interval(name, getattr(self, name))
            if value == 0.0:
                raise ValueError(f"{name} must be greater than 0")

        for name in (
            "validity_weight",
            "speech_weight",
            "snr_weight",
            "clipping_penalty_weight",
            "invalid_penalty_weight",
            "gap_penalty_weight",
            "noise_penalty_weight",
        ):
            _require_non_negative(name, getattr(self, name))
        positive_weight = float(
            self.validity_weight + self.speech_weight + self.snr_weight
        )
        penalty_weight = float(
            self.clipping_penalty_weight
            + self.invalid_penalty_weight
            + self.gap_penalty_weight
            + self.noise_penalty_weight
        )
        if not math.isfinite(positive_weight) or not math.isfinite(
            penalty_weight
        ):
            raise ValueError("quality weight totals must be finite")
        if positive_weight <= 0.0:
            raise ValueError(
                "at least one positive quality component weight is required"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "frame_duration": float(self.frame_duration),
            "minimum_frame_valid_ratio": float(
                self.minimum_frame_valid_ratio
            ),
            "minimum_snr_frames": int(self.minimum_snr_frames),
            "energy_floor_dbfs": float(self.energy_floor_dbfs),
            "minimum_signal_dbfs": float(self.minimum_signal_dbfs),
            "snr_bad_db": float(self.snr_bad_db),
            "snr_good_db": float(self.snr_good_db),
            "clipping_threshold": float(self.clipping_threshold),
            "clipping_min_run_duration": float(
                self.clipping_min_run_duration
            ),
            "clipping_plateau_tolerance": float(
                self.clipping_plateau_tolerance
            ),
            "clipping_full_penalty_ratio": float(
                self.clipping_full_penalty_ratio
            ),
            "noise_full_penalty_ratio": float(
                self.noise_full_penalty_ratio
            ),
            "gap_full_penalty_ratio": float(
                self.gap_full_penalty_ratio
            ),
            "validity_weight": float(self.validity_weight),
            "speech_weight": float(self.speech_weight),
            "snr_weight": float(self.snr_weight),
            "clipping_penalty_weight": float(
                self.clipping_penalty_weight
            ),
            "invalid_penalty_weight": float(
                self.invalid_penalty_weight
            ),
            "gap_penalty_weight": float(self.gap_penalty_weight),
            "noise_penalty_weight": float(self.noise_penalty_weight),
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


@dataclass(frozen=True)
class InputQualityScore:
    """Traceable quality result for one input in one analysis window.

    ``measurement_confidence`` describes feature evidence coverage; it is not
    the channel-selection confidence that MM-DEV-009 will derive from margins
    and hysteresis.
    """

    input_index: int
    score: float | None
    measurement_confidence: float
    valid_sample_count: int
    valid_ratio: float
    rms_dbfs: float | None
    expected_speech_sample_count: int
    speech_sample_count: int
    speech_presence_score: float
    estimated_snr_db: float | None
    snr_score: float | None
    snr_estimation_method: str | None
    snr_confidence: float
    clipped_sample_count: int
    clipping_ratio: float
    clipping_penalty: float
    invalid_sample_count: int
    invalid_penalty: float
    internal_gap_count: int
    internal_gap_sample_count: int
    gap_penalty: float
    noise_suspected_sample_count: int
    noise_suspected_ratio: float
    noise_penalty: float
    base_score: float | None
    penalty_score: float | None
    diagnostic_codes: tuple[str, ...]

    @property
    def quality_score(self) -> float | None:
        return self.score

    @property
    def available(self) -> bool:
        return self.valid_sample_count > 0

    @property
    def scoreable(self) -> bool:
        return self.score is not None

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return self.diagnostic_codes

    def to_dict(self) -> dict[str, object]:
        return {
            "input_index": int(self.input_index),
            "quality_score": self.score,
            "measurement_confidence": float(
                self.measurement_confidence
            ),
            "available": self.available,
            "scoreable": self.scoreable,
            "valid_sample_count": int(self.valid_sample_count),
            "valid_ratio": float(self.valid_ratio),
            "rms_dbfs": self.rms_dbfs,
            "expected_speech_sample_count": int(
                self.expected_speech_sample_count
            ),
            "speech_sample_count": int(self.speech_sample_count),
            "speech_presence_score": float(self.speech_presence_score),
            "estimated_snr_db": self.estimated_snr_db,
            "snr_score": self.snr_score,
            "snr_estimation_method": self.snr_estimation_method,
            "snr_confidence": float(self.snr_confidence),
            "clipped_sample_count": int(self.clipped_sample_count),
            "clipping_ratio": float(self.clipping_ratio),
            "clipping_penalty": float(self.clipping_penalty),
            "invalid_sample_count": int(self.invalid_sample_count),
            "invalid_penalty": float(self.invalid_penalty),
            "internal_gap_count": int(self.internal_gap_count),
            "internal_gap_sample_count": int(
                self.internal_gap_sample_count
            ),
            "gap_penalty": float(self.gap_penalty),
            "noise_suspected_sample_count": int(
                self.noise_suspected_sample_count
            ),
            "noise_suspected_ratio": float(
                self.noise_suspected_ratio
            ),
            "noise_penalty": float(self.noise_penalty),
            "base_score": self.base_score,
            "penalty_score": self.penalty_score,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class QualityAnalysisWindow:
    """Per-channel scores on one MM-DEV-007 feature window."""

    region: SpeechRegion
    inputs: tuple[InputQualityScore, ...]
    has_speech_candidate: bool
    has_likely_speech: bool

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
    def scores(self) -> tuple[InputQualityScore, ...]:
        return self.inputs

    def score_for_input(self, input_index: int) -> InputQualityScore:
        index = _index("input_index", input_index)
        for item in self.inputs:
            if item.input_index == index:
                return item
        raise IndexError(f"no quality score for input index {index}")

    def to_dict(self) -> dict[str, object]:
        return {
            **self.region.to_dict(),
            "has_speech_candidate": self.has_speech_candidate,
            "has_likely_speech": self.has_likely_speech,
            "inputs": [item.to_dict() for item in self.inputs],
        }


@dataclass(frozen=True)
class InputQualitySummary:
    """Coverage-aware diagnostic aggregate over the analysis windows.

    Unscoreable window duration contributes no quality points but remains in
    the denominator.  Channel selection still operates on individual windows.
    """

    input_index: int
    score: float | None
    measurement_confidence: float
    score_coverage_ratio: float
    scored_window_count: int
    unscoreable_window_count: int
    diagnostic_codes: tuple[str, ...]

    @property
    def quality_score(self) -> float | None:
        return self.score

    @property
    def scoreable(self) -> bool:
        return self.score is not None

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return self.diagnostic_codes

    def to_dict(self) -> dict[str, object]:
        return {
            "input_index": int(self.input_index),
            "quality_score": self.score,
            "measurement_confidence": float(
                self.measurement_confidence
            ),
            "score_coverage_ratio": float(self.score_coverage_ratio),
            "scoreable": self.scoreable,
            "scored_window_count": int(self.scored_window_count),
            "unscoreable_window_count": int(
                self.unscoreable_window_count
            ),
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class ChannelQualityReport:
    """MM-DEV-008 channel scores on the canonical reference timeline."""

    sample_rate: int
    timeline_sample_count: int
    reference_index: int
    speech_report: SpeechActivityReport
    config: QualityScoringConfig
    inputs: tuple[InputQualitySummary, ...]
    windows: tuple[QualityAnalysisWindow, ...]

    @property
    def per_input(self) -> tuple[InputQualitySummary, ...]:
        return self.inputs

    @property
    def analysis_windows(self) -> tuple[QualityAnalysisWindow, ...]:
        return self.windows

    def input_summary(self, input_index: int) -> InputQualitySummary:
        index = _index("input_index", input_index)
        for item in self.inputs:
            if item.input_index == index:
                return item
        raise IndexError(f"no quality summary for input index {index}")

    def to_dict(self) -> dict[str, object]:
        return {
            "sample_rate": int(self.sample_rate),
            "timeline_sample_count": int(self.timeline_sample_count),
            "reference_index": int(self.reference_index),
            "speech_analysis": self.speech_report.to_dict(),
            "config": self.config.to_dict(),
            "inputs": [item.to_dict() for item in self.inputs],
            "windows": [window.to_dict() for window in self.windows],
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


def _clamp_unit(value: float) -> float:
    return float(min(1.0, max(0.0, value)))


def _normalize_between(value: float, lower: float, upper: float) -> float:
    return _clamp_unit((value - lower) / (upper - lower))


def _internal_gap_mask(valid_mask: np.ndarray) -> np.ndarray:
    """Mark invalid samples bounded by recorded audio on the full timeline."""

    result = np.zeros(valid_mask.size, dtype=np.bool_)
    valid_indexes = np.flatnonzero(valid_mask)
    if valid_indexes.size < 2:
        return result
    start = int(valid_indexes[0])
    end = int(valid_indexes[-1]) + 1
    result[start:end] = np.logical_not(valid_mask[start:end])
    return result


def _true_run_stats(mask: np.ndarray) -> tuple[int, int]:
    if not np.any(mask):
        return 0, 0
    padded = np.pad(mask, (1, 1), constant_values=False)
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return len(edges) // 2, int(np.count_nonzero(mask))


def _samples_in_flat_runs(
    values: np.ndarray,
    candidate_mask: np.ndarray,
    minimum_run_samples: int,
    tolerance: float,
) -> int:
    if values.size < 2 or not np.any(candidate_mask):
        return 0
    flat_links = (
        candidate_mask[:-1]
        & candidate_mask[1:]
        & (np.abs(np.diff(values)) <= tolerance)
    )
    if not np.any(flat_links):
        return 0
    padded = np.pad(flat_links, (1, 1), constant_values=False)
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    sample_lengths = edges[1::2] - edges[0::2] + 1
    return int(
        np.sum(sample_lengths[sample_lengths >= minimum_run_samples])
    )


def _suspected_clipped_sample_count(
    values: np.ndarray,
    valid_mask: np.ndarray,
    sample_rate: int,
    config: QualityScoringConfig,
) -> int:
    """Count repeated near-rail plateaus, not isolated full-scale peaks."""

    recorded = values[valid_mask]
    if recorded.size == 0:
        return 0
    threshold = float(config.clipping_threshold)
    tolerance = float(config.clipping_plateau_tolerance)
    minimum_run_samples = _seconds_to_samples(
        "clipping_min_run_duration",
        config.clipping_min_run_duration,
        sample_rate,
        minimum=2,
    )
    count = 0

    if float(np.max(recorded)) >= threshold:
        count += _samples_in_flat_runs(
            values,
            valid_mask & (values >= threshold),
            minimum_run_samples,
            tolerance,
        )

    if float(np.min(recorded)) <= -threshold:
        count += _samples_in_flat_runs(
            values,
            valid_mask & (values <= -threshold),
            minimum_run_samples,
            tolerance,
        )
    return count


def _quality_frame_powers(
    signal: AnalysisSignal,
    region: SpeechRegion,
    evidence: InputSpeechEvidence,
    config: QualityScoringConfig,
) -> tuple[list[float], list[float], list[float]]:
    """Collect all, confirmed-speech, and inactive frame powers.

    A frame crossing a validity hole is used only when its recorded fraction
    meets the configured threshold.  Invalid timeline padding never becomes a
    zero-valued noise observation.
    """

    frame_samples = _seconds_to_samples(
        "frame_duration",
        config.frame_duration,
        signal.sample_rate,
        minimum=8,
    )
    all_powers: list[float] = []
    speech_powers: list[float] = []
    inactive_powers: list[float] = []
    for relative_start in range(0, region.sample_count, frame_samples):
        start = region.start_sample + relative_start
        end = min(region.end_sample, start + frame_samples)
        frame_mask = signal.valid_mask[start:end]
        if frame_mask.size < 8:
            continue
        valid_count = int(np.count_nonzero(frame_mask))
        if valid_count / frame_mask.size < config.minimum_frame_valid_ratio:
            continue
        values = signal.samples[start:end][frame_mask].astype(
            np.float64,
            copy=False,
        )
        power = float(np.mean(np.square(values)))
        if not math.isfinite(power):
            continue
        all_powers.append(power)
        if _overlaps(evidence.speech_regions, start, end):
            speech_powers.append(power)
        elif not _overlaps(evidence.activity_regions, start, end):
            inactive_powers.append(power)
    return all_powers, speech_powers, inactive_powers


def _estimate_window_snr(
    signal: AnalysisSignal,
    region: SpeechRegion,
    evidence: InputSpeechEvidence,
    config: QualityScoringConfig,
) -> tuple[float | None, float | None, str | None, float]:
    all_powers, speech_powers, inactive_powers = _quality_frame_powers(
        signal,
        region,
        evidence,
        config,
    )
    minimum_frames = config.minimum_snr_frames
    if len(speech_powers) < minimum_frames:
        return None, None, None, 0.0

    speech_power = float(np.percentile(speech_powers, 75))
    speech_floor_power = float(np.percentile(speech_powers, 20))
    if len(inactive_powers) >= minimum_frames:
        # The inactive floor alone is optimistic when interference exists only
        # during speech.  Cap that estimate with minimum statistics from the
        # speech frames so added in-speech noise cannot improve the SNR.
        noise_power = max(
            float(np.median(inactive_powers)),
            speech_floor_power,
        )
        method = "inactive_and_speech_minimum_statistics"
        confidence = min(
            1.0,
            min(len(speech_powers), len(inactive_powers)) / minimum_frames,
        )
    elif len(all_powers) >= minimum_frames * 2:
        # A conservative minimum-statistics fallback for windows containing
        # continuous speech.  It measures dynamics, not absolute loudness.
        noise_power = max(
            float(np.percentile(all_powers, 20)),
            speech_floor_power,
        )
        method = "frame_power_percentile_fallback"
        confidence = 0.5 * min(
            1.0,
            len(speech_powers) / (minimum_frames * 2),
        )
    else:
        return None, None, None, 0.0

    power_floor = 10.0 ** (float(config.energy_floor_dbfs) / 10.0)
    noise_power = max(power_floor, noise_power)
    signal_power = max(power_floor, speech_power - noise_power)
    snr_db = float(10.0 * math.log10(signal_power / noise_power))
    if not math.isfinite(snr_db):
        return None, None, None, 0.0
    # Keep diagnostics finite and bounded even for digital silence floors.
    snr_db = float(min(100.0, max(-100.0, snr_db)))
    snr_score = _normalize_between(
        snr_db,
        float(config.snr_bad_db),
        float(config.snr_good_db),
    )
    return snr_db, snr_score, method, float(confidence)


def _empty_quality_score(
    input_index: int,
    window_sample_count: int,
    expected_speech_sample_count: int,
) -> InputQualityScore:
    return InputQualityScore(
        input_index=input_index,
        score=None,
        measurement_confidence=0.0,
        valid_sample_count=0,
        valid_ratio=0.0,
        rms_dbfs=None,
        expected_speech_sample_count=expected_speech_sample_count,
        speech_sample_count=0,
        speech_presence_score=0.0,
        estimated_snr_db=None,
        snr_score=None,
        snr_estimation_method=None,
        snr_confidence=0.0,
        clipped_sample_count=0,
        clipping_ratio=0.0,
        clipping_penalty=0.0,
        invalid_sample_count=window_sample_count,
        invalid_penalty=1.0,
        internal_gap_count=0,
        internal_gap_sample_count=0,
        gap_penalty=0.0,
        noise_suspected_sample_count=0,
        noise_suspected_ratio=0.0,
        noise_penalty=0.0,
        base_score=None,
        penalty_score=None,
        diagnostic_codes=("no_valid_audio",),
    )


def _score_window_input(
    signal: AnalysisSignal,
    internal_gap_mask: np.ndarray,
    speech_evidence: InputSpeechEvidence,
    window: SpeechAnalysisWindow,
    window_evidence: WindowInputEvidence,
    config: QualityScoringConfig,
) -> InputQualityScore:
    region = window.region
    valid_count = window_evidence.valid_sample_count
    expected_speech_count = window.speech_overlap_samples
    if valid_count == 0:
        return _empty_quality_score(
            speech_evidence.input_index,
            region.sample_count,
            expected_speech_count,
        )

    interval = slice(region.start_sample, region.end_sample)
    mask = signal.valid_mask[interval]
    window_values = signal.samples[interval]
    valid_values = window_values[mask]
    valid_ratio = valid_count / region.sample_count
    invalid_count = region.sample_count - valid_count
    invalid_penalty = _clamp_unit(1.0 - valid_ratio)
    gap_count, gap_sample_count = _true_run_stats(internal_gap_mask[interval])
    gap_ratio = gap_sample_count / region.sample_count
    gap_penalty = _clamp_unit(
        gap_ratio / float(config.gap_full_penalty_ratio)
    )

    rms = float(
        np.sqrt(np.mean(np.square(valid_values, dtype=np.float64)))
    )
    rms_dbfs = _dbfs_from_rms(rms)
    clipped_count = _suspected_clipped_sample_count(
        window_values,
        mask,
        signal.sample_rate,
        config,
    )
    clipping_ratio = clipped_count / valid_count
    clipping_penalty = _clamp_unit(
        clipping_ratio / float(config.clipping_full_penalty_ratio)
    )

    speech_count = window_evidence.speech_sample_count
    speech_presence_score = (
        _clamp_unit(speech_count / expected_speech_count)
        if expected_speech_count > 0
        else 0.0
    )
    noise_count = window_evidence.noise_suspected_sample_count
    noise_ratio = _clamp_unit(noise_count / valid_count)
    noise_penalty = _clamp_unit(
        noise_ratio / float(config.noise_full_penalty_ratio)
    )

    snr_db, snr_score, snr_method, snr_confidence = _estimate_window_snr(
        signal,
        region,
        speech_evidence,
        config,
    )

    codes: list[str] = []
    if valid_ratio < 1.0:
        codes.append("partially_unavailable")
    if gap_count:
        codes.append("recording_gap_detected")
    if clipped_count:
        codes.append("clipping_suspected")
    if noise_count:
        codes.append("noise_suspected")
    if expected_speech_count > 0:
        codes.append(
            "speech_detected"
            if speech_count > 0
            else "expected_speech_not_detected"
        )
    else:
        codes.append("no_speech_expected")
    if snr_score is None:
        codes.append("snr_unavailable")
    else:
        codes.append(f"snr_{snr_method}")

    # A low-level channel is unusable when the shared timeline says speech
    # should be present.  During a genuine no-speech window, however, recorded
    # silence is useful neutral evidence: otherwise a noisy channel can become
    # the only scoreable candidate simply because it has more energy.
    if expected_speech_count > 0 and (
        rms_dbfs is None
        or rms_dbfs < float(config.minimum_signal_dbfs)
    ):
        codes.append("muted_or_too_quiet")
        return InputQualityScore(
            input_index=speech_evidence.input_index,
            score=None,
            measurement_confidence=0.0,
            valid_sample_count=valid_count,
            valid_ratio=float(valid_ratio),
            rms_dbfs=rms_dbfs,
            expected_speech_sample_count=expected_speech_count,
            speech_sample_count=speech_count,
            speech_presence_score=float(speech_presence_score),
            estimated_snr_db=snr_db,
            snr_score=snr_score,
            snr_estimation_method=snr_method,
            snr_confidence=snr_confidence,
            clipped_sample_count=clipped_count,
            clipping_ratio=float(clipping_ratio),
            clipping_penalty=float(clipping_penalty),
            invalid_sample_count=invalid_count,
            invalid_penalty=float(invalid_penalty),
            internal_gap_count=gap_count,
            internal_gap_sample_count=gap_sample_count,
            gap_penalty=float(gap_penalty),
            noise_suspected_sample_count=noise_count,
            noise_suspected_ratio=float(noise_ratio),
            noise_penalty=float(noise_penalty),
            base_score=None,
            penalty_score=None,
            diagnostic_codes=tuple(codes),
        )

    positive_weight = (
        float(config.validity_weight)
        + float(config.speech_weight)
        + float(config.snr_weight)
    )
    base_score = (
        float(config.validity_weight) * valid_ratio
        + float(config.speech_weight) * speech_presence_score
        + float(config.snr_weight) * (snr_score or 0.0)
    ) / positive_weight
    penalty_score = (
        float(config.clipping_penalty_weight) * clipping_penalty
        + float(config.invalid_penalty_weight) * invalid_penalty
        + float(config.gap_penalty_weight) * gap_penalty
        + float(config.noise_penalty_weight) * noise_penalty
    )
    score = _clamp_unit(base_score - penalty_score)
    confidence = valid_ratio * (
        0.5
        if expected_speech_count == 0
        else 0.4 + 0.6 * snr_confidence
    )
    return InputQualityScore(
        input_index=speech_evidence.input_index,
        score=score,
        measurement_confidence=_clamp_unit(confidence),
        valid_sample_count=valid_count,
        valid_ratio=float(valid_ratio),
        rms_dbfs=rms_dbfs,
        expected_speech_sample_count=expected_speech_count,
        speech_sample_count=speech_count,
        speech_presence_score=float(speech_presence_score),
        estimated_snr_db=snr_db,
        snr_score=snr_score,
        snr_estimation_method=snr_method,
        snr_confidence=float(snr_confidence),
        clipped_sample_count=clipped_count,
        clipping_ratio=float(clipping_ratio),
        clipping_penalty=float(clipping_penalty),
        invalid_sample_count=invalid_count,
        invalid_penalty=float(invalid_penalty),
        internal_gap_count=gap_count,
        internal_gap_sample_count=gap_sample_count,
        gap_penalty=float(gap_penalty),
        noise_suspected_sample_count=noise_count,
        noise_suspected_ratio=float(noise_ratio),
        noise_penalty=float(noise_penalty),
        base_score=float(base_score),
        penalty_score=float(penalty_score),
        diagnostic_codes=tuple(codes),
    )


def _validate_speech_report(
    report: SpeechActivityReport,
    signals: Sequence[AnalysisSignal],
    reference_index: int,
) -> None:
    if not isinstance(report, SpeechActivityReport):
        raise TypeError("speech_report must be a SpeechActivityReport")
    if report.reference_index != reference_index:
        raise ValueError("speech report reference_index does not match signals")
    if report.sample_rate != signals[reference_index].sample_rate:
        raise ValueError("speech report sample rate does not match signals")
    if report.timeline_sample_count != signals[reference_index].samples.size:
        raise ValueError("speech report timeline length does not match signals")
    expected_indexes = tuple(range(len(signals)))
    if tuple(item.input_index for item in report.inputs) != expected_indexes:
        raise ValueError("speech report inputs do not match signals")
    for item in report.inputs:
        if item.valid_sample_count != signals[item.input_index].valid_sample_count:
            raise ValueError("speech report validity does not match signals")
    for window in report.windows:
        if (
            window.start_sample < 0
            or window.end_sample <= window.start_sample
            or window.end_sample > report.timeline_sample_count
        ):
            raise ValueError("speech report window is outside the timeline")
        if tuple(item.input_index for item in window.inputs) != expected_indexes:
            raise ValueError("speech report window inputs do not match signals")
        for item in window.inputs:
            counts = (
                item.valid_sample_count,
                item.activity_sample_count,
                item.speech_sample_count,
                item.noise_suspected_sample_count,
            )
            if any(count < 0 or count > window.sample_count for count in counts):
                raise ValueError(
                    "speech report window evidence is inconsistent"
                )
            actual_valid_count = int(
                np.count_nonzero(
                    signals[item.input_index].valid_mask[
                        window.start_sample : window.end_sample
                    ]
                )
            )
            if item.valid_sample_count != actual_valid_count:
                raise ValueError(
                    "speech report window validity does not match signals"
                )
            expected_valid_ratio = item.valid_sample_count / window.sample_count
            if not math.isclose(
                item.valid_ratio,
                expected_valid_ratio,
                rel_tol=0.0,
                abs_tol=1.0e-12,
            ):
                raise ValueError(
                    "speech report window valid ratio is inconsistent"
                )


def _quality_summaries(
    windows: Sequence[QualityAnalysisWindow],
    input_count: int,
) -> tuple[InputQualitySummary, ...]:
    summaries: list[InputQualitySummary] = []
    for input_index in range(input_count):
        pairs = tuple(
            (window, window.score_for_input(input_index))
            for window in windows
        )
        items = tuple(item for _, item in pairs)
        scored = tuple(item for item in items if item.score is not None)
        total_samples = sum(window.sample_count for window, _ in pairs)
        scored_samples = sum(
            window.sample_count
            for window, item in pairs
            if item.score is not None
        )
        aggregate_score = (
            sum(
                float(item.score) * window.sample_count
                for window, item in pairs
                if item.score is not None
            )
            / total_samples
            if scored and total_samples
            else None
        )
        measurement_confidence = (
            sum(
                item.measurement_confidence * window.sample_count
                for window, item in pairs
                if item.score is not None
            )
            / total_samples
            if total_samples
            else 0.0
        )
        codes = tuple(
            dict.fromkeys(
                code
                for item in items
                for code in item.diagnostic_codes
            )
        )
        summaries.append(
            InputQualitySummary(
                input_index=input_index,
                score=(
                    float(aggregate_score)
                    if aggregate_score is not None
                    else None
                ),
                measurement_confidence=float(measurement_confidence),
                score_coverage_ratio=(
                    scored_samples / total_samples if total_samples else 0.0
                ),
                scored_window_count=len(scored),
                unscoreable_window_count=len(items) - len(scored),
                diagnostic_codes=codes,
            )
        )
    return tuple(summaries)


def score_aligned_quality(
    signals: Sequence[AnalysisSignal],
    speech_report: SpeechActivityReport,
    *,
    config: QualityScoringConfig | None = None,
) -> ChannelQualityReport:
    """Score aligned inputs using an existing MM-DEV-007 speech report."""

    if not isinstance(speech_report, SpeechActivityReport):
        raise TypeError("speech_report must be a SpeechActivityReport")
    values, base_index = _validate_aligned_signals(
        signals,
        speech_report.reference_index,
    )
    _validate_speech_report(speech_report, values, base_index)
    settings = config if config is not None else QualityScoringConfig()
    if not isinstance(settings, QualityScoringConfig):
        raise TypeError("config must be a QualityScoringConfig")
    _seconds_to_samples(
        "frame_duration",
        settings.frame_duration,
        speech_report.sample_rate,
        minimum=8,
    )
    _seconds_to_samples(
        "clipping_min_run_duration",
        settings.clipping_min_run_duration,
        speech_report.sample_rate,
        minimum=2,
    )

    windows: list[QualityAnalysisWindow] = []
    internal_gap_masks = tuple(
        _internal_gap_mask(signal.valid_mask) for signal in values
    )
    for speech_window in speech_report.windows:
        scores = tuple(
            _score_window_input(
                values[input_index],
                internal_gap_masks[input_index],
                speech_report.input_evidence(input_index),
                speech_window,
                speech_window.evidence_for_input(input_index),
                settings,
            )
            for input_index in range(len(values))
        )
        windows.append(
            QualityAnalysisWindow(
                region=speech_window.region,
                inputs=scores,
                has_speech_candidate=speech_window.has_speech_candidate,
                has_likely_speech=speech_window.has_likely_speech,
            )
        )
    window_tuple = tuple(windows)
    return ChannelQualityReport(
        sample_rate=speech_report.sample_rate,
        timeline_sample_count=speech_report.timeline_sample_count,
        reference_index=base_index,
        speech_report=speech_report,
        config=settings,
        inputs=_quality_summaries(window_tuple, len(values)),
        windows=window_tuple,
    )


def analyze_aligned_quality(
    signals: Sequence[AnalysisSignal],
    *,
    reference_index: int = 0,
    speech_config: SpeechAnalysisConfig | None = None,
    quality_config: QualityScoringConfig | None = None,
) -> ChannelQualityReport:
    """Run speech analysis and channel scoring on aligned signals."""

    values = tuple(signals)
    speech_report = analyze_aligned_speech(
        values,
        reference_index=reference_index,
        config=speech_config,
    )
    return score_aligned_quality(
        values,
        speech_report,
        config=quality_config,
    )


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
    """Warp prepared inputs and analyze them on the reference axis."""

    aligned_signals = warp_prepared_inputs(prepared, alignment)
    return analyze_aligned_speech(
        aligned_signals,
        reference_index=alignment.reference_index,
        config=config,
    )


def analyze_prepared_quality(
    prepared: PreparedMultiAudio,
    alignment: MultiAudioAlignment,
    *,
    speech_config: SpeechAnalysisConfig | None = None,
    quality_config: QualityScoringConfig | None = None,
) -> ChannelQualityReport:
    """Warp prepared inputs and calculate per-window channel quality."""

    aligned_signals = warp_prepared_inputs(prepared, alignment)
    return analyze_aligned_quality(
        aligned_signals,
        reference_index=alignment.reference_index,
        speech_config=speech_config,
        quality_config=quality_config,
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
    "ChannelQualityReport",
    "InputQualityScore",
    "InputQualitySummary",
    "InputSpeechEvidence",
    "QualityAnalysisWindow",
    "QualityScoringConfig",
    "RegionSpeechDiagnostic",
    "SpeechActivityReport",
    "SpeechAnalysisConfig",
    "SpeechAnalysisWarning",
    "SpeechAnalysisWindow",
    "SpeechCandidateRegion",
    "WindowInputEvidence",
    "analyze_aligned_quality",
    "analyze_aligned_speech",
    "analyze_prepared_quality",
    "analyze_prepared_speech",
    "analyze_speech_candidates",
    "score_aligned_quality",
]
