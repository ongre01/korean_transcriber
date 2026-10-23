"""Initial time-offset estimation for offline multi-microphone recordings.

The canonical ``offset_samples`` in this module is a *timeline placement*
offset: it is the position on the reference timeline where target sample zero
belongs.  A positive value means the target recording started later than the
reference recording.  This convention is intentionally compatible with
``pipeline.place_signal_on_timeline(..., start_sample=offset_samples)`` and is
the opposite of the target-minus-reference lag commonly returned by a cross
correlator.

Only decoded analysis samples and their validity masks are considered.  File
timestamps are deliberately absent from this API, so they cannot accidentally
become synchronization evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
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

    def __post_init__(self) -> None:
        _require_positive("window_seconds", self.window_seconds)
        _require_positive("hop_seconds", self.hop_seconds)
        _require_positive("max_offset_seconds", self.max_offset_seconds)
        _require_positive("peak_exclusion_ms", self.peak_exclusion_ms)
        _require_positive("consensus_tolerance_ms", self.consensus_tolerance_ms)
        _require_positive(
            "max_offset_dispersion_ms", self.max_offset_dispersion_ms
        )
        _require_non_negative("min_signal_rms", self.min_signal_rms)

        _require_integer_at_least("max_windows", self.max_windows, 1)
        _require_integer_at_least(
            "min_reference_windows", self.min_reference_windows, 1
        )
        _require_integer_at_least(
            "min_consistent_windows", self.min_consistent_windows, 1
        )
        if self.min_reference_windows > self.max_windows:
            raise ValueError("min_reference_windows must not exceed max_windows")
        if self.min_consistent_windows > self.max_windows:
            raise ValueError("min_consistent_windows must not exceed max_windows")
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

    @property
    def ok(self) -> bool:
        return self.failure is None and self.offset_samples is not None

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
    seed_ranked = sorted(
        set(seeds),
        key=lambda index: (-float(scores[index]), int(starts[index])),
    )
    selected: list[int] = []
    minimum_separation = max(1, window // 2)
    for index in (*seed_ranked, *ranked):
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


__all__ = [
    "AlignmentConfig",
    "AlignmentFailure",
    "InputAlignment",
    "InputAlignmentResult",
    "MultiAudioAlignment",
    "MultiAudioAlignmentResult",
    "WindowAlignment",
    "estimate_initial_offset",
    "estimate_initial_offsets",
]
