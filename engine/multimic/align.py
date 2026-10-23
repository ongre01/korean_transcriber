"""Offset and clock-drift alignment for offline multi-microphone recordings.

The canonical ``offset_samples`` in this module is a *timeline placement*
offset: it is the position on the reference timeline where target sample zero
belongs.  A positive value means the target recording started later than the
reference recording.  This convention is intentionally compatible with
``pipeline.place_signal_on_timeline(..., start_sample=offset_samples)`` and is
the opposite of the target-minus-reference lag commonly returned by a cross
correlator.

For long recordings the drift-aware entry points fit the guarded affine map
``reference = time_scale * target + offset_samples``.  They retain the initial
offset API for short recordings and reject unstable/nonlinear fits for manual
review rather than resampling them automatically.

Only decoded analysis samples and their validity masks are considered.  File
timestamps are deliberately absent from this API, so they cannot accidentally
become synchronization evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from numbers import Real
import operator
from typing import Protocol, Sequence

import numpy as np


class AnalysisSignalLike(Protocol):
    """Structural type consumed by the alignment routines."""

    samples: np.ndarray
    valid_mask: np.ndarray
    sample_rate: int


@dataclass(frozen=True)
class AlignmentConfig:
    """Tunable initial-alignment parameters.

    Defaults favor conservative failure over returning a plausible-looking
    offset for unrelated or repetitive recordings.  ``max_offset_seconds`` is
    a search bound, not the +/-0.5 second acceptance-test value.
    """

    window_seconds: float = 0.75
    hop_seconds: float = 0.25
    max_offset_seconds: float = 2.0
    max_windows: int = 8
    min_reference_windows: int = 3
    min_consistent_windows: int = 3
    min_valid_ratio: float = 0.98
    min_signal_rms: float = 1.0e-4
    reference_activity_floor_ratio: float = 0.10
    min_peak_correlation: float = 0.30
    min_peak_prominence: float = 0.015
    peak_exclusion_ms: float = 20.0
    consensus_tolerance_ms: float = 20.0
    min_consensus_ratio: float = 0.60
    max_offset_dispersion_ms: float = 20.0
    min_confidence: float = 0.55
    use_gcc_phat: bool = True
    gcc_compare_below_correlation: float = 0.75
    gcc_compare_below_prominence: float = 0.08
    drift_min_duration_seconds: float = 30.0
    drift_min_anchors: int = 3
    drift_min_inlier_ratio: float = 0.80
    drift_min_span_ratio: float = 0.60
    drift_residual_tolerance_ms: float = 20.0
    drift_max_abs_ppm: float = 2_000.0
    drift_min_correction_ms: float = 20.0

    def __post_init__(self) -> None:
        _require_positive("window_seconds", self.window_seconds)
        _require_positive("hop_seconds", self.hop_seconds)
        _require_positive("max_offset_seconds", self.max_offset_seconds)
        _require_positive("peak_exclusion_ms", self.peak_exclusion_ms)
        _require_positive("consensus_tolerance_ms", self.consensus_tolerance_ms)
        _require_positive(
            "max_offset_dispersion_ms", self.max_offset_dispersion_ms
        )
        _require_positive(
            "drift_min_duration_seconds", self.drift_min_duration_seconds
        )
        _require_positive(
            "drift_residual_tolerance_ms", self.drift_residual_tolerance_ms
        )
        _require_positive("drift_max_abs_ppm", self.drift_max_abs_ppm)
        _require_non_negative(
            "drift_min_correction_ms", self.drift_min_correction_ms
        )
        _require_non_negative("min_signal_rms", self.min_signal_rms)

        _require_integer_at_least("max_windows", self.max_windows, 1)
        _require_integer_at_least(
            "min_reference_windows", self.min_reference_windows, 1
        )
        _require_integer_at_least(
            "min_consistent_windows", self.min_consistent_windows, 1
        )
        _require_integer_at_least("drift_min_anchors", self.drift_min_anchors, 3)
        if self.min_reference_windows > self.max_windows:
            raise ValueError("min_reference_windows must not exceed max_windows")
        if self.min_consistent_windows > self.max_windows:
            raise ValueError("min_consistent_windows must not exceed max_windows")
        # Preserve legacy two-window offset-only configurations, while
        # rejecting drift configurations that can never collect their stated
        # minimum once at least three affine anchors are otherwise possible.
        if self.max_windows >= 3 and self.drift_min_anchors > self.max_windows:
            raise ValueError("drift_min_anchors must not exceed max_windows")
        if self.hop_seconds > self.window_seconds:
            raise ValueError("hop_seconds must not exceed window_seconds")

        for name, value in (
            ("min_valid_ratio", self.min_valid_ratio),
            ("reference_activity_floor_ratio", self.reference_activity_floor_ratio),
            ("min_peak_correlation", self.min_peak_correlation),
            ("min_peak_prominence", self.min_peak_prominence),
            ("min_consensus_ratio", self.min_consensus_ratio),
            ("min_confidence", self.min_confidence),
            ("gcc_compare_below_correlation", self.gcc_compare_below_correlation),
            ("gcc_compare_below_prominence", self.gcc_compare_below_prominence),
            ("drift_min_inlier_ratio", self.drift_min_inlier_ratio),
            ("drift_min_span_ratio", self.drift_min_span_ratio),
        ):
            _require_unit_interval(name, value)

        if not isinstance(self.use_gcc_phat, bool):
            raise ValueError("use_gcc_phat must be a boolean")


@dataclass(frozen=True)
class AlignmentFailure:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class AlignmentWarning:
    """A recoverable diagnostic that still requires explicit user review."""

    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class DriftAnchor:
    """One local delay observation used to diagnose an affine clock fit."""

    reference_sample: int
    target_sample: int
    observed_offset_samples: int
    observed_offset_ms: float
    predicted_reference_sample: float
    residual_samples: float
    residual_ms: float
    correlation: float
    peak_prominence: float
    inlier: bool
    region: str

    def to_dict(self) -> dict[str, object]:
        return {
            "reference_sample": self.reference_sample,
            "target_sample": self.target_sample,
            "observed_offset_samples": self.observed_offset_samples,
            "observed_offset_ms": self.observed_offset_ms,
            "predicted_reference_sample": self.predicted_reference_sample,
            "residual_samples": self.residual_samples,
            "residual_ms": self.residual_ms,
            "correlation": self.correlation,
            "peak_prominence": self.peak_prominence,
            "inlier": self.inlier,
            "region": self.region,
        }


@dataclass(frozen=True)
class WindowAlignment:
    """One reference-window observation used for robust aggregation."""

    reference_start_sample: int
    reference_end_sample: int
    offset_samples: int | None
    correlation: float
    second_peak_correlation: float
    peak_prominence: float
    polarity: int
    gcc_phat_offset_samples: int | None
    gcc_phat_agrees: bool | None
    accepted: bool
    rejection_code: str | None = None
    selected_peak_rank: int | None = None
    peak_candidate_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "reference_start_sample": self.reference_start_sample,
            "reference_end_sample": self.reference_end_sample,
            "offset_samples": self.offset_samples,
            "correlation": self.correlation,
            "second_peak_correlation": self.second_peak_correlation,
            "peak_prominence": self.peak_prominence,
            "polarity": self.polarity,
            "gcc_phat_offset_samples": self.gcc_phat_offset_samples,
            "gcc_phat_agrees": self.gcc_phat_agrees,
            "accepted": self.accepted,
            "rejection_code": self.rejection_code,
            "selected_peak_rank": self.selected_peak_rank,
            "peak_candidate_count": self.peak_candidate_count,
        }


@dataclass(frozen=True)
class InputAlignment:
    """Initial placement estimate for one input relative to the reference."""

    input_index: int
    reference_index: int
    offset_samples: int | None
    offset_ms: float | None
    confidence: float
    method: str
    failure: AlignmentFailure | None
    selected_window_count: int
    accepted_window_count: int
    consistent_window_count: int
    offset_dispersion_ms: float | None
    window_observations: tuple[WindowAlignment, ...] = ()
    time_scale: float | None = None
    estimated_time_scale: float | None = None
    drift_ppm: float | None = None
    drift_span_ms: float | None = None
    pre_correction_error_ms: float | None = None
    post_correction_error_ms: float | None = None
    max_post_correction_error_ms: float | None = None
    correction_method: str = "none"
    correction_applied: bool = False
    requires_manual_review: bool = False
    warning: AlignmentWarning | None = None
    drift_anchors: tuple[DriftAnchor, ...] = ()

    @property
    def ok(self) -> bool:
        return (
            self.failure is None
            and self.offset_samples is not None
            and not self.requires_manual_review
        )

    @property
    def success(self) -> bool:
        return self.ok

    @property
    def failure_code(self) -> str | None:
        return self.failure.code if self.failure is not None else None

    @property
    def failure_reason(self) -> str | None:
        return self.failure.message if self.failure is not None else None

    @property
    def offset_milliseconds(self) -> float | None:
        return self.offset_ms

    @property
    def warning_code(self) -> str | None:
        return self.warning.code if self.warning is not None else None

    @property
    def warning_reason(self) -> str | None:
        return self.warning.message if self.warning is not None else None

    @property
    def status(self) -> str:
        if self.requires_manual_review:
            return "manual_review"
        if self.success:
            return "aligned"
        return "failed"

    def to_dict(self) -> dict[str, object]:
        return {
            "input_index": self.input_index,
            "reference_index": self.reference_index,
            "success": self.success,
            "offset_samples": self.offset_samples,
            "offset_ms": self.offset_ms,
            "confidence": self.confidence,
            "method": self.method,
            "failure_code": self.failure_code,
            "failure_reason": self.failure_reason,
            "selected_window_count": self.selected_window_count,
            "accepted_window_count": self.accepted_window_count,
            "consistent_window_count": self.consistent_window_count,
            "offset_dispersion_ms": self.offset_dispersion_ms,
            "time_scale": self.time_scale,
            "estimated_time_scale": self.estimated_time_scale,
            "drift_ppm": self.drift_ppm,
            "drift_span_ms": self.drift_span_ms,
            "pre_correction_error_ms": self.pre_correction_error_ms,
            "post_correction_error_ms": self.post_correction_error_ms,
            "max_post_correction_error_ms": self.max_post_correction_error_ms,
            "correction_method": self.correction_method,
            "correction_applied": self.correction_applied,
            "requires_manual_review": self.requires_manual_review,
            "status": self.status,
            "warning_code": self.warning_code,
            "warning_reason": self.warning_reason,
            "drift_anchors": [anchor.to_dict() for anchor in self.drift_anchors],
            "windows": [window.to_dict() for window in self.window_observations],
        }


@dataclass(frozen=True)
class MultiAudioAlignment:
    reference_index: int
    sample_rate: int
    inputs: tuple[InputAlignment, ...]
    reference_windows: tuple[tuple[int, int], ...]

    @property
    def ok(self) -> bool:
        return all(item.ok for item in self.inputs)

    @property
    def success(self) -> bool:
        return self.ok

    @property
    def all_aligned(self) -> bool:
        return self.ok

    @property
    def failures(self) -> tuple[InputAlignment, ...]:
        return tuple(item for item in self.inputs if not item.ok)

    def result_for_input(self, input_index: int) -> InputAlignment:
        index = _index("input_index", input_index)
        for item in self.inputs:
            if item.input_index == index:
                return item
        raise IndexError(f"no alignment result for input index {index}")

    def to_dict(self) -> dict[str, object]:
        return {
            "reference_index": self.reference_index,
            "sample_rate": self.sample_rate,
            "success": self.success,
            "reference_windows": [
                {"start_sample": start, "end_sample": end}
                for start, end in self.reference_windows
            ],
            "inputs": [item.to_dict() for item in self.inputs],
        }


# Explicit aliases make the result roles discoverable without changing the
# shorter public names used by the rest of the package.
InputAlignmentResult = InputAlignment
MultiAudioAlignmentResult = MultiAudioAlignment


@dataclass(frozen=True)
class _SignalView:
    samples: np.ndarray
    valid_mask: np.ndarray
    sample_rate: int


@dataclass(frozen=True)
class _Peak:
    index: int
    value: float
    second: float

    @property
    def prominence(self) -> float:
        return max(0.0, self.value - self.second)


@dataclass(frozen=True)
class _WindowEstimate:
    """Primary diagnostic plus non-overlapping peaks eligible for consensus."""

    primary: WindowAlignment
    candidates: tuple[WindowAlignment, ...]


@dataclass(frozen=True)
class _CandidateCluster:
    """At most one peak candidate from each independent reference window."""

    choices: tuple[tuple[int, WindowAlignment], ...]
    estimated_offset: int
    weight: float
    spread: float

    @property
    def count(self) -> int:
        return len(self.choices)


@dataclass(frozen=True)
class _AffineFit:
    """Robust target-to-reference model selected from per-window peaks."""

    scale: float
    intercept: float
    choices: tuple[tuple[int, WindowAlignment], ...]
    residuals: tuple[float, ...]
    weight: float
    rms_residual: float
    span_ratio: float
    regions: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.choices)

    @property
    def drift_ppm(self) -> float:
        return (self.scale - 1.0) * 1_000_000.0


def _require_positive(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number greater than 0")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number greater than 0") from exc
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be a finite number greater than 0")


def _require_non_negative(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number at least 0")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number at least 0") from exc
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{name} must be a finite number at least 0")


def _require_unit_interval(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number in [0, 1]")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number in [0, 1]") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be a finite number in [0, 1]")


def _require_integer_at_least(name: str, value: int, minimum: int) -> None:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer at least {minimum}")
    try:
        integer = operator.index(value)
    except TypeError as exc:
        raise ValueError(f"{name} must be an integer at least {minimum}") from exc
    if integer < minimum:
        raise ValueError(f"{name} must be an integer at least {minimum}")


def _index(name: str, value: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        return operator.index(value)
    except TypeError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _coerce_signal(signal: AnalysisSignalLike, name: str) -> _SignalView:
    try:
        samples = np.asarray(signal.samples, dtype=np.float32)
        valid_mask = np.asarray(signal.valid_mask, dtype=np.bool_)
        sample_rate = _index(f"{name}.sample_rate", signal.sample_rate)
    except AttributeError as exc:
        raise TypeError(
            f"{name} must provide samples, valid_mask, and sample_rate"
        ) from exc

    if sample_rate <= 0:
        raise ValueError(f"{name}.sample_rate must be positive")
    if samples.ndim != 1 or valid_mask.ndim != 1:
        raise ValueError(f"{name} samples and valid_mask must be one-dimensional")
    if samples.shape != valid_mask.shape:
        raise ValueError(f"{name} samples and valid_mask must have the same length")
    if not np.isfinite(samples).all():
        raise ValueError(f"{name} samples must contain only finite values")

    return _SignalView(
        samples=np.ascontiguousarray(samples),
        valid_mask=np.ascontiguousarray(valid_mask),
        sample_rate=sample_rate,
    )


def _condition_signal(
    samples: np.ndarray,
    valid_mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """High-pass valid sample pairs without turning mask edges into evidence."""

    conditioned = np.zeros(samples.size, dtype=np.float32)
    conditioned_mask = np.zeros(samples.size, dtype=np.bool_)
    if samples.size < 2:
        return conditioned, conditioned_mask

    conditioned_mask[1:] = valid_mask[1:] & valid_mask[:-1]
    differences = samples[1:] - np.float32(0.97) * samples[:-1]
    conditioned[1:] = np.where(conditioned_mask[1:], differences, 0.0)
    return conditioned, conditioned_mask


def _window_samples(config: AlignmentConfig, sample_rate: int) -> int:
    return max(2, int(round(config.window_seconds * sample_rate)))


def _candidate_starts(sample_count: int, window: int, hop: int) -> np.ndarray:
    if sample_count < window:
        return np.empty(0, dtype=np.int64)
    starts = np.arange(0, sample_count - window + 1, hop, dtype=np.int64)
    final_start = sample_count - window
    if not starts.size or starts[-1] != final_start:
        starts = np.append(starts, final_start)
    return starts


def _prefix_sum(values: np.ndarray, *, dtype: object) -> np.ndarray:
    return np.concatenate(
        (np.zeros(1, dtype=dtype), np.cumsum(values, dtype=dtype))
    )


def _select_reference_windows(
    reference: _SignalView,
    config: AlignmentConfig,
    *,
    reserve_drift_edges: bool = False,
) -> tuple[tuple[int, int], ...]:
    window = _window_samples(config, reference.sample_rate)
    hop = max(1, int(round(config.hop_seconds * reference.sample_rate)))
    starts = _candidate_starts(reference.samples.size, window, hop)
    if not starts.size:
        return ()

    ends = starts + window
    valid = np.zeros(starts.size, dtype=np.bool_)
    scores = np.zeros(starts.size, dtype=np.float64)
    minimum_valid = math.ceil(config.min_valid_ratio * window)
    for candidate_index, (raw_start, raw_end) in enumerate(zip(starts, ends)):
        start = int(raw_start)
        end = int(raw_end)
        samples = reference.samples[start:end]
        mask = reference.valid_mask[start:end]
        pair_mask = mask[1:] & mask[:-1]
        valid_count = int(np.count_nonzero(pair_mask))
        first_valid = bool(
            start > 0
            and reference.valid_mask[start]
            and reference.valid_mask[start - 1]
        )
        if first_valid:
            valid_count += 1
        if valid_count < minimum_valid:
            continue

        differences = samples[1:] - np.float32(0.97) * samples[:-1]
        values = differences[pair_mask]
        value_sum = float(np.sum(values, dtype=np.float64))
        square_sum = float(
            np.sum(np.square(values, dtype=np.float64), dtype=np.float64)
        )
        if first_valid:
            first_value = float(
                reference.samples[start]
                - np.float32(0.97) * reference.samples[start - 1]
            )
            value_sum += first_value
            square_sum += first_value * first_value
        mean = value_sum / valid_count
        variance = max(0.0, square_sum / valid_count - mean * mean)
        valid[candidate_index] = True
        scores[candidate_index] = math.sqrt(variance)
    eligible_indexes = np.flatnonzero(valid & (scores >= config.min_signal_rms))
    if not eligible_indexes.size:
        return ()

    strongest = float(np.max(scores[eligible_indexes]))
    activity_floor = max(
        config.min_signal_rms,
        strongest * config.reference_activity_floor_ratio,
    )
    eligible_indexes = eligible_indexes[scores[eligible_indexes] >= activity_floor]
    if not eligible_indexes.size:
        return ()

    # Seed the selection with the strongest active window in each temporal bin.
    # A second score-ordered pass fills bins containing long utterances, while a
    # half-window separation prevents near-identical overlapping observations.
    centers = starts + window // 2
    bin_edges = np.linspace(
        0,
        reference.samples.size,
        num=config.max_windows + 1,
        dtype=np.float64,
    )
    seeds: list[int] = []
    for bin_index in range(config.max_windows):
        lower = bin_edges[bin_index]
        upper = bin_edges[bin_index + 1]
        in_bin = eligible_indexes[
            (centers[eligible_indexes] >= lower)
            & (
                (centers[eligible_indexes] < upper)
                | (
                    bin_index == config.max_windows - 1
                    and centers[eligible_indexes] <= upper
                )
            )
        ]
        if in_bin.size:
            seeds.append(int(in_bin[np.argmax(scores[in_bin])]))

    ranked = sorted(
        (int(index) for index in eligible_indexes),
        key=lambda index: (-float(scores[index]), int(starts[index])),
    )
    if reserve_drift_edges:
        # A drift fit must actually observe the start, middle, and end.  Keep
        # edge windows inside the offset search margin so a cropped target can
        # still overlap the complete template at the configured search bound.
        edge_margin = int(
            math.ceil(config.max_offset_seconds * reference.sample_rate)
        )
        safe_edge_indexes = eligible_indexes[
            (starts[eligible_indexes] >= edge_margin)
            & (ends[eligible_indexes] <= reference.samples.size - edge_margin)
        ]
        edge_pool = safe_edge_indexes if safe_edge_indexes.size else eligible_indexes
        reserved_seeds = [int(edge_pool[0])]
        if int(edge_pool[-1]) != reserved_seeds[0]:
            reserved_seeds.append(int(edge_pool[-1]))

        middle_indexes = eligible_indexes[
            (centers[eligible_indexes] >= reference.samples.size / 3.0)
            & (centers[eligible_indexes] < 2.0 * reference.samples.size / 3.0)
        ]
        if middle_indexes.size:
            middle_seed = int(middle_indexes[np.argmax(scores[middle_indexes])])
            if middle_seed not in reserved_seeds:
                reserved_seeds.append(middle_seed)
        seed_ranked = sorted(
            set(seeds) - set(reserved_seeds),
            key=lambda index: (-float(scores[index]), int(starts[index])),
        )
        selection_order = (*reserved_seeds, *seed_ranked, *ranked)
    else:
        # Preserve the MM-DEV-004 selection order exactly for the public
        # initial-offset APIs and for short offset-only recordings.
        seed_ranked = sorted(
            set(seeds),
            key=lambda index: (-float(scores[index]), int(starts[index])),
        )
        selection_order = (*seed_ranked, *ranked)

    selected: list[int] = []
    minimum_separation = max(1, window // 2)
    for index in selection_order:
        if index in selected:
            continue
        center = int(centers[index])
        if any(
            abs(center - int(centers[chosen])) < minimum_separation
            for chosen in selected
        ):
            continue
        selected.append(index)
        if len(selected) >= config.max_windows:
            break

    selected.sort(key=lambda index: int(starts[index]))
    return tuple((int(starts[index]), int(ends[index])) for index in selected)


def _next_power_of_two(value: int) -> int:
    return 1 << (value - 1).bit_length()


def _fft_valid_correlation(signal: np.ndarray, template: np.ndarray) -> np.ndarray:
    output_count = signal.size - template.size + 1
    if output_count <= 0:
        return np.empty(0, dtype=np.float64)
    fft_size = _next_power_of_two(signal.size + template.size - 1)
    spectrum = np.fft.rfft(signal, fft_size)
    template_spectrum = np.fft.rfft(template[::-1], fft_size)
    convolution = np.fft.irfft(spectrum * template_spectrum, fft_size)
    start = template.size - 1
    return np.asarray(convolution[start : start + output_count], dtype=np.float64)


def _normalized_cross_correlation(
    template: np.ndarray,
    signal: np.ndarray,
) -> np.ndarray:
    centered = np.asarray(template, dtype=np.float64)
    centered = centered - float(np.mean(centered))
    template_energy = float(np.dot(centered, centered))
    if template_energy <= np.finfo(np.float64).eps:
        return np.full(signal.size - template.size + 1, np.nan, dtype=np.float64)

    signal64 = np.asarray(signal, dtype=np.float64)
    dots = _fft_valid_correlation(signal64, centered)
    prefix = _prefix_sum(signal64, dtype=np.float64)
    square_prefix = _prefix_sum(np.square(signal64), dtype=np.float64)
    window = template.size
    sums = prefix[window:] - prefix[:-window]
    sums_of_squares = square_prefix[window:] - square_prefix[:-window]
    variances = np.maximum(0.0, sums_of_squares - np.square(sums) / window)
    denominators = np.sqrt(template_energy * variances)
    result = np.full(dots.shape, np.nan, dtype=np.float64)
    usable = denominators > np.finfo(np.float64).eps
    result[usable] = dots[usable] / denominators[usable]
    np.clip(result, -1.0, 1.0, out=result)
    return result


def _masked_normalized_cross_correlation(
    template: np.ndarray,
    signal: np.ndarray,
    template_mask: np.ndarray,
    signal_mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return Pearson NCC using only each candidate's valid intersection."""

    output_count = signal.size - template.size + 1
    if output_count <= 0:
        return (
            np.empty(0, dtype=np.float64),
            np.empty(0, dtype=np.int64),
        )
    if template_mask.all() and signal_mask.all():
        return (
            _normalized_cross_correlation(template, signal),
            np.full(output_count, template.size, dtype=np.int64),
        )

    template64 = np.asarray(template, dtype=np.float64)
    signal64 = np.asarray(signal, dtype=np.float64)
    template_valid = np.asarray(template_mask, dtype=np.float64)
    signal_valid = np.asarray(signal_mask, dtype=np.float64)
    template_values = template64 * template_valid
    signal_values = signal64 * signal_valid
    template_squares = np.square(template64) * template_valid
    signal_squares = np.square(signal64) * signal_valid

    fft_size = _next_power_of_two(signal.size + template.size - 1)
    signal_mask_spectrum = np.fft.rfft(signal_valid, fft_size)
    signal_spectrum = np.fft.rfft(signal_values, fft_size)
    signal_square_spectrum = np.fft.rfft(signal_squares, fft_size)
    template_mask_spectrum = np.fft.rfft(template_valid[::-1], fft_size)
    template_spectrum = np.fft.rfft(template_values[::-1], fft_size)
    template_square_spectrum = np.fft.rfft(template_squares[::-1], fft_size)
    start = template.size - 1
    stop = start + output_count

    def correlate(left: np.ndarray, right: np.ndarray) -> np.ndarray:
        convolution = np.fft.irfft(left * right, fft_size)
        return np.asarray(convolution[start:stop], dtype=np.float64)

    counts_float = correlate(signal_mask_spectrum, template_mask_spectrum)
    counts = np.asarray(np.rint(np.maximum(0.0, counts_float)), dtype=np.int64)
    sum_x = correlate(signal_mask_spectrum, template_spectrum)
    sum_y = correlate(signal_spectrum, template_mask_spectrum)
    sum_xx = correlate(signal_mask_spectrum, template_square_spectrum)
    sum_yy = correlate(signal_square_spectrum, template_mask_spectrum)
    sum_xy = correlate(signal_spectrum, template_spectrum)

    safe_counts = np.maximum(counts, 1).astype(np.float64)
    covariance = sum_xy - sum_x * sum_y / safe_counts
    variance_x = np.maximum(0.0, sum_xx - np.square(sum_x) / safe_counts)
    variance_y = np.maximum(0.0, sum_yy - np.square(sum_y) / safe_counts)
    denominators = np.sqrt(variance_x * variance_y)
    correlations = np.full(output_count, np.nan, dtype=np.float64)
    usable = (counts >= 2) & (denominators > np.finfo(np.float64).eps)
    correlations[usable] = covariance[usable] / denominators[usable]
    np.clip(correlations, -1.0, 1.0, out=correlations)
    return correlations, counts


def _gcc_phat_valid_correlation(
    template: np.ndarray,
    signal: np.ndarray,
) -> np.ndarray:
    """Return GCC-PHAT scores for starts where ``template`` fully overlaps."""

    output_count = signal.size - template.size + 1
    if output_count <= 0:
        return np.empty(0, dtype=np.float64)
    fft_size = _next_power_of_two(signal.size + template.size - 1)
    target_spectrum = np.fft.rfft(signal, fft_size)
    reference_spectrum = np.fft.rfft(template, fft_size)
    cross_spectrum = target_spectrum * np.conj(reference_spectrum)
    magnitude = np.abs(cross_spectrum)
    usable = magnitude > np.finfo(np.float64).eps
    cross_spectrum[usable] /= magnitude[usable]
    cross_spectrum[~usable] = 0.0
    circular = np.fft.irfft(cross_spectrum, fft_size)
    negative_count = template.size - 1
    full = np.concatenate(
        (circular[-negative_count:], circular[: signal.size])
    )
    start = template.size - 1
    return np.asarray(full[start : start + output_count], dtype=np.float64)


def _find_peak(values: np.ndarray, exclusion_samples: int) -> _Peak | None:
    finite = np.isfinite(values)
    if not finite.any():
        return None
    strengths = np.where(finite, np.abs(values), -np.inf)
    index = int(np.argmax(strengths))
    value = float(strengths[index])
    alternatives = strengths.copy()
    start = max(0, index - exclusion_samples)
    end = min(alternatives.size, index + exclusion_samples + 1)
    alternatives[start:end] = -np.inf
    second = float(np.max(alternatives)) if np.isfinite(alternatives).any() else 0.0
    return _Peak(index=index, value=value, second=max(0.0, second))


def _find_peaks(
    values: np.ndarray,
    exclusion_samples: int,
    *,
    limit: int = 3,
) -> tuple[_Peak, ...]:
    """Return deterministic non-overlapping peaks in descending strength order.

    Each candidate's ``second`` value is the strongest peak left after that
    candidate and all higher-ranked lobes have been suppressed.  This keeps a
    useful clarity measure for a shared second-choice peak without treating a
    stronger, window-local decoy as proof that the shared peak is ambiguous.
    """

    finite = np.isfinite(values)
    if not finite.any() or limit <= 0:
        return ()

    remaining = np.where(finite, np.abs(values), -np.inf)
    peaks: list[_Peak] = []
    for _ in range(limit):
        if not np.isfinite(remaining).any():
            break
        index = int(np.argmax(remaining))
        value = float(remaining[index])
        start = max(0, index - exclusion_samples)
        end = min(remaining.size, index + exclusion_samples + 1)
        remaining[start:end] = -np.inf
        second = (
            float(np.max(remaining)) if np.isfinite(remaining).any() else 0.0
        )
        peaks.append(_Peak(index=index, value=value, second=max(0.0, second)))
    return tuple(peaks)


def _estimate_window(
    reference: _SignalView,
    target: _SignalView,
    conditioned_reference: np.ndarray,
    conditioned_target: np.ndarray,
    conditioned_reference_mask: np.ndarray,
    conditioned_target_mask: np.ndarray,
    reference_start: int,
    reference_end: int,
    config: AlignmentConfig,
) -> _WindowEstimate:
    window = reference_end - reference_start
    max_lag = max(
        1,
        int(round(config.max_offset_seconds * reference.sample_rate)),
    )
    candidate_start = max(0, reference_start - max_lag)
    candidate_end = min(
        target.samples.size - window,
        reference_start + max_lag,
    )
    if candidate_end < candidate_start:
        return _WindowEstimate(
            primary=WindowAlignment(
                reference_start,
                reference_end,
                None,
                0.0,
                0.0,
                0.0,
                0,
                None,
                None,
                False,
                "insufficient_shared_audio",
            ),
            candidates=(),
        )

    segment_end = candidate_end + window
    target_segment = conditioned_target[candidate_start:segment_end]
    target_mask = conditioned_target_mask[candidate_start:segment_end]
    template = conditioned_reference[reference_start:reference_end]
    template_mask = conditioned_reference_mask[reference_start:reference_end]
    correlations, valid_counts = _masked_normalized_cross_correlation(
        template,
        target_segment,
        template_mask,
        target_mask,
    )
    candidates_valid = valid_counts >= math.ceil(config.min_valid_ratio * window)
    correlations[~candidates_valid] = np.nan

    if not candidates_valid.any():
        return _WindowEstimate(
            primary=WindowAlignment(
                reference_start,
                reference_end,
                None,
                0.0,
                0.0,
                0.0,
                0,
                None,
                None,
                False,
                "insufficient_shared_audio",
            ),
            candidates=(),
        )

    exclusion = max(
        1,
        int(round(config.peak_exclusion_ms * reference.sample_rate / 1000.0)),
    )
    peak = _find_peak(correlations, exclusion)
    if peak is None:
        return _WindowEstimate(
            primary=WindowAlignment(
                reference_start,
                reference_end,
                None,
                0.0,
                0.0,
                0.0,
                0,
                None,
                None,
                False,
                "target_insufficient_signal",
            ),
            candidates=(),
        )

    target_window_start = candidate_start + peak.index
    correlation_lag = target_window_start - reference_start
    offset = -correlation_lag
    signed_correlation = float(correlations[peak.index])
    polarity = 1 if signed_correlation >= 0.0 else -1

    compare_gcc = config.use_gcc_phat and (
        peak.value < config.gcc_compare_below_correlation
        or peak.prominence < config.gcc_compare_below_prominence
    )
    gcc_offset: int | None = None
    gcc_agrees: bool | None = None
    if compare_gcc:
        gcc_values = _gcc_phat_valid_correlation(template, target_segment)
        gcc_values[~candidates_valid] = np.nan
        gcc_peak = _find_peak(gcc_values, exclusion)
        if gcc_peak is not None:
            gcc_target_start = candidate_start + gcc_peak.index
            gcc_offset = -(gcc_target_start - reference_start)
            tolerance = int(
                round(config.consensus_tolerance_ms * reference.sample_rate / 1000.0)
            )
            gcc_agrees = abs(gcc_offset - offset) <= tolerance

    boundary_margin = min(exclusion, max(1, max_lag // 20))
    accepted = True
    rejection: str | None = None
    if peak.value < config.min_peak_correlation:
        accepted = False
        rejection = "low_correlation"
    elif peak.prominence < config.min_peak_prominence:
        accepted = False
        rejection = "ambiguous_peak"
    elif abs(offset) >= max_lag - boundary_margin:
        accepted = False
        rejection = "offset_at_search_boundary"

    tolerance = max(
        1,
        int(round(config.consensus_tolerance_ms * reference.sample_rate / 1000.0)),
    )
    # A wider NMS radius keeps shoulders of one broad correlation lobe from
    # consuming the small candidate budget.  Cross-window clustering still
    # uses the configured (narrower) consensus tolerance.
    candidate_peaks = _find_peaks(
        correlations,
        max(exclusion, 2 * tolerance),
        limit=3,
    )
    candidate_count = len(candidate_peaks)
    primary = WindowAlignment(
        reference_start_sample=reference_start,
        reference_end_sample=reference_end,
        offset_samples=int(offset),
        correlation=float(peak.value),
        second_peak_correlation=float(peak.second),
        peak_prominence=float(peak.prominence),
        polarity=polarity,
        gcc_phat_offset_samples=gcc_offset,
        gcc_phat_agrees=gcc_agrees,
        accepted=accepted,
        rejection_code=rejection,
        selected_peak_rank=1,
        peak_candidate_count=candidate_count,
    )

    candidate_observations: list[WindowAlignment] = []
    for rank, candidate_peak in enumerate(candidate_peaks, start=1):
        candidate_target_start = candidate_start + candidate_peak.index
        candidate_offset = -(candidate_target_start - reference_start)
        candidate_signed_correlation = float(correlations[candidate_peak.index])
        candidate_rejection: str | None = None
        if candidate_peak.value < config.min_peak_correlation:
            candidate_rejection = "low_correlation"
        elif abs(candidate_offset) >= max_lag - boundary_margin:
            candidate_rejection = "offset_at_search_boundary"

        candidate_observations.append(
            WindowAlignment(
                reference_start_sample=reference_start,
                reference_end_sample=reference_end,
                offset_samples=int(candidate_offset),
                correlation=float(candidate_peak.value),
                second_peak_correlation=float(candidate_peak.second),
                peak_prominence=float(candidate_peak.prominence),
                polarity=1 if candidate_signed_correlation >= 0.0 else -1,
                gcc_phat_offset_samples=gcc_offset,
                gcc_phat_agrees=(
                    None
                    if gcc_offset is None
                    else abs(gcc_offset - candidate_offset) <= tolerance
                ),
                accepted=candidate_rejection is None,
                rejection_code=candidate_rejection,
                selected_peak_rank=rank,
                peak_candidate_count=candidate_count,
            )
        )

    return _WindowEstimate(
        primary=primary,
        candidates=tuple(candidate_observations),
    )


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values, kind="stable")
    ordered_values = values[order]
    ordered_weights = weights[order]
    midpoint = float(np.sum(ordered_weights)) / 2.0
    position = int(np.searchsorted(np.cumsum(ordered_weights), midpoint, side="left"))
    return float(ordered_values[min(position, ordered_values.size - 1)])


def _observation_weights(observations: Sequence[WindowAlignment]) -> np.ndarray:
    weights: list[float] = []
    for observation in observations:
        clarity = min(1.0, observation.peak_prominence / 0.10)
        method_factor = 0.75 if observation.gcc_phat_agrees is False else 1.0
        weights.append(
            max(1.0e-6, observation.correlation * (0.25 + 0.75 * clarity))
            * method_factor
        )
    return np.asarray(weights, dtype=np.float64)


def _candidate_clusters(
    candidate_windows: Sequence[tuple[WindowAlignment, ...]],
    tolerance_samples: int,
) -> tuple[_CandidateCluster, ...]:
    """Cluster peak candidates while allowing at most one vote per window."""

    centers = sorted(
        {
            int(candidate.offset_samples)
            for candidates in candidate_windows
            for candidate in candidates
            if candidate.accepted and candidate.offset_samples is not None
        }
    )
    clusters: dict[
        tuple[tuple[int, int], ...],
        _CandidateCluster,
    ] = {}

    def choose(center: int) -> tuple[tuple[int, WindowAlignment], ...]:
        selected: list[tuple[int, WindowAlignment]] = []
        for window_index, candidates in enumerate(candidate_windows):
            nearby = tuple(
                candidate
                for candidate in candidates
                if candidate.accepted
                and candidate.offset_samples is not None
                and abs(candidate.offset_samples - center) <= tolerance_samples
            )
            if not nearby:
                continue
            nearby_weights = _observation_weights(nearby)
            best_index = max(
                range(len(nearby)),
                key=lambda index: (
                    float(nearby_weights[index]),
                    nearby[index].correlation,
                    nearby[index].peak_prominence,
                    -abs(int(nearby[index].offset_samples) - center),
                    -(nearby[index].selected_peak_rank or 1),
                    -int(nearby[index].offset_samples),
                ),
            )
            selected.append((window_index, nearby[best_index]))
        return tuple(selected)

    for center in centers:
        choices = choose(center)
        if not choices:
            continue

        # Re-center on the weighted median before the final strict pruning.
        # Candidate centers can initially join values almost two tolerances
        # apart, so re-selection alone is not a sufficient invariant.
        for _ in range(2):
            observations = tuple(item for _, item in choices)
            offsets = np.asarray(
                [item.offset_samples for item in observations],
                dtype=np.float64,
            )
            weights = _observation_weights(observations)
            estimated = int(round(_weighted_median(offsets, weights)))
            refined = choose(estimated)
            if refined == choices:
                break
            choices = refined
            if not choices:
                break
        if not choices:
            continue

        # Pruning is monotonic and therefore converges in at most one pass per
        # window.  Recomputing the median after every removal is important: a
        # high-weight edge candidate can otherwise move the final center far
        # enough that a counted low-weight vote lies outside the tolerance.
        while choices:
            observations = tuple(item for _, item in choices)
            offsets = np.asarray(
                [item.offset_samples for item in observations],
                dtype=np.float64,
            )
            weights = _observation_weights(observations)
            estimated = int(round(_weighted_median(offsets, weights)))
            retained = tuple(
                (window_index, item)
                for window_index, item in choices
                if abs(int(item.offset_samples) - estimated) <= tolerance_samples
            )
            if len(retained) == len(choices):
                break
            choices = retained
        if not choices:
            continue

        observations = tuple(item for _, item in choices)
        offsets = np.asarray(
            [item.offset_samples for item in observations],
            dtype=np.float64,
        )
        weights = _observation_weights(observations)
        estimated = int(round(_weighted_median(offsets, weights)))
        signature = tuple(
            (window_index, int(item.offset_samples))
            for window_index, item in choices
        )
        cluster = _CandidateCluster(
            choices=choices,
            estimated_offset=estimated,
            weight=float(np.sum(weights)),
            spread=float(np.ptp(offsets)) if offsets.size else math.inf,
        )
        previous = clusters.get(signature)
        if previous is None or cluster.weight > previous.weight:
            clusters[signature] = cluster

    return tuple(
        sorted(
            clusters.values(),
            key=lambda cluster: (
                -cluster.count,
                -cluster.weight,
                cluster.spread,
                abs(cluster.estimated_offset),
                cluster.estimated_offset,
            ),
        )
    )


def _observation_coordinates(observation: WindowAlignment) -> tuple[float, float]:
    """Return corresponding ``(target, reference)`` sample coordinates."""

    if observation.offset_samples is None:
        raise ValueError("an affine anchor must have an offset")
    reference_sample = float(observation.reference_start_sample)
    target_sample = reference_sample - float(observation.offset_samples)
    return target_sample, reference_sample


def _weighted_affine_parameters(
    choices: Sequence[tuple[int, WindowAlignment]],
) -> tuple[float, float] | None:
    """Fit ``reference = scale * target + intercept`` with centered WLS."""

    if len(choices) < 2:
        return None
    observations = tuple(item for _, item in choices)
    coordinates = tuple(_observation_coordinates(item) for item in observations)
    target = np.asarray([item[0] for item in coordinates], dtype=np.float64)
    reference = np.asarray([item[1] for item in coordinates], dtype=np.float64)
    weights = _observation_weights(observations)
    total_weight = float(np.sum(weights))
    if total_weight <= np.finfo(np.float64).eps:
        return None

    target_mean = float(np.sum(weights * target) / total_weight)
    reference_mean = float(np.sum(weights * reference) / total_weight)
    centered_target = target - target_mean
    denominator = float(np.sum(weights * np.square(centered_target)))
    if denominator <= np.finfo(np.float64).eps:
        return None
    scale = float(
        np.sum(weights * centered_target * (reference - reference_mean))
        / denominator
    )
    intercept = reference_mean - scale * target_mean
    if not math.isfinite(scale) or not math.isfinite(intercept) or scale <= 0.0:
        return None
    return scale, intercept


def _affine_residual(
    observation: WindowAlignment,
    scale: float,
    intercept: float,
) -> float:
    target_sample, reference_sample = _observation_coordinates(observation)
    return reference_sample - (scale * target_sample + intercept)


def _select_affine_choices(
    candidate_windows: Sequence[tuple[WindowAlignment, ...]],
    scale: float,
    intercept: float,
    tolerance_samples: float,
) -> tuple[tuple[int, WindowAlignment], ...]:
    """Select at most one peak from each window for an affine hypothesis."""

    selected: list[tuple[int, WindowAlignment]] = []
    for window_index, candidates in enumerate(candidate_windows):
        eligible = tuple(
            candidate
            for candidate in candidates
            if candidate.accepted and candidate.offset_samples is not None
        )
        if not eligible:
            continue
        weights = _observation_weights(eligible)
        best_index = min(
            range(len(eligible)),
            key=lambda index: (
                abs(_affine_residual(eligible[index], scale, intercept)),
                -float(weights[index]),
                -eligible[index].correlation,
                eligible[index].selected_peak_rank or 1,
                int(eligible[index].offset_samples),
            ),
        )
        best = eligible[best_index]
        if abs(_affine_residual(best, scale, intercept)) <= tolerance_samples:
            selected.append((window_index, best))
    return tuple(selected)


def _anchor_region(reference_sample: float, reference_sample_count: int) -> str:
    fraction = reference_sample / max(1.0, float(reference_sample_count))
    if fraction < 1.0 / 3.0:
        return "start"
    if fraction < 2.0 / 3.0:
        return "middle"
    return "end"


def _refine_affine_fit(
    initial_scale: float,
    initial_intercept: float,
    candidate_windows: Sequence[tuple[WindowAlignment, ...]],
    *,
    tolerance_samples: float,
    reference_sample_count: int,
    max_abs_ppm: float,
) -> _AffineFit | None:
    scale = initial_scale
    intercept = initial_intercept
    previous_signature: tuple[tuple[int, int], ...] | None = None
    choices: tuple[tuple[int, WindowAlignment], ...] = ()
    for _ in range(6):
        if (
            not math.isfinite(scale)
            or not math.isfinite(intercept)
            or scale <= 0.0
            or abs((scale - 1.0) * 1_000_000.0) > max_abs_ppm
        ):
            return None
        choices = _select_affine_choices(
            candidate_windows,
            scale,
            intercept,
            tolerance_samples,
        )
        if len(choices) < 2:
            return None
        signature = tuple(
            (window_index, int(observation.offset_samples))
            for window_index, observation in choices
        )
        parameters = _weighted_affine_parameters(choices)
        if parameters is None:
            return None
        scale, intercept = parameters
        if signature == previous_signature:
            break
        previous_signature = signature

    choices = _select_affine_choices(
        candidate_windows,
        scale,
        intercept,
        tolerance_samples,
    )
    parameters = _weighted_affine_parameters(choices)
    if parameters is None:
        return None
    scale, intercept = parameters
    drift_ppm = (scale - 1.0) * 1_000_000.0
    if abs(drift_ppm) > max_abs_ppm:
        return None

    residuals = tuple(
        _affine_residual(observation, scale, intercept)
        for _, observation in choices
    )
    observations = tuple(observation for _, observation in choices)
    weights = _observation_weights(observations)
    total_weight = float(np.sum(weights))
    rms_residual = math.sqrt(
        float(
            np.sum(weights * np.square(np.asarray(residuals, dtype=np.float64)))
            / max(total_weight, np.finfo(np.float64).eps)
        )
    )
    reference_positions = np.asarray(
        [item.reference_start_sample for item in observations],
        dtype=np.float64,
    )
    span_ratio = (
        float(np.ptp(reference_positions)) / max(1.0, reference_sample_count - 1.0)
        if reference_positions.size
        else 0.0
    )
    regions = tuple(
        sorted(
            {
                _anchor_region(position, reference_sample_count)
                for position in reference_positions
            }
        )
    )
    return _AffineFit(
        scale=scale,
        intercept=intercept,
        choices=choices,
        residuals=residuals,
        weight=total_weight,
        rms_residual=rms_residual,
        span_ratio=span_ratio,
        regions=regions,
    )


def _affine_candidate_fits(
    candidate_windows: Sequence[tuple[WindowAlignment, ...]],
    *,
    tolerance_samples: float,
    reference_sample_count: int,
    max_abs_ppm: float,
) -> tuple[_AffineFit, ...]:
    """Build deterministic pair hypotheses and retain unique refined fits."""

    hypotheses: list[tuple[float, float]] = []
    populated = tuple(
        (window_index, candidates)
        for window_index, candidates in enumerate(candidate_windows)
        if candidates
    )
    for _, candidates in populated:
        for candidate in candidates:
            target_sample, reference_sample = _observation_coordinates(candidate)
            hypotheses.append((1.0, reference_sample - target_sample))

    for left_index in range(len(populated)):
        _, left_candidates = populated[left_index]
        for right_index in range(left_index + 1, len(populated)):
            _, right_candidates = populated[right_index]
            for left in left_candidates:
                left_target, left_reference = _observation_coordinates(left)
                for right in right_candidates:
                    right_target, right_reference = _observation_coordinates(right)
                    target_delta = right_target - left_target
                    if abs(target_delta) <= np.finfo(np.float64).eps:
                        continue
                    scale = (right_reference - left_reference) / target_delta
                    intercept = left_reference - scale * left_target
                    hypotheses.append((float(scale), float(intercept)))

    fits: dict[tuple[tuple[int, int], ...], _AffineFit] = {}
    for scale, intercept in hypotheses:
        fit = _refine_affine_fit(
            scale,
            intercept,
            candidate_windows,
            tolerance_samples=tolerance_samples,
            reference_sample_count=reference_sample_count,
            max_abs_ppm=max_abs_ppm,
        )
        if fit is None:
            continue
        signature = tuple(
            (window_index, int(observation.offset_samples))
            for window_index, observation in fit.choices
        )
        previous = fits.get(signature)
        if previous is None or (
            fit.rms_residual,
            -fit.weight,
            abs(fit.drift_ppm),
        ) < (
            previous.rms_residual,
            -previous.weight,
            abs(previous.drift_ppm),
        ):
            fits[signature] = fit

    return tuple(
        sorted(
            fits.values(),
            key=lambda fit: (
                -fit.count,
                -len(fit.regions),
                -fit.span_ratio,
                -fit.weight,
                fit.rms_residual,
                abs(fit.drift_ppm),
                fit.intercept,
            ),
        )
    )


def _closest_affine_observations(
    candidate_windows: Sequence[tuple[WindowAlignment, ...]],
    primary_observations: Sequence[WindowAlignment],
    scale: float,
    intercept: float,
) -> tuple[WindowAlignment, ...]:
    selected: list[WindowAlignment] = []
    for candidates, primary in zip(candidate_windows, primary_observations):
        if not candidates:
            selected.append(primary)
            continue
        selected.append(
            min(
                candidates,
                key=lambda candidate: (
                    abs(_affine_residual(candidate, scale, intercept)),
                    -candidate.correlation,
                    candidate.selected_peak_rank or 1,
                ),
            )
        )
    return tuple(selected)


def _weighted_rms(values: np.ndarray, weights: np.ndarray) -> float:
    total_weight = float(np.sum(weights))
    if not values.size or total_weight <= np.finfo(np.float64).eps:
        return 0.0
    return math.sqrt(float(np.sum(weights * np.square(values)) / total_weight))


def _drift_anchors(
    observations: Sequence[WindowAlignment],
    *,
    scale: float,
    intercept: float,
    tolerance_samples: float,
    reference_sample_count: int,
    sample_rate: int,
) -> tuple[DriftAnchor, ...]:
    anchors: list[DriftAnchor] = []
    for observation in observations:
        if observation.offset_samples is None or not observation.accepted:
            continue
        target_sample, reference_sample = _observation_coordinates(observation)
        predicted = scale * target_sample + intercept
        residual = reference_sample - predicted
        anchors.append(
            DriftAnchor(
                reference_sample=int(round(reference_sample)),
                target_sample=int(round(target_sample)),
                observed_offset_samples=int(observation.offset_samples),
                observed_offset_ms=(
                    float(observation.offset_samples) * 1000.0 / sample_rate
                ),
                predicted_reference_sample=float(predicted),
                residual_samples=float(residual),
                residual_ms=float(residual * 1000.0 / sample_rate),
                correlation=float(observation.correlation),
                peak_prominence=float(observation.peak_prominence),
                inlier=abs(residual) <= tolerance_samples,
                region=_anchor_region(reference_sample, reference_sample_count),
            )
        )
    return tuple(anchors)


def _failure_confidence(
    observations: Sequence[WindowAlignment],
    selected_window_count: int,
) -> float:
    if not observations or selected_window_count <= 0:
        return 0.0
    correlations = np.asarray(
        [observation.correlation for observation in observations], dtype=np.float64
    )
    evidence = min(1.0, len(observations) / selected_window_count)
    return float(np.clip(np.median(correlations) * evidence * 0.40, 0.0, 0.40))


def _failed_input(
    *,
    input_index: int,
    reference_index: int,
    method: str,
    failure: AlignmentFailure,
    selected_window_count: int,
    observations: Sequence[WindowAlignment] = (),
    accepted_window_count: int = 0,
    consistent_window_count: int = 0,
    dispersion_ms: float | None = None,
    confidence: float | None = None,
) -> InputAlignment:
    if confidence is None:
        confidence = _failure_confidence(observations, selected_window_count)
    return InputAlignment(
        input_index=input_index,
        reference_index=reference_index,
        offset_samples=None,
        offset_ms=None,
        confidence=float(np.clip(confidence, 0.0, 1.0)),
        method=method,
        failure=failure,
        selected_window_count=selected_window_count,
        accepted_window_count=accepted_window_count,
        consistent_window_count=consistent_window_count,
        offset_dispersion_ms=dispersion_ms,
        window_observations=tuple(observations),
    )


def _estimate_pair(
    reference: _SignalView,
    target: _SignalView,
    reference_windows: Sequence[tuple[int, int]],
    *,
    input_index: int,
    reference_index: int,
    config: AlignmentConfig,
    conditioned_reference_pair: tuple[np.ndarray, np.ndarray] | None = None,
) -> InputAlignment:
    if conditioned_reference_pair is None:
        conditioned_reference, conditioned_reference_mask = _condition_signal(
            reference.samples, reference.valid_mask
        )
    else:
        conditioned_reference, conditioned_reference_mask = conditioned_reference_pair
    conditioned_target, conditioned_target_mask = _condition_signal(
        target.samples, target.valid_mask
    )
    window_estimates = tuple(
        _estimate_window(
            reference,
            target,
            conditioned_reference,
            conditioned_target,
            conditioned_reference_mask,
            conditioned_target_mask,
            start,
            end,
            config,
        )
        for start, end in reference_windows
    )
    primary_observations = tuple(item.primary for item in window_estimates)
    method = (
        "normalized_cross_correlation+gcc_phat"
        if any(
            candidate.gcc_phat_agrees is not None
            for item in window_estimates
            for candidate in (item.primary, *item.candidates)
        )
        else "normalized_cross_correlation"
    )
    candidate_windows = tuple(
        tuple(candidate for candidate in item.candidates if candidate.accepted)
        for item in window_estimates
    )
    observations = tuple(
        candidates[0] if candidates else primary
        for candidates, primary in zip(candidate_windows, primary_observations)
    )
    accepted_window_count = sum(bool(candidates) for candidates in candidate_windows)
    if accepted_window_count < config.min_consistent_windows:
        available = tuple(
            item for item in primary_observations if item.offset_samples is not None
        )
        if accepted_window_count:
            failure = AlignmentFailure(
                "insufficient_shared_audio",
                "Too few windows contained a reliable shared event.",
            )
        elif sum(
            item.rejection_code == "target_insufficient_signal"
            for item in primary_observations
        ) >= config.min_consistent_windows:
            failure = AlignmentFailure(
                "target_insufficient_signal",
                "The target input did not contain enough usable signal for alignment.",
            )
        elif len(available) < config.min_consistent_windows:
            failure = AlignmentFailure(
                "insufficient_shared_audio",
                "Too few valid overlapping windows were available for "
                "automatic alignment.",
            )
        elif sum(
            item.rejection_code == "offset_at_search_boundary"
            for item in available
        ) >= config.min_consistent_windows:
            failure = AlignmentFailure(
                "offset_at_search_boundary",
                "The best offset reached the configured search boundary.",
            )
        elif sum(
            item.correlation >= config.min_peak_correlation for item in available
        ) < config.min_consistent_windows:
            failure = AlignmentFailure(
                "low_correlation",
                "The recordings did not contain enough strongly correlated windows.",
            )
        else:
            failure = AlignmentFailure(
                "ambiguous_peak",
                "Correlation peaks were too ambiguous to choose a reliable offset.",
            )
        return _failed_input(
            input_index=input_index,
            reference_index=reference_index,
            method=method,
            failure=failure,
            selected_window_count=len(reference_windows),
            observations=observations,
            accepted_window_count=accepted_window_count,
        )

    tolerance_samples = max(
        1,
        int(round(config.consensus_tolerance_ms * reference.sample_rate / 1000.0)),
    )
    clusters = _candidate_clusters(candidate_windows, tolerance_samples)
    if not clusters:
        return _failed_input(
            input_index=input_index,
            reference_index=reference_index,
            method=method,
            failure=AlignmentFailure(
                "ambiguous_peak",
                "No correlation peak produced a stable cross-window candidate.",
            ),
            selected_window_count=len(reference_windows),
            observations=observations,
            accepted_window_count=accepted_window_count,
        )

    best_cluster = clusters[0]
    consistent_count = best_cluster.count
    consensus_ratio = consistent_count / max(1, accepted_window_count)
    cluster = tuple(item for _, item in best_cluster.choices)
    cluster_offsets = np.asarray(
        [item.offset_samples for item in cluster],
        dtype=np.float64,
    )
    cluster_weights = _observation_weights(cluster)
    estimated_offset = best_cluster.estimated_offset
    deviations = np.abs(cluster_offsets - estimated_offset)
    dispersion_samples = _weighted_median(deviations, cluster_weights)
    dispersion_ms = dispersion_samples * 1000.0 / reference.sample_rate

    selected_by_window = dict(best_cluster.choices)
    selected_observations = tuple(
        selected_by_window.get(window_index, observation)
        for window_index, observation in enumerate(observations)
    )

    evaluable_window_indexes: list[int] = []
    for window_index, observation in enumerate(observations):
        window_size = (
            observation.reference_end_sample
            - observation.reference_start_sample
        )
        target_start = observation.reference_start_sample - estimated_offset
        target_end = target_start + window_size
        if target_start < 0 or target_end > target.samples.size:
            continue
        reference_mask = conditioned_reference_mask[
            observation.reference_start_sample : observation.reference_end_sample
        ]
        target_mask = conditioned_target_mask[target_start:target_end]
        valid_intersection = int(np.count_nonzero(reference_mask & target_mask))
        if valid_intersection >= math.ceil(config.min_valid_ratio * window_size):
            evaluable_window_indexes.append(window_index)

    evaluable_window_count = len(evaluable_window_indexes)
    support_ratio = consistent_count / max(1, evaluable_window_count)
    accepted_evaluable_count = sum(
        bool(candidate_windows[window_index])
        for window_index in evaluable_window_indexes
    )
    accepted_ratio = accepted_evaluable_count / max(1, evaluable_window_count)

    if support_ratio < config.min_consensus_ratio:
        if accepted_ratio >= config.min_consensus_ratio:
            failure = AlignmentFailure(
                "inconsistent_offsets",
                "Independent analysis windows favored incompatible offsets.",
            )
        else:
            failure = AlignmentFailure(
                "insufficient_shared_audio",
                "Too few reference windows contained the same shared event.",
            )
        return _failed_input(
            input_index=input_index,
            reference_index=reference_index,
            method=method,
            failure=failure,
            selected_window_count=len(reference_windows),
            observations=selected_observations,
            accepted_window_count=accepted_window_count,
            consistent_window_count=consistent_count,
            dispersion_ms=float(dispersion_ms),
        )

    if (
        consistent_count < config.min_consistent_windows
        or consensus_ratio < config.min_consensus_ratio
        or dispersion_ms > config.max_offset_dispersion_ms
    ):
        return _failed_input(
            input_index=input_index,
            reference_index=reference_index,
            method=method,
            failure=AlignmentFailure(
                "inconsistent_offsets",
                "Independent analysis windows did not agree on one stable offset.",
            ),
            selected_window_count=len(reference_windows),
            observations=selected_observations,
            accepted_window_count=accepted_window_count,
            consistent_window_count=consistent_count,
            dispersion_ms=float(dispersion_ms),
        )

    runner_up = next(
        (
            candidate_cluster
            for candidate_cluster in clusters[1:]
            if abs(
                candidate_cluster.estimated_offset
                - best_cluster.estimated_offset
            )
            > tolerance_samples
        ),
        None,
    )
    if (
        runner_up is not None
        and runner_up.count >= config.min_consistent_windows
        and runner_up.count / max(1, best_cluster.count)
        >= config.min_consensus_ratio
    ):
        return _failed_input(
            input_index=input_index,
            reference_index=reference_index,
            method=method,
            failure=AlignmentFailure(
                "inconsistent_offsets",
                "Multiple cross-window peak clusters remained plausible.",
            ),
            selected_window_count=len(reference_windows),
            observations=selected_observations,
            accepted_window_count=accepted_window_count,
            consistent_window_count=consistent_count,
            dispersion_ms=float(dispersion_ms),
        )

    median_correlation = float(np.median([item.correlation for item in cluster]))
    median_prominence = float(np.median([item.peak_prominence for item in cluster]))
    if median_prominence < config.min_peak_prominence:
        return _failed_input(
            input_index=input_index,
            reference_index=reference_index,
            method=method,
            failure=AlignmentFailure(
                "ambiguous_peak",
                "The consensus peak was not distinct from repeated alternatives.",
            ),
            selected_window_count=len(reference_windows),
            observations=selected_observations,
            accepted_window_count=accepted_window_count,
            consistent_window_count=consistent_count,
            dispersion_ms=float(dispersion_ms),
        )
    correlation_component = np.clip(
        (median_correlation - config.min_peak_correlation)
        / max(1.0e-9, 1.0 - config.min_peak_correlation),
        0.0,
        1.0,
    )
    prominence_component = np.clip(median_prominence / 0.10, 0.0, 1.0)
    stability_component = np.clip(
        1.0 - dispersion_ms / config.max_offset_dispersion_ms,
        0.0,
        1.0,
    )
    evidence_component = min(1.0, support_ratio)
    confidence = float(
        np.clip(
            0.45 * correlation_component
            + 0.20 * prominence_component
            + 0.15 * consensus_ratio
            + 0.10 * stability_component
            + 0.10 * evidence_component,
            0.0,
            1.0,
        )
    )
    if confidence < config.min_confidence:
        return _failed_input(
            input_index=input_index,
            reference_index=reference_index,
            method=method,
            failure=AlignmentFailure(
                "low_confidence",
                "The estimated offset did not meet the confidence threshold.",
            ),
            selected_window_count=len(reference_windows),
            observations=selected_observations,
            accepted_window_count=accepted_window_count,
            consistent_window_count=consistent_count,
            dispersion_ms=float(dispersion_ms),
            confidence=confidence,
        )

    offset_ms = estimated_offset * 1000.0 / reference.sample_rate
    return InputAlignment(
        input_index=input_index,
        reference_index=reference_index,
        offset_samples=estimated_offset,
        offset_ms=float(offset_ms),
        confidence=confidence,
        method=method,
        failure=None,
        selected_window_count=len(reference_windows),
        accepted_window_count=accepted_window_count,
        consistent_window_count=consistent_count,
        offset_dispersion_ms=float(dispersion_ms),
        window_observations=selected_observations,
    )


def _offset_only_diagnostics(result: InputAlignment) -> InputAlignment:
    """Add identity clock diagnostics to a successful legacy offset result."""

    if not result.success:
        return replace(result, correction_method="none")
    residual = float(result.offset_dispersion_ms or 0.0)
    method = "reference" if result.method == "reference" else "offset_only"
    return replace(
        result,
        time_scale=1.0,
        estimated_time_scale=1.0,
        drift_ppm=0.0,
        drift_span_ms=0.0,
        pre_correction_error_ms=residual,
        post_correction_error_ms=residual,
        max_post_correction_error_ms=residual,
        correction_method=method,
        correction_applied=False,
    )


def _reference_time_alignment(
    *,
    input_index: int,
    reference_index: int,
    selected_window_count: int,
) -> InputAlignment:
    return InputAlignment(
        input_index=input_index,
        reference_index=reference_index,
        offset_samples=0,
        offset_ms=0.0,
        confidence=1.0,
        method="reference",
        failure=None,
        selected_window_count=selected_window_count,
        accepted_window_count=selected_window_count,
        consistent_window_count=selected_window_count,
        offset_dispersion_ms=0.0,
        window_observations=(),
        time_scale=1.0,
        estimated_time_scale=1.0,
        drift_ppm=0.0,
        drift_span_ms=0.0,
        pre_correction_error_ms=0.0,
        post_correction_error_ms=0.0,
        max_post_correction_error_ms=0.0,
        correction_method="reference",
        correction_applied=False,
    )


def _insufficient_window_failure(
    primary_observations: Sequence[WindowAlignment],
    accepted_window_count: int,
    config: AlignmentConfig,
) -> AlignmentFailure:
    available = tuple(
        item for item in primary_observations if item.offset_samples is not None
    )
    if accepted_window_count:
        return AlignmentFailure(
            "insufficient_shared_audio",
            "Too few windows contained a reliable shared event.",
        )
    if sum(
        item.rejection_code == "target_insufficient_signal"
        for item in primary_observations
    ) >= config.min_consistent_windows:
        return AlignmentFailure(
            "target_insufficient_signal",
            "The target input did not contain enough usable signal for alignment.",
        )
    if len(available) < config.min_consistent_windows:
        return AlignmentFailure(
            "insufficient_shared_audio",
            "Too few valid overlapping windows were available for automatic alignment.",
        )
    if sum(
        item.rejection_code == "offset_at_search_boundary" for item in available
    ) >= config.min_consistent_windows:
        return AlignmentFailure(
            "offset_at_search_boundary",
            "The best offset reached the configured search boundary.",
        )
    if sum(
        item.correlation >= config.min_peak_correlation for item in available
    ) < config.min_consistent_windows:
        return AlignmentFailure(
            "low_correlation",
            "The recordings did not contain enough strongly correlated windows.",
        )
    return AlignmentFailure(
        "ambiguous_peak",
        "Correlation peaks were too ambiguous to choose reliable drift anchors.",
    )


def _estimate_time_pair(
    reference: _SignalView,
    target: _SignalView,
    reference_windows: Sequence[tuple[int, int]],
    *,
    input_index: int,
    reference_index: int,
    config: AlignmentConfig,
    conditioned_reference_pair: tuple[np.ndarray, np.ndarray] | None = None,
) -> InputAlignment:
    """Estimate a guarded affine target-to-reference clock transform."""

    if conditioned_reference_pair is None:
        conditioned_reference, conditioned_reference_mask = _condition_signal(
            reference.samples,
            reference.valid_mask,
        )
    else:
        conditioned_reference, conditioned_reference_mask = conditioned_reference_pair
    conditioned_target, conditioned_target_mask = _condition_signal(
        target.samples,
        target.valid_mask,
    )
    window_estimates = tuple(
        _estimate_window(
            reference,
            target,
            conditioned_reference,
            conditioned_target,
            conditioned_reference_mask,
            conditioned_target_mask,
            start,
            end,
            config,
        )
        for start, end in reference_windows
    )
    primary_observations = tuple(item.primary for item in window_estimates)
    method = (
        "normalized_cross_correlation+gcc_phat"
        if any(
            candidate.gcc_phat_agrees is not None
            for item in window_estimates
            for candidate in (item.primary, *item.candidates)
        )
        else "normalized_cross_correlation"
    )
    candidate_windows = tuple(
        tuple(candidate for candidate in item.candidates if candidate.accepted)
        for item in window_estimates
    )
    primary_or_candidate = tuple(
        candidates[0] if candidates else primary
        for candidates, primary in zip(candidate_windows, primary_observations)
    )
    accepted_window_count = sum(bool(candidates) for candidates in candidate_windows)
    minimum_anchor_count = max(
        config.min_consistent_windows,
        config.drift_min_anchors,
    )
    if accepted_window_count < minimum_anchor_count:
        return _failed_input(
            input_index=input_index,
            reference_index=reference_index,
            method=method,
            failure=_insufficient_window_failure(
                primary_observations,
                accepted_window_count,
                config,
            ),
            selected_window_count=len(reference_windows),
            observations=primary_or_candidate,
            accepted_window_count=accepted_window_count,
        )

    tolerance_samples = max(
        1.0,
        config.drift_residual_tolerance_ms * reference.sample_rate / 1000.0,
    )
    fits = _affine_candidate_fits(
        candidate_windows,
        tolerance_samples=tolerance_samples,
        reference_sample_count=reference.samples.size,
        # Keep a wider diagnostic search than the automatic-correction limit.
        # Otherwise an over-limit real trend can be discarded in favor of a
        # short, wrong within-limit subset and its reported drift can have the
        # wrong sign.  The configured limit is enforced below before success.
        max_abs_ppm=max(100_000.0, config.drift_max_abs_ppm * 10.0),
    )
    if not fits:
        message = (
            "Reliable local delays did not form a plausible linear clock model; "
            "manual review is required."
        )
        return InputAlignment(
            input_index=input_index,
            reference_index=reference_index,
            offset_samples=None,
            offset_ms=None,
            confidence=_failure_confidence(
                primary_or_candidate,
                len(reference_windows),
            ),
            method=method,
            failure=AlignmentFailure("unstable_drift", message),
            selected_window_count=len(reference_windows),
            accepted_window_count=accepted_window_count,
            consistent_window_count=0,
            offset_dispersion_ms=None,
            window_observations=primary_or_candidate,
            correction_method="manual_review",
            correction_applied=False,
            requires_manual_review=True,
            warning=AlignmentWarning("manual_review_required", message),
        )

    best = fits[0]
    comparison_positions = (0.0, max(0.0, reference.samples.size - 1.0))

    def fitted_offset(fit: _AffineFit, reference_position: float) -> float:
        target_position = (reference_position - fit.intercept) / fit.scale
        return reference_position - target_position

    runner_up = next(
        (
            candidate
            for candidate in fits[1:]
            if candidate.count >= minimum_anchor_count
            and candidate.count / max(1, best.count) >= config.min_consensus_ratio
            and candidate.span_ratio >= config.drift_min_span_ratio
            and set(candidate.regions) == {"start", "middle", "end"}
            and max(
                abs(
                    fitted_offset(candidate, position)
                    - fitted_offset(best, position)
                )
                for position in comparison_positions
            )
            > tolerance_samples
        ),
        None,
    )
    selected_observations = _closest_affine_observations(
        candidate_windows,
        primary_observations,
        best.scale,
        best.intercept,
    )
    reliable_observations = tuple(
        observation
        for observation in selected_observations
        if observation.accepted and observation.offset_samples is not None
    )
    offsets = np.asarray(
        [observation.offset_samples for observation in reliable_observations],
        dtype=np.float64,
    )
    weights = _observation_weights(reliable_observations)
    constant_offset = _weighted_median(offsets, weights)
    static_residuals = offsets - constant_offset
    pre_error_samples = _weighted_rms(static_residuals, weights)
    dispersion_samples = _weighted_median(np.abs(static_residuals), weights)

    proposed_residuals = np.asarray(
        [
            _affine_residual(observation, best.scale, best.intercept)
            for observation in reliable_observations
        ],
        dtype=np.float64,
    )
    proposed_error_samples = _weighted_rms(proposed_residuals, weights)
    proposed_max_error_samples = (
        float(np.max(np.abs(proposed_residuals)))
        if proposed_residuals.size
        else 0.0
    )
    best_targets = np.asarray(
        [
            _observation_coordinates(observation)[0]
            for _, observation in best.choices
        ],
        dtype=np.float64,
    )
    drift_span_ms = (
        (best.scale - 1.0)
        * (float(np.ptp(best_targets)) if best_targets.size else 0.0)
        * 1000.0
        / reference.sample_rate
    )
    inlier_ratio = best.count / max(1, accepted_window_count)
    median_correlation = float(
        np.median([observation.correlation for _, observation in best.choices])
    )
    median_prominence = float(
        np.median([observation.peak_prominence for _, observation in best.choices])
    )
    correlation_component = float(
        np.clip(
            (median_correlation - config.min_peak_correlation)
            / max(1.0e-9, 1.0 - config.min_peak_correlation),
            0.0,
            1.0,
        )
    )
    prominence_component = float(np.clip(median_prominence / 0.10, 0.0, 1.0))
    residual_component = float(
        np.clip(
            1.0
            - (best.rms_residual * 1000.0 / reference.sample_rate)
            / config.drift_residual_tolerance_ms,
            0.0,
            1.0,
        )
    )
    confidence = float(
        np.clip(
            0.35 * correlation_component
            + 0.20 * prominence_component
            + 0.20 * inlier_ratio
            + 0.15 * min(1.0, best.span_ratio)
            + 0.10 * residual_component,
            0.0,
            1.0,
        )
    )

    issue_code: str | None = None
    issue_message: str | None = None
    if best.count < minimum_anchor_count:
        issue_code = "insufficient_drift_anchors"
        issue_message = "Too few delay anchors supported one clock model."
    elif inlier_ratio < config.drift_min_inlier_ratio:
        issue_code = "unstable_drift"
        issue_message = "Delay anchors changed nonlinearly or inconsistently over time."
    elif best.span_ratio < config.drift_min_span_ratio:
        issue_code = "insufficient_drift_coverage"
        issue_message = "Delay anchors did not cover enough of the recording duration."
    elif set(best.regions) != {"start", "middle", "end"}:
        issue_code = "insufficient_drift_coverage"
        issue_message = "Start, middle, and end delay anchors were not all available."
    elif runner_up is not None:
        issue_code = "ambiguous_drift_model"
        issue_message = "Multiple incompatible clock-drift tracks remained plausible."
    elif median_prominence < config.min_peak_prominence:
        issue_code = "ambiguous_peak"
        issue_message = "Drift anchors were ambiguous because of repeated correlation peaks."
    elif (
        proposed_max_error_samples * 1000.0 / reference.sample_rate
        > config.drift_residual_tolerance_ms
    ):
        issue_code = "unstable_drift"
        issue_message = "At least one reliable delay anchor was not explained linearly."
    elif (
        best.rms_residual * 1000.0 / reference.sample_rate
        > config.drift_residual_tolerance_ms
    ):
        issue_code = "unstable_drift"
        issue_message = "Affine correction left excessive timing residuals."
    elif abs(best.drift_ppm) > config.drift_max_abs_ppm:
        issue_code = "drift_out_of_range"
        issue_message = "The estimated clock drift exceeds the automatic limit."
    elif confidence < config.min_confidence:
        issue_code = "drift_low_confidence"
        issue_message = "The affine clock estimate did not meet the confidence threshold."

    if issue_code is not None:
        assert issue_message is not None
        review_message = f"{issue_message} Manual review is required."
        anchors = _drift_anchors(
            reliable_observations,
            scale=best.scale,
            intercept=best.intercept,
            tolerance_samples=tolerance_samples,
            reference_sample_count=reference.samples.size,
            sample_rate=reference.sample_rate,
        )
        return InputAlignment(
            input_index=input_index,
            reference_index=reference_index,
            offset_samples=None,
            offset_ms=None,
            confidence=confidence,
            method=method,
            failure=AlignmentFailure(issue_code, review_message),
            selected_window_count=len(reference_windows),
            accepted_window_count=accepted_window_count,
            consistent_window_count=best.count,
            offset_dispersion_ms=float(
                dispersion_samples * 1000.0 / reference.sample_rate
            ),
            window_observations=selected_observations,
            time_scale=None,
            estimated_time_scale=float(best.scale),
            drift_ppm=float(best.drift_ppm),
            drift_span_ms=float(drift_span_ms),
            pre_correction_error_ms=float(
                pre_error_samples * 1000.0 / reference.sample_rate
            ),
            post_correction_error_ms=float(
                proposed_error_samples * 1000.0 / reference.sample_rate
            ),
            max_post_correction_error_ms=float(
                proposed_max_error_samples * 1000.0 / reference.sample_rate
            ),
            correction_method="manual_review",
            correction_applied=False,
            requires_manual_review=True,
            warning=AlignmentWarning("manual_review_required", review_message),
            drift_anchors=anchors,
        )

    def applied_statistics(
        scale: float,
        intercept: float,
    ) -> tuple[np.ndarray, float, float]:
        residuals = np.asarray(
            [
                _affine_residual(observation, scale, intercept)
                for observation in reliable_observations
            ],
            dtype=np.float64,
        )
        rms = _weighted_rms(residuals, weights)
        maximum = float(np.max(np.abs(residuals))) if residuals.size else 0.0
        return residuals, rms, maximum

    static_intercept = float(round(constant_offset))
    static_statistics = applied_statistics(1.0, static_intercept)
    affine_options = []
    for candidate_intercept in sorted(
        {math.floor(best.intercept), math.ceil(best.intercept)}
    ):
        statistics = applied_statistics(best.scale, float(candidate_intercept))
        affine_options.append((float(candidate_intercept), *statistics))
    (
        affine_intercept,
        affine_residuals,
        affine_error_samples,
        affine_max_error_samples,
    ) = min(
        affine_options,
        key=lambda option: (
            option[3],
            option[2],
            abs(option[0] - best.intercept),
            option[0],
        ),
    )

    correction_threshold_ms = max(
        config.drift_min_correction_ms,
        3.0 * proposed_error_samples * 1000.0 / reference.sample_rate,
    )
    correction_applied = (
        abs(drift_span_ms) >= correction_threshold_ms
        or static_statistics[2] > tolerance_samples
    )
    if correction_applied:
        applied_scale = best.scale
        applied_intercept = affine_intercept
        applied_residuals = affine_residuals
        post_error_samples = affine_error_samples
        max_post_error_samples = affine_max_error_samples
        correction_method = "affine"
    else:
        applied_scale = 1.0
        applied_intercept = static_intercept
        applied_residuals, post_error_samples, max_post_error_samples = (
            static_statistics
        )
        correction_method = "offset_only"
    anchors = _drift_anchors(
        reliable_observations,
        scale=applied_scale,
        intercept=applied_intercept,
        tolerance_samples=tolerance_samples,
        reference_sample_count=reference.samples.size,
        sample_rate=reference.sample_rate,
    )
    offset_samples = int(round(applied_intercept))
    result = InputAlignment(
        input_index=input_index,
        reference_index=reference_index,
        offset_samples=offset_samples,
        offset_ms=float(offset_samples * 1000.0 / reference.sample_rate),
        confidence=confidence,
        method=method,
        failure=None,
        selected_window_count=len(reference_windows),
        accepted_window_count=accepted_window_count,
        consistent_window_count=best.count,
        offset_dispersion_ms=float(
            dispersion_samples * 1000.0 / reference.sample_rate
        ),
        window_observations=selected_observations,
        time_scale=float(applied_scale),
        estimated_time_scale=float(best.scale),
        drift_ppm=float(best.drift_ppm),
        drift_span_ms=float(drift_span_ms),
        pre_correction_error_ms=float(
            pre_error_samples * 1000.0 / reference.sample_rate
        ),
        post_correction_error_ms=float(
            post_error_samples * 1000.0 / reference.sample_rate
        ),
        max_post_correction_error_ms=float(
            max_post_error_samples * 1000.0 / reference.sample_rate
        ),
        correction_method=correction_method,
        correction_applied=correction_applied,
        requires_manual_review=False,
        warning=None,
        drift_anchors=anchors,
    )
    rounding_epsilon = (
        np.finfo(np.float64).eps * max(1.0, tolerance_samples) * 16.0
    )
    if max_post_error_samples > tolerance_samples + rounding_epsilon:
        review_message = (
            "The applicable affine correction left excessive timing residuals. "
            "Manual review is required."
        )
        return replace(
            result,
            offset_samples=None,
            offset_ms=None,
            failure=AlignmentFailure("unstable_drift", review_message),
            time_scale=None,
            correction_method="manual_review",
            correction_applied=False,
            requires_manual_review=True,
            warning=AlignmentWarning("manual_review_required", review_message),
        )
    assert max_post_error_samples <= tolerance_samples + rounding_epsilon
    return result


def estimate_initial_offset(
    reference: AnalysisSignalLike,
    target: AnalysisSignalLike,
    *,
    config: AlignmentConfig | None = None,
    input_index: int = 1,
    reference_index: int = 0,
) -> InputAlignment:
    """Estimate one target's placement offset on the reference timeline."""

    settings = config if config is not None else AlignmentConfig()
    if not isinstance(settings, AlignmentConfig):
        raise TypeError("config must be an AlignmentConfig")
    target_index = _index("input_index", input_index)
    base_index = _index("reference_index", reference_index)
    if target_index < 0:
        raise ValueError("input_index must not be negative")
    if base_index < 0:
        raise ValueError("reference_index must not be negative")
    reference_view = _coerce_signal(reference, "reference")
    target_view = _coerce_signal(target, "target")
    if reference_view.sample_rate != target_view.sample_rate:
        raise ValueError("reference and target sample rates must match")

    windows = _select_reference_windows(reference_view, settings)
    if len(windows) < settings.min_reference_windows:
        return _failed_input(
            input_index=target_index,
            reference_index=base_index,
            method="normalized_cross_correlation",
            failure=AlignmentFailure(
                "reference_insufficient_signal",
                "The reference input does not contain enough valid active windows.",
            ),
            selected_window_count=len(windows),
        )
    return _estimate_pair(
        reference_view,
        target_view,
        windows,
        input_index=target_index,
        reference_index=base_index,
        config=settings,
    )


def estimate_initial_offsets(
    signals: Sequence[AnalysisSignalLike],
    *,
    reference_index: int = 0,
    config: AlignmentConfig | None = None,
) -> MultiAudioAlignment:
    """Estimate per-input initial offsets while retaining file-level failures."""

    settings = config if config is not None else AlignmentConfig()
    if not isinstance(settings, AlignmentConfig):
        raise TypeError("config must be an AlignmentConfig")
    views = tuple(
        _coerce_signal(signal, f"signals[{index}]")
        for index, signal in enumerate(signals)
    )
    if len(views) < 2:
        raise ValueError("at least two analysis signals are required")
    base_index = _index("reference_index", reference_index)
    if base_index < 0 or base_index >= len(views):
        raise ValueError("reference_index is outside the input range")

    sample_rate = views[0].sample_rate
    if any(view.sample_rate != sample_rate for view in views[1:]):
        raise ValueError("all analysis signal sample rates must match")

    reference = views[base_index]
    windows = _select_reference_windows(reference, settings)
    if len(windows) < settings.min_reference_windows:
        failure = AlignmentFailure(
            "reference_insufficient_signal",
            "The reference input does not contain enough valid active windows.",
        )
        results = tuple(
            _failed_input(
                input_index=index,
                reference_index=base_index,
                method=(
                    "reference"
                    if index == base_index
                    else "normalized_cross_correlation"
                ),
                failure=failure,
                selected_window_count=len(windows),
            )
            for index in range(len(views))
        )
        return MultiAudioAlignment(
            reference_index=base_index,
            sample_rate=sample_rate,
            inputs=results,
            reference_windows=windows,
        )

    results: list[InputAlignment] = []
    conditioned_reference_pair = _condition_signal(
        reference.samples,
        reference.valid_mask,
    )
    for index, target in enumerate(views):
        if index == base_index:
            results.append(
                InputAlignment(
                    input_index=index,
                    reference_index=base_index,
                    offset_samples=0,
                    offset_ms=0.0,
                    confidence=1.0,
                    method="reference",
                    failure=None,
                    selected_window_count=len(windows),
                    accepted_window_count=len(windows),
                    consistent_window_count=len(windows),
                    offset_dispersion_ms=0.0,
                    window_observations=(),
                )
            )
            continue
        results.append(
            _estimate_pair(
                reference,
                target,
                windows,
                input_index=index,
                reference_index=base_index,
                config=settings,
                conditioned_reference_pair=conditioned_reference_pair,
            )
        )

    return MultiAudioAlignment(
        reference_index=base_index,
        sample_rate=sample_rate,
        inputs=tuple(results),
        reference_windows=windows,
    )


def estimate_time_alignment(
    reference: AnalysisSignalLike,
    target: AnalysisSignalLike,
    *,
    config: AlignmentConfig | None = None,
    input_index: int = 1,
    reference_index: int = 0,
) -> InputAlignment:
    """Estimate offset and, for long inputs, a guarded affine clock model.

    The returned transform maps target samples onto the unchanged reference
    time axis as ``reference = time_scale * target + offset_samples``.  Short
    inputs retain the MM-DEV-004 offset-only behavior because their baseline is
    too small for a reliable clock-rate estimate.
    """

    settings = config if config is not None else AlignmentConfig()
    if not isinstance(settings, AlignmentConfig):
        raise TypeError("config must be an AlignmentConfig")
    target_index = _index("input_index", input_index)
    base_index = _index("reference_index", reference_index)
    if target_index < 0:
        raise ValueError("input_index must not be negative")
    if base_index < 0:
        raise ValueError("reference_index must not be negative")
    reference_view = _coerce_signal(reference, "reference")
    target_view = _coerce_signal(target, "target")
    if reference_view.sample_rate != target_view.sample_rate:
        raise ValueError("reference and target sample rates must match")

    duration_seconds = reference_view.samples.size / reference_view.sample_rate
    analyze_drift = (
        duration_seconds >= settings.drift_min_duration_seconds
        and settings.max_windows >= settings.drift_min_anchors
    )
    windows = _select_reference_windows(
        reference_view,
        settings,
        reserve_drift_edges=analyze_drift,
    )
    if len(windows) < settings.min_reference_windows:
        return _failed_input(
            input_index=target_index,
            reference_index=base_index,
            method="normalized_cross_correlation",
            failure=AlignmentFailure(
                "reference_insufficient_signal",
                "The reference input does not contain enough valid active windows.",
            ),
            selected_window_count=len(windows),
        )
    if not analyze_drift:
        return _offset_only_diagnostics(
            _estimate_pair(
                reference_view,
                target_view,
                windows,
                input_index=target_index,
                reference_index=base_index,
                config=settings,
            )
        )
    return _estimate_time_pair(
        reference_view,
        target_view,
        windows,
        input_index=target_index,
        reference_index=base_index,
        config=settings,
    )


def estimate_time_alignments(
    signals: Sequence[AnalysisSignalLike],
    *,
    reference_index: int = 0,
    config: AlignmentConfig | None = None,
) -> MultiAudioAlignment:
    """Estimate safe per-input affine transforms on one reference time axis."""

    settings = config if config is not None else AlignmentConfig()
    if not isinstance(settings, AlignmentConfig):
        raise TypeError("config must be an AlignmentConfig")
    views = tuple(
        _coerce_signal(signal, f"signals[{index}]")
        for index, signal in enumerate(signals)
    )
    if len(views) < 2:
        raise ValueError("at least two analysis signals are required")
    base_index = _index("reference_index", reference_index)
    if base_index < 0 or base_index >= len(views):
        raise ValueError("reference_index is outside the input range")
    sample_rate = views[0].sample_rate
    if any(view.sample_rate != sample_rate for view in views[1:]):
        raise ValueError("all analysis signal sample rates must match")

    reference = views[base_index]
    analyze_drift = (
        reference.samples.size / reference.sample_rate
        >= settings.drift_min_duration_seconds
        and settings.max_windows >= settings.drift_min_anchors
    )
    windows = _select_reference_windows(
        reference,
        settings,
        reserve_drift_edges=analyze_drift,
    )
    if len(windows) < settings.min_reference_windows:
        failure = AlignmentFailure(
            "reference_insufficient_signal",
            "The reference input does not contain enough valid active windows.",
        )
        results = tuple(
            _failed_input(
                input_index=index,
                reference_index=base_index,
                method=(
                    "reference"
                    if index == base_index
                    else "normalized_cross_correlation"
                ),
                failure=failure,
                selected_window_count=len(windows),
            )
            for index in range(len(views))
        )
        return MultiAudioAlignment(
            reference_index=base_index,
            sample_rate=sample_rate,
            inputs=results,
            reference_windows=windows,
        )

    conditioned_reference_pair = _condition_signal(
        reference.samples,
        reference.valid_mask,
    )
    results: list[InputAlignment] = []
    for index, target in enumerate(views):
        if index == base_index:
            results.append(
                _reference_time_alignment(
                    input_index=index,
                    reference_index=base_index,
                    selected_window_count=len(windows),
                )
            )
            continue
        if analyze_drift:
            results.append(
                _estimate_time_pair(
                    reference,
                    target,
                    windows,
                    input_index=index,
                    reference_index=base_index,
                    config=settings,
                    conditioned_reference_pair=conditioned_reference_pair,
                )
            )
        else:
            results.append(
                _offset_only_diagnostics(
                    _estimate_pair(
                        reference,
                        target,
                        windows,
                        input_index=index,
                        reference_index=base_index,
                        config=settings,
                        conditioned_reference_pair=conditioned_reference_pair,
                    )
                )
            )

    return MultiAudioAlignment(
        reference_index=base_index,
        sample_rate=sample_rate,
        inputs=tuple(results),
        reference_windows=windows,
    )


__all__ = [
    "AlignmentConfig",
    "AlignmentFailure",
    "AlignmentWarning",
    "DriftAnchor",
    "InputAlignment",
    "InputAlignmentResult",
    "MultiAudioAlignment",
    "MultiAudioAlignmentResult",
    "WindowAlignment",
    "estimate_initial_offset",
    "estimate_initial_offsets",
    "estimate_time_alignment",
    "estimate_time_alignments",
]
