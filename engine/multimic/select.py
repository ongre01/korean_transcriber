"""Stable channel selection on an aligned multi-microphone timeline.

The quality layer deliberately produces overlapping analysis windows.  This
module turns those measurements into a deterministic, non-overlapping channel
plan while preserving the complete reference timeline.  Selection is kept
separate from audio rendering so MM-DEV-010 can consume the plan without this
stage touching source files.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real
import operator
from typing import Iterable, Sequence

from .quality import (
    ChannelQualityReport,
    InputQualityScore,
    QualityAnalysisWindow,
    SpeechCandidateRegion,
)

try:  # Support both ``engine.multimic`` and the application's top-level layout.
    from ..vad import SpeechRegion
except ImportError:  # pragma: no cover - exercised by the application's layout
    from vad import SpeechRegion  # type: ignore[no-redef]


_REASON_PRIORITY = {
    "manual_override": 0,
    "single_input_speech": 1,
    "speech_preservation": 2,
    "current_input_unavailable": 3,
    "current_input_unscoreable": 4,
    "fallback_most_valid": 5,
    "better_quality": 6,
    "hysteresis_hold": 7,
    "minimum_hold": 8,
    "best_quality": 9,
}


def _finite_number(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _unit_interval(name: str, value: object) -> float:
    result = _finite_number(name, value)
    if result < 0.0 or result > 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return result


def _sample_index(name: str, value: object) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        return operator.index(value)
    except TypeError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _unique_reasons(values: Iterable[str]) -> tuple[str, ...]:
    unique = tuple(dict.fromkeys(value for value in values if value))
    return tuple(
        sorted(
            unique,
            key=lambda value: (_REASON_PRIORITY.get(value, 100), value),
        )
    )


@dataclass(frozen=True)
class ChannelSelectionConfig:
    """Tunable stability and diagnostic thresholds for channel selection."""

    hysteresis_margin: float = 0.05
    minimum_hold_seconds: float = 2.5
    boundary_search_seconds: float = 0.5
    low_quality_threshold: float = 0.20

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "hysteresis_margin",
            _unit_interval("hysteresis_margin", self.hysteresis_margin),
        )
        hold = _finite_number(
            "minimum_hold_seconds", self.minimum_hold_seconds
        )
        if hold < 0.0:
            raise ValueError("minimum_hold_seconds must be at least 0")
        object.__setattr__(self, "minimum_hold_seconds", hold)
        search = _finite_number(
            "boundary_search_seconds", self.boundary_search_seconds
        )
        if search < 0.0:
            raise ValueError("boundary_search_seconds must be at least 0")
        object.__setattr__(self, "boundary_search_seconds", search)
        object.__setattr__(
            self,
            "low_quality_threshold",
            _unit_interval(
                "low_quality_threshold", self.low_quality_threshold
            ),
        )

    @property
    def switch_margin(self) -> float:
        """Compatibility name for callers describing the hysteresis delta."""

        return self.hysteresis_margin

    @property
    def min_hold_seconds(self) -> float:
        return self.minimum_hold_seconds

    @property
    def boundary_snap_seconds(self) -> float:
        return self.boundary_search_seconds

    def to_dict(self) -> dict[str, float]:
        return {
            "hysteresis_margin": float(self.hysteresis_margin),
            "minimum_hold_seconds": float(self.minimum_hold_seconds),
            "boundary_search_seconds": float(self.boundary_search_seconds),
            "low_quality_threshold": float(self.low_quality_threshold),
        }


@dataclass(frozen=True)
class ChannelSelectionOverride:
    """A user-selected channel on a half-open reference-timeline interval."""

    start_sample: int
    end_sample: int
    input_index: int
    reason: str = "user_override"

    def __post_init__(self) -> None:
        start = _sample_index("start_sample", self.start_sample)
        end = _sample_index("end_sample", self.end_sample)
        index = _sample_index("input_index", self.input_index)
        if start < 0 or end <= start:
            raise ValueError(
                "override bounds must satisfy 0 <= start_sample < end_sample"
            )
        if index < 0:
            raise ValueError("input_index must be at least 0")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")
        object.__setattr__(self, "start_sample", start)
        object.__setattr__(self, "end_sample", end)
        object.__setattr__(self, "input_index", index)
        object.__setattr__(self, "reason", self.reason.strip())

    @property
    def region(self) -> SpeechRegion:
        return SpeechRegion(self.start_sample, self.end_sample)

    @classmethod
    def from_region(
        cls,
        region: SpeechRegion,
        input_index: int,
        *,
        reason: str = "user_override",
    ) -> "ChannelSelectionOverride":
        if not isinstance(region, SpeechRegion):
            raise TypeError("region must be a SpeechRegion")
        return cls(
            region.start_sample,
            region.end_sample,
            input_index,
            reason,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "start_sample": self.start_sample,
            "end_sample": self.end_sample,
            "input_index": self.input_index,
            "reason": self.reason,
        }


# The ticket and GUI use both terms.  Keep one concrete type so validation and
# equality remain identical whichever public spelling a caller chooses.
ManualChannelOverride = ChannelSelectionOverride


@dataclass(frozen=True)
class ChannelSelectionSegment:
    """One final non-overlapping interval in the selected channel plan."""

    region: SpeechRegion
    selected_input_index: int
    automatic_input_index: int
    quality_score: float | None
    confidence: float
    reason_codes: tuple[str, ...]
    selection_source: str = "automatic"
    quality_score_coverage_ratio: float = 0.0
    override_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.region, SpeechRegion):
            raise TypeError("region must be a SpeechRegion")
        if self.region.sample_count <= 0:
            raise ValueError("selection segment must not be empty")
        for name in ("selected_input_index", "automatic_input_index"):
            value = _sample_index(name, getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} must be at least 0")
            object.__setattr__(self, name, value)
        if self.quality_score is not None:
            object.__setattr__(
                self,
                "quality_score",
                _unit_interval("quality_score", self.quality_score),
            )
        object.__setattr__(
            self,
            "confidence",
            _unit_interval("confidence", self.confidence),
        )
        object.__setattr__(
            self,
            "quality_score_coverage_ratio",
            _unit_interval(
                "quality_score_coverage_ratio",
                self.quality_score_coverage_ratio,
            ),
        )
        reasons = _unique_reasons(self.reason_codes)
        if not reasons:
            raise ValueError("reason_codes must contain at least one reason")
        object.__setattr__(self, "reason_codes", reasons)
        if self.selection_source not in {"automatic", "manual_override"}:
            raise ValueError(
                'selection_source must be "automatic" or "manual_override"'
            )
        if self.override_reason is not None:
            if (
                not isinstance(self.override_reason, str)
                or not self.override_reason.strip()
            ):
                raise ValueError("override_reason must be a non-empty string")
            if self.selection_source != "manual_override":
                raise ValueError(
                    "override_reason is only valid for a manual override"
                )
            object.__setattr__(
                self, "override_reason", self.override_reason.strip()
            )

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
    def reason(self) -> str:
        return self.reason_codes[0]

    @property
    def manual_override(self) -> bool:
        return self.selection_source == "manual_override"

    def to_dict(self, sample_rate: int | None = None) -> dict[str, object]:
        result: dict[str, object] = {
            "start_sample": self.start_sample,
            "end_sample": self.end_sample,
            "selected_input_index": self.selected_input_index,
            "automatic_input_index": self.automatic_input_index,
            "quality_score": self.quality_score,
            "quality_score_coverage_ratio": float(
                self.quality_score_coverage_ratio
            ),
            "confidence": float(self.confidence),
            "reason": self.reason,
            "reason_codes": list(self.reason_codes),
            "selection_source": self.selection_source,
            "manual_override": self.manual_override,
            "override_reason": self.override_reason,
        }
        if sample_rate is not None:
            rate = _sample_index("sample_rate", sample_rate)
            if rate <= 0:
                raise ValueError("sample_rate must be positive")
            result["start"] = self.start_sample / rate
            result["end"] = self.end_sample / rate
        return result


@dataclass(frozen=True)
class ChannelSelectionWarning:
    code: str
    message: str
    region: SpeechRegion | None = None
    input_index: int | None = None

    def to_dict(self, sample_rate: int | None = None) -> dict[str, object]:
        result: dict[str, object] = {
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
        if sample_rate is not None and self.region is not None:
            rate = _sample_index("sample_rate", sample_rate)
            if rate <= 0:
                raise ValueError("sample_rate must be positive")
            result["start"] = self.region.start_sample / rate
            result["end"] = self.region.end_sample / rate
        return result


@dataclass(frozen=True)
class ChannelSelectionReport:
    """Complete MM-DEV-009 output on the canonical reference timeline."""

    sample_rate: int
    timeline_sample_count: int
    reference_index: int
    config: ChannelSelectionConfig
    segments: tuple[ChannelSelectionSegment, ...]
    warnings: tuple[ChannelSelectionWarning, ...]
    quality_report: ChannelQualityReport

    @property
    def selections(self) -> tuple[ChannelSelectionSegment, ...]:
        return self.segments

    @property
    def selection_segments(self) -> tuple[ChannelSelectionSegment, ...]:
        return self.segments

    @property
    def switch_count(self) -> int:
        return sum(
            previous.selected_input_index != current.selected_input_index
            for previous, current in zip(self.segments, self.segments[1:])
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "sample_rate": int(self.sample_rate),
            "timeline_sample_count": int(self.timeline_sample_count),
            "duration_seconds": (
                self.timeline_sample_count / self.sample_rate
            ),
            "reference_index": int(self.reference_index),
            "config": self.config.to_dict(),
            "switch_count": self.switch_count,
            "segments": [
                segment.to_dict(self.sample_rate) for segment in self.segments
            ],
            "warnings": [
                warning.to_dict(self.sample_rate) for warning in self.warnings
            ],
            "quality_analysis": self.quality_report.to_dict(),
        }


@dataclass
class _Decision:
    start_sample: int
    end_sample: int
    selected_input_index: int
    automatic_input_index: int
    quality_score: float | None
    confidence: float
    reason_codes: tuple[str, ...]
    selection_source: str = "automatic"
    locked_start: bool = False
    quality_score_coverage_ratio: float = 0.0
    override_reason: str | None = None

    @property
    def sample_count(self) -> int:
        return self.end_sample - self.start_sample


@dataclass(frozen=True)
class _Proposal:
    input_index: int
    score: float | None
    used_fallback: bool


def _overlap_samples(
    regions: Sequence[SpeechRegion],
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


def _contains_sample(regions: Sequence[SpeechRegion], sample: int) -> bool:
    return any(
        region.start_sample <= sample < region.end_sample
        for region in regions
    )


def _seconds_to_samples(name: str, value: float, sample_rate: int) -> int:
    scaled = value * sample_rate
    if not math.isfinite(scaled) or scaled > 2 ** 63 - 1:
        raise ValueError(f"{name} is too large for the analysis sample rate")
    return max(0, int(round(scaled)))


def _validate_report(
    report: ChannelQualityReport,
) -> tuple[int, tuple[int, ...], tuple[QualityAnalysisWindow, ...]]:
    if not isinstance(report, ChannelQualityReport):
        raise TypeError("quality_report must be a ChannelQualityReport")
    sample_rate = _sample_index("quality_report.sample_rate", report.sample_rate)
    timeline = _sample_index(
        "quality_report.timeline_sample_count",
        report.timeline_sample_count,
    )
    reference = _sample_index(
        "quality_report.reference_index", report.reference_index
    )
    if sample_rate <= 0:
        raise ValueError("quality_report.sample_rate must be positive")
    if timeline < 0:
        raise ValueError(
            "quality_report.timeline_sample_count must be at least 0"
        )

    speech = report.speech_report
    if (
        speech.sample_rate != sample_rate
        or speech.timeline_sample_count != timeline
        or speech.reference_index != reference
    ):
        raise ValueError(
            "quality and speech reports must describe the same timeline"
        )
    input_indices = tuple(item.input_index for item in speech.inputs)
    if input_indices != tuple(range(len(input_indices))):
        raise ValueError("speech report input indexes must be contiguous from 0")
    if len(input_indices) < 2:
        raise ValueError("at least two channel inputs are required")
    if reference not in input_indices:
        raise ValueError("reference_index is outside the input range")
    if tuple(item.input_index for item in report.inputs) != input_indices:
        raise ValueError("quality summary input indexes do not match speech inputs")

    windows = tuple(report.windows)
    previous_start = -1
    for window in windows:
        if not isinstance(window, QualityAnalysisWindow):
            raise TypeError(
                "quality_report.windows must contain QualityAnalysisWindow values"
            )
        if (
            window.start_sample < 0
            or window.end_sample <= window.start_sample
            or window.end_sample > timeline
        ):
            raise ValueError("quality window is outside the reference timeline")
        if window.start_sample <= previous_start:
            raise ValueError("quality windows must have strictly increasing starts")
        previous_start = window.start_sample
        if tuple(item.input_index for item in window.inputs) != input_indices:
            raise ValueError("quality window input indexes are inconsistent")
        for item in window.inputs:
            if item.score is not None:
                _unit_interval("quality score", item.score)
            _unit_interval(
                "quality measurement_confidence",
                item.measurement_confidence,
            )
            _unit_interval("quality valid_ratio", item.valid_ratio)
    if timeline == 0 and windows:
        raise ValueError("an empty timeline must not contain quality windows")
    return timeline, input_indices, windows


def _normalize_overrides(
    overrides: Sequence[ChannelSelectionOverride],
    *,
    timeline_sample_count: int,
    input_count: int,
) -> tuple[ChannelSelectionOverride, ...]:
    values = tuple(overrides)
    if any(not isinstance(item, ChannelSelectionOverride) for item in values):
        raise TypeError(
            "overrides must contain ChannelSelectionOverride values"
        )
    ordered = tuple(
        sorted(values, key=lambda item: (item.start_sample, item.end_sample))
    )
    previous_end = 0
    for index, item in enumerate(ordered):
        if item.end_sample > timeline_sample_count:
            raise ValueError("override is outside the reference timeline")
        if item.input_index >= input_count:
            raise ValueError("override input_index is outside the input range")
        if index and item.start_sample < previous_end:
            raise ValueError("channel selection overrides must not overlap")
        previous_end = item.end_sample
    return ordered


def _decision_boundaries(
    report: ChannelQualityReport,
    windows: Sequence[QualityAnalysisWindow],
) -> tuple[int, ...]:
    timeline = report.timeline_sample_count
    boundaries = {0, timeline}
    boundaries.update(window.start_sample for window in windows)
    for evidence in report.speech_report.inputs:
        for region in (*evidence.valid_regions, *evidence.speech_regions):
            boundaries.add(region.start_sample)
            boundaries.add(region.end_sample)
    for candidate in report.speech_report.candidates:
        boundaries.add(candidate.start_sample)
        boundaries.add(candidate.end_sample)
    return tuple(sorted(value for value in boundaries if 0 <= value <= timeline))


def _window_for_start(
    windows: Sequence[QualityAnalysisWindow],
    start_sample: int,
) -> QualityAnalysisWindow | None:
    selected: QualityAnalysisWindow | None = None
    for window in windows:
        if window.start_sample > start_sample:
            break
        selected = window
    return selected if selected is not None else (windows[0] if windows else None)


def _candidate_for_interval(
    report: ChannelQualityReport,
    start_sample: int,
    end_sample: int,
) -> SpeechCandidateRegion | None:
    for candidate in report.speech_report.candidates:
        if (
            candidate.start_sample <= start_sample
            and candidate.end_sample >= end_sample
        ):
            return candidate
    return None


def _valid_counts(
    report: ChannelQualityReport,
    start_sample: int,
    end_sample: int,
) -> dict[int, int]:
    return {
        item.input_index: _overlap_samples(
            item.valid_regions,
            start_sample,
            end_sample,
        )
        for item in report.speech_report.inputs
    }


def _score_map(
    window: QualityAnalysisWindow | None,
) -> dict[int, InputQualityScore]:
    return (
        {item.input_index: item for item in window.inputs}
        if window is not None
        else {}
    )


def _rank_tie(
    input_index: int,
    *,
    current_index: int | None,
    reference_index: int,
) -> tuple[int, int, int]:
    return (
        int(input_index == current_index),
        int(input_index == reference_index),
        -input_index,
    )


def _propose_channel(
    input_indices: Sequence[int],
    eligible_indices: Sequence[int],
    scores: dict[int, InputQualityScore],
    valid_counts: dict[int, int],
    *,
    current_index: int | None,
    reference_index: int,
) -> _Proposal:
    eligible = tuple(dict.fromkeys(eligible_indices)) or tuple(input_indices)
    scoreable = tuple(
        index
        for index in eligible
        if valid_counts.get(index, 0) > 0
        and index in scores
        and scores[index].score is not None
    )
    if scoreable:
        selected = max(
            scoreable,
            key=lambda index: (
                float(scores[index].score),
                scores[index].measurement_confidence,
                valid_counts.get(index, 0),
                *_rank_tie(
                    index,
                    current_index=current_index,
                    reference_index=reference_index,
                ),
            ),
        )
        return _Proposal(selected, scores[selected].score, False)

    if not any(valid_counts.get(index, 0) > 0 for index in eligible):
        selected = max(
            eligible,
            key=lambda index: _rank_tie(
                index,
                current_index=current_index,
                reference_index=reference_index,
            ),
        )
        return _Proposal(selected, None, True)

    selected = max(
        eligible,
        key=lambda index: (
            valid_counts.get(index, 0),
            scores[index].valid_sample_count if index in scores else 0,
            scores[index].valid_ratio if index in scores else 0.0,
            scores[index].measurement_confidence if index in scores else 0.0,
            *_rank_tie(
                index,
                current_index=current_index,
                reference_index=reference_index,
            ),
        ),
    )
    return _Proposal(selected, None, True)


def _selection_confidence(
    selected_index: int,
    scores: dict[int, InputQualityScore],
    valid_counts: dict[int, int],
    config: ChannelSelectionConfig,
    *,
    held_against_index: int | None = None,
) -> float:
    selected = scores.get(selected_index)
    if (
        selected is None
        or selected.score is None
        or valid_counts.get(selected_index, 0) <= 0
    ):
        return 0.0
    alternatives = [
        float(item.score)
        for index, item in scores.items()
        if index != selected_index
        and item.score is not None
        and valid_counts.get(index, 0) > 0
    ]
    if not alternatives:
        return float(selected.measurement_confidence)
    best_other = max(alternatives)
    margin = float(selected.score) - best_other
    if held_against_index is not None and config.hysteresis_margin > 0.0:
        margin_factor = max(
            0.0,
            min(
                1.0,
                (config.hysteresis_margin + margin)
                / config.hysteresis_margin,
            ),
        )
    elif config.hysteresis_margin > 0.0:
        margin_factor = max(
            0.0,
            min(1.0, max(0.0, margin) / config.hysteresis_margin),
        )
    else:
        margin_factor = 1.0 if margin >= 0.0 else 0.0
    return float(selected.measurement_confidence * margin_factor)


def _quality_warnings(
    region: SpeechRegion,
    input_indices: Sequence[int],
    scores: dict[int, InputQualityScore],
    valid_counts: dict[int, int],
    config: ChannelSelectionConfig,
    *,
    has_activity_candidate: bool,
) -> list[ChannelSelectionWarning]:
    if not any(valid_counts.get(index, 0) > 0 for index in input_indices):
        return [
            ChannelSelectionWarning(
                "no_valid_channel",
                "No input contains valid audio in this interval; the current "
                "or reference channel is retained so the timeline is not "
                "shortened.",
                region,
            )
        ]
    scoreable = [
        float(scores[index].score)
        for index in input_indices
        if index in scores
        and scores[index].score is not None
        and valid_counts.get(index, 0) > 0
    ]
    if not scoreable:
        return [
            ChannelSelectionWarning(
                "all_channels_unscoreable",
                "No channel has a usable quality score in this interval; the "
                "most valid original channel is retained.",
                region,
            )
        ]
    if (
        has_activity_candidate
        and max(scoreable) <= config.low_quality_threshold
    ):
        return [
            ChannelSelectionWarning(
                "all_channels_low_quality",
                "All available channels are below the configured quality "
                "threshold; the best original channel is retained.",
                region,
            )
        ]
    return []


def _automatic_decisions(
    report: ChannelQualityReport,
    input_indices: Sequence[int],
    windows: Sequence[QualityAnalysisWindow],
    boundaries: Sequence[int],
    config: ChannelSelectionConfig,
) -> tuple[list[_Decision], list[ChannelSelectionWarning]]:
    hold_samples = _seconds_to_samples(
        "minimum_hold_seconds",
        config.minimum_hold_seconds,
        report.sample_rate,
    )
    current_index: int | None = None
    last_switch_sample = 0
    decisions: list[_Decision] = []
    warnings: list[ChannelSelectionWarning] = []

    for start_sample, end_sample in zip(boundaries, boundaries[1:]):
        if end_sample <= start_sample:
            continue
        region = SpeechRegion(start_sample, end_sample)
        window = _window_for_start(windows, start_sample)
        scores = _score_map(window)
        valid_counts = _valid_counts(report, start_sample, end_sample)
        candidate = _candidate_for_interval(
            report, start_sample, end_sample
        )
        supporting = (
            candidate.supporting_input_indices
            if candidate is not None
            else ()
        )
        eligible = supporting if supporting else tuple(input_indices)
        proposal = _propose_channel(
            input_indices,
            eligible,
            scores,
            valid_counts,
            current_index=current_index,
            reference_index=report.reference_index,
        )
        warnings.extend(
            _quality_warnings(
                region,
                input_indices,
                scores,
                valid_counts,
                config,
                has_activity_candidate=candidate is not None,
            )
        )

        selected = proposal.input_index
        reason = (
            "fallback_most_valid" if proposal.used_fallback else "best_quality"
        )
        if len(supporting) == 1:
            reason = "single_input_speech"
        locked_start = False
        held_against: int | None = None

        if current_index is None:
            current_index = selected
            last_switch_sample = start_sample
        elif selected != current_index:
            current_item = scores.get(current_index)
            proposed_item = scores.get(selected)
            current_valid = valid_counts.get(current_index, 0) > 0
            current_scoreable = (
                current_item is not None and current_item.score is not None
            )
            proposed_scoreable = (
                proposed_item is not None and proposed_item.score is not None
            )

            if supporting and current_index not in supporting:
                reason = (
                    "single_input_speech"
                    if len(supporting) == 1
                    else "speech_preservation"
                )
                locked_start = True
            elif not current_valid:
                reason = "current_input_unavailable"
                locked_start = True
            elif not current_scoreable and proposed_scoreable:
                reason = "current_input_unscoreable"
                locked_start = True
            elif start_sample - last_switch_sample < hold_samples:
                selected = current_index
                reason = "minimum_hold"
                held_against = proposal.input_index
            elif current_scoreable and proposed_scoreable:
                assert current_item is not None and proposed_item is not None
                assert current_item.score is not None
                assert proposed_item.score is not None
                if (
                    float(proposed_item.score) - float(current_item.score)
                    <= config.hysteresis_margin
                ):
                    selected = current_index
                    reason = "hysteresis_hold"
                    held_against = proposal.input_index
                else:
                    reason = "better_quality"
            elif proposal.used_fallback:
                # With no usable scores, validity decides, but the hold still
                # prevents small per-window validity changes from flapping.
                reason = "fallback_most_valid"

            if selected != current_index:
                current_index = selected
                last_switch_sample = start_sample
        elif supporting and len(supporting) == 1:
            reason = "single_input_speech"
        elif proposal.used_fallback:
            reason = "fallback_most_valid"

        selected_item = scores.get(selected)
        score = (
            selected_item.score
            if selected_item is not None
            and valid_counts.get(selected, 0) > 0
            else None
        )
        confidence = _selection_confidence(
            selected,
            scores,
            valid_counts,
            config,
            held_against_index=held_against,
        )
        decisions.append(
            _Decision(
                start_sample=start_sample,
                end_sample=end_sample,
                selected_input_index=selected,
                automatic_input_index=selected,
                quality_score=score,
                confidence=confidence,
                reason_codes=(reason,),
                locked_start=locked_start,
                quality_score_coverage_ratio=(
                    1.0 if score is not None else 0.0
                ),
            )
        )
    return decisions, warnings


def _weighted_optional(
    decisions: Sequence[_Decision],
    attribute: str,
) -> float | None:
    values = [
        (getattr(item, attribute), item.sample_count)
        for item in decisions
        if getattr(item, attribute) is not None
    ]
    denominator = sum(weight for _, weight in values)
    if denominator <= 0:
        return None
    return float(sum(float(value) * weight for value, weight in values) / denominator)


def _coalesce_decisions(
    decisions: Sequence[_Decision],
    *,
    require_same_automatic: bool = True,
) -> list[_Decision]:
    result: list[_Decision] = []
    groups: list[list[_Decision]] = []
    for decision in decisions:
        if (
            groups
            and groups[-1][-1].end_sample == decision.start_sample
            and groups[-1][-1].selected_input_index
            == decision.selected_input_index
            and groups[-1][-1].selection_source == decision.selection_source
            and (
                decision.selection_source != "manual_override"
                or (
                    groups[-1][-1].reason_codes == decision.reason_codes
                    and groups[-1][-1].override_reason
                    == decision.override_reason
                )
            )
            and (
                not require_same_automatic
                or groups[-1][-1].automatic_input_index
                == decision.automatic_input_index
            )
        ):
            groups[-1].append(decision)
        else:
            groups.append([decision])

    for group in groups:
        first = group[0]
        confidence = _weighted_optional(group, "confidence")
        coverage = _weighted_optional(
            group, "quality_score_coverage_ratio"
        )
        result.append(
            _Decision(
                start_sample=first.start_sample,
                end_sample=group[-1].end_sample,
                selected_input_index=first.selected_input_index,
                automatic_input_index=first.automatic_input_index,
                quality_score=_weighted_optional(group, "quality_score"),
                confidence=0.0 if confidence is None else confidence,
                reason_codes=_unique_reasons(
                    reason
                    for item in group
                    for reason in item.reason_codes
                ),
                selection_source=first.selection_source,
                locked_start=first.locked_start,
                quality_score_coverage_ratio=(
                    0.0 if coverage is None else coverage
                ),
                override_reason=first.override_reason,
            )
        )
    return result


def _metrics_for_interval(
    selected_input_index: int,
    start_sample: int,
    end_sample: int,
    report: ChannelQualityReport,
    windows: Sequence[QualityAnalysisWindow],
    decision_boundaries: Sequence[int],
    config: ChannelSelectionConfig,
    confidence_cells: Sequence[_Decision],
) -> tuple[float | None, float, float]:
    boundaries = sorted(
        {
            start_sample,
            end_sample,
            *(
                boundary
                for boundary in decision_boundaries
                if start_sample < boundary < end_sample
            ),
        }
    )
    weighted_score = 0.0
    score_sample_count = 0
    weighted_confidence = 0.0
    total_sample_count = end_sample - start_sample
    for part_start, part_end in zip(boundaries, boundaries[1:]):
        sample_count = part_end - part_start
        if sample_count <= 0:
            continue
        scores = (
            _score_map(_window_for_start(windows, part_start))
            if windows
            else {}
        )
        valid_counts = _valid_counts(report, part_start, part_end)
        selected = scores.get(selected_input_index)
        score = (
            selected.score
            if selected is not None
            and selected.score is not None
            and valid_counts.get(selected_input_index, 0) > 0
            else None
        )
        if score is not None:
            weighted_score += float(score) * sample_count
            score_sample_count += sample_count
        source = next(
            (
                cell
                for cell in confidence_cells
                if cell.start_sample <= part_start
                and cell.end_sample >= part_end
            ),
            None,
        )
        confidence = (
            source.confidence
            if source is not None
            and source.selected_input_index == selected_input_index
            else _selection_confidence(
                selected_input_index,
                scores,
                valid_counts,
                config,
            )
        )
        weighted_confidence += confidence * sample_count
    return (
        (
            weighted_score / score_sample_count
            if score_sample_count > 0
            else None
        ),
        (
            weighted_confidence / total_sample_count
            if total_sample_count > 0
            else 0.0
        ),
        (
            score_sample_count / total_sample_count
            if total_sample_count > 0
            else 0.0
        ),
    )


def _refresh_decision_metrics(
    decisions: Sequence[_Decision],
    report: ChannelQualityReport,
    windows: Sequence[QualityAnalysisWindow],
    decision_boundaries: Sequence[int],
    config: ChannelSelectionConfig,
    confidence_cells: Sequence[_Decision],
) -> None:
    for decision in decisions:
        score, confidence, coverage = _metrics_for_interval(
            decision.selected_input_index,
            decision.start_sample,
            decision.end_sample,
            report,
            windows,
            decision_boundaries,
            config,
            confidence_cells,
        )
        decision.quality_score = score
        decision.confidence = confidence
        decision.quality_score_coverage_ratio = coverage


def _inside_speech(regions: Sequence[SpeechRegion], boundary: int) -> bool:
    return any(
        region.start_sample < boundary < region.end_sample
        for region in regions
    )


def _boundary_is_valid(
    report: ChannelQualityReport,
    left_input: int,
    right_input: int,
    boundary: int,
) -> bool:
    if boundary <= 0 or boundary >= report.timeline_sample_count:
        return False
    left_regions = report.speech_report.input_evidence(left_input).valid_regions
    right_regions = report.speech_report.input_evidence(right_input).valid_regions
    return _contains_sample(left_regions, boundary - 1) and _contains_sample(
        right_regions, boundary
    )


def _snap_preserves_evidence(
    report: ChannelQualityReport,
    left_input: int,
    right_input: int,
    raw_boundary: int,
    snapped_boundary: int,
) -> bool:
    """Reject a snap that hands unique speech or invalid audio to its peer."""

    if snapped_boundary == raw_boundary:
        return True
    if snapped_boundary > raw_boundary:
        start_sample, end_sample = raw_boundary, snapped_boundary
        old_owner, new_owner = right_input, left_input
    else:
        start_sample, end_sample = snapped_boundary, raw_boundary
        old_owner, new_owner = left_input, right_input

    new_regions = report.speech_report.input_evidence(new_owner).valid_regions
    if _overlap_samples(new_regions, start_sample, end_sample) != (
        end_sample - start_sample
    ):
        return False
    for candidate in report.speech_report.candidates:
        overlaps = (
            candidate.start_sample < end_sample
            and candidate.end_sample > start_sample
        )
        if (
            overlaps
            and old_owner in candidate.supporting_input_indices
            and new_owner not in candidate.supporting_input_indices
        ):
            return False
    return True


def _snap_switch_boundaries(
    decisions: list[_Decision],
    report: ChannelQualityReport,
    config: ChannelSelectionConfig,
) -> None:
    if len(decisions) < 2 or config.boundary_search_seconds <= 0.0:
        return
    speech_regions = report.speech_report.speech_regions
    candidates = sorted(
        {
            boundary
            for region in speech_regions
            for boundary in (region.start_sample, region.end_sample)
            if 0 < boundary < report.timeline_sample_count
        }
    )
    if not candidates:
        return
    radius = _seconds_to_samples(
        "boundary_search_seconds",
        config.boundary_search_seconds,
        report.sample_rate,
    )
    minimum_hold = _seconds_to_samples(
        "minimum_hold_seconds",
        config.minimum_hold_seconds,
        report.sample_rate,
    )

    for index in range(1, len(decisions)):
        left = decisions[index - 1]
        right = decisions[index]
        raw = right.start_sample
        if right.locked_start or not _inside_speech(speech_regions, raw):
            continue
        lower = left.start_sample + 1
        upper = right.end_sample - 1
        if minimum_hold > 0:
            lower = max(lower, left.start_sample + minimum_hold)
            has_ordinary_next_switch = (
                index + 1 < len(decisions)
                and not decisions[index + 1].locked_start
            )
            if has_ordinary_next_switch:
                upper = min(upper, right.end_sample - minimum_hold)
        nearby = [
            boundary
            for boundary in candidates
            if lower <= boundary <= upper
            and abs(boundary - raw) <= radius
            and _boundary_is_valid(
                report,
                left.selected_input_index,
                right.selected_input_index,
                boundary,
            )
            and _snap_preserves_evidence(
                report,
                left.selected_input_index,
                right.selected_input_index,
                raw,
                boundary,
            )
        ]
        if not nearby:
            continue
        snapped = min(nearby, key=lambda value: (abs(value - raw), value))
        left.end_sample = snapped
        right.start_sample = snapped


def _override_for_interval(
    overrides: Sequence[ChannelSelectionOverride],
    start_sample: int,
    end_sample: int,
) -> ChannelSelectionOverride | None:
    for item in overrides:
        if item.start_sample <= start_sample and item.end_sample >= end_sample:
            return item
    return None


def _decision_for_interval(
    decisions: Sequence[_Decision],
    start_sample: int,
    end_sample: int,
) -> _Decision:
    for decision in decisions:
        if (
            decision.start_sample <= start_sample
            and decision.end_sample >= end_sample
        ):
            return decision
    raise RuntimeError("automatic channel plan does not cover the timeline")


def _apply_overrides(
    automatic: Sequence[_Decision],
    report: ChannelQualityReport,
    windows: Sequence[QualityAnalysisWindow],
    decision_boundaries: Sequence[int],
    config: ChannelSelectionConfig,
    confidence_cells: Sequence[_Decision],
    overrides: Sequence[ChannelSelectionOverride],
) -> tuple[list[_Decision], list[ChannelSelectionWarning]]:
    if not overrides:
        return list(automatic), []
    boundaries = {
        boundary
        for decision in automatic
        for boundary in (decision.start_sample, decision.end_sample)
    }
    boundaries.update(decision_boundaries)
    boundaries.update(item.start_sample for item in overrides)
    boundaries.update(item.end_sample for item in overrides)
    ordered = sorted(boundaries)
    result: list[_Decision] = []
    warnings: list[ChannelSelectionWarning] = []
    for start_sample, end_sample in zip(ordered, ordered[1:]):
        if end_sample <= start_sample:
            continue
        auto = _decision_for_interval(
            automatic, start_sample, end_sample
        )
        override = _override_for_interval(
            overrides, start_sample, end_sample
        )
        if override is None:
            score, confidence, coverage = _metrics_for_interval(
                auto.selected_input_index,
                start_sample,
                end_sample,
                report,
                windows,
                decision_boundaries,
                config,
                confidence_cells,
            )
            result.append(
                _Decision(
                    start_sample=start_sample,
                    end_sample=end_sample,
                    selected_input_index=auto.selected_input_index,
                    automatic_input_index=auto.automatic_input_index,
                    quality_score=score,
                    confidence=confidence,
                    reason_codes=auto.reason_codes,
                    selection_source=auto.selection_source,
                    locked_start=auto.locked_start,
                    quality_score_coverage_ratio=coverage,
                    override_reason=auto.override_reason,
                )
            )
            continue

        scores = (
            _score_map(_window_for_start(windows, start_sample))
            if windows
            else {}
        )
        item = scores.get(override.input_index)
        valid = _valid_counts(report, start_sample, end_sample).get(
            override.input_index, 0
        )
        if valid <= 0:
            warnings.append(
                ChannelSelectionWarning(
                    "manual_override_unavailable",
                    "The manually selected channel has no valid audio in this "
                    "interval; the override is retained for explicit review.",
                    SpeechRegion(start_sample, end_sample),
                    override.input_index,
                )
            )
        result.append(
            _Decision(
                start_sample=start_sample,
                end_sample=end_sample,
                selected_input_index=override.input_index,
                automatic_input_index=auto.selected_input_index,
                quality_score=(
                    item.score if item is not None and valid > 0 else None
                ),
                confidence=1.0 if valid > 0 else 0.0,
                reason_codes=("manual_override",),
                selection_source="manual_override",
                locked_start=True,
                quality_score_coverage_ratio=(
                    1.0
                    if item is not None
                    and item.score is not None
                    and valid > 0
                    else 0.0
                ),
                override_reason=override.reason,
            )
        )
    return _coalesce_decisions(result), warnings


def _merge_warnings(
    warnings: Sequence[ChannelSelectionWarning],
) -> tuple[ChannelSelectionWarning, ...]:
    ordered = sorted(
        warnings,
        key=lambda item: (
            item.code,
            item.message,
            -1 if item.input_index is None else item.input_index,
            item.region.start_sample if item.region is not None else -1,
            item.region.end_sample if item.region is not None else -1,
        ),
    )
    merged: list[ChannelSelectionWarning] = []
    for warning in ordered:
        if not merged:
            merged.append(warning)
            continue
        previous = merged[-1]
        compatible = (
            previous.code == warning.code
            and previous.message == warning.message
            and previous.input_index == warning.input_index
            and previous.region is not None
            and warning.region is not None
            and warning.region.start_sample <= previous.region.end_sample
        )
        if compatible:
            assert previous.region is not None and warning.region is not None
            merged[-1] = ChannelSelectionWarning(
                previous.code,
                previous.message,
                SpeechRegion(
                    previous.region.start_sample,
                    max(previous.region.end_sample, warning.region.end_sample),
                ),
                previous.input_index,
            )
        else:
            merged.append(warning)
    return tuple(
        sorted(
            merged,
            key=lambda item: (
                item.region.start_sample if item.region is not None else -1,
                item.region.end_sample if item.region is not None else -1,
                item.code,
                -1 if item.input_index is None else item.input_index,
            ),
        )
    )


def _public_segments(
    decisions: Sequence[_Decision],
) -> tuple[ChannelSelectionSegment, ...]:
    return tuple(
        ChannelSelectionSegment(
            region=SpeechRegion(item.start_sample, item.end_sample),
            selected_input_index=item.selected_input_index,
            automatic_input_index=item.automatic_input_index,
            quality_score=item.quality_score,
            confidence=item.confidence,
            reason_codes=item.reason_codes,
            selection_source=item.selection_source,
            quality_score_coverage_ratio=(
                item.quality_score_coverage_ratio
            ),
            override_reason=item.override_reason,
        )
        for item in decisions
    )


def _validate_partition(
    segments: Sequence[ChannelSelectionSegment],
    timeline_sample_count: int,
) -> None:
    if timeline_sample_count == 0:
        if segments:
            raise RuntimeError("empty timeline unexpectedly contains selections")
        return
    if not segments:
        raise RuntimeError("channel selection produced no timeline segments")
    if segments[0].start_sample != 0:
        raise RuntimeError("channel selection does not start at sample zero")
    if segments[-1].end_sample != timeline_sample_count:
        raise RuntimeError("channel selection does not reach the timeline end")
    for previous, current in zip(segments, segments[1:]):
        if previous.end_sample != current.start_sample:
            raise RuntimeError("channel selection contains a gap or overlap")


def select_channels(
    quality_report: ChannelQualityReport,
    *,
    config: ChannelSelectionConfig | None = None,
    overrides: Sequence[ChannelSelectionOverride] = (),
) -> ChannelSelectionReport:
    """Select one channel for every sample on the quality report timeline.

    Ordinary score improvements must clear both the hysteresis margin and the
    configured minimum hold.  Missing/unscoreable current audio and speech
    supported by another input bypass those stability guards so speech is not
    lost.  User overrides are applied after the automatic plan and therefore
    do not make later automatic choices depend on a temporary manual choice.
    """

    timeline, input_indices, windows = _validate_report(quality_report)
    settings = config if config is not None else ChannelSelectionConfig()
    if not isinstance(settings, ChannelSelectionConfig):
        raise TypeError("config must be a ChannelSelectionConfig")
    _seconds_to_samples(
        "minimum_hold_seconds",
        settings.minimum_hold_seconds,
        quality_report.sample_rate,
    )
    _seconds_to_samples(
        "boundary_search_seconds",
        settings.boundary_search_seconds,
        quality_report.sample_rate,
    )
    manual = _normalize_overrides(
        overrides,
        timeline_sample_count=timeline,
        input_count=len(input_indices),
    )

    if timeline == 0:
        report = ChannelSelectionReport(
            sample_rate=quality_report.sample_rate,
            timeline_sample_count=0,
            reference_index=quality_report.reference_index,
            config=settings,
            segments=(),
            warnings=(),
            quality_report=quality_report,
        )
        _validate_partition(report.segments, 0)
        return report

    warnings: list[ChannelSelectionWarning] = []
    boundaries = _decision_boundaries(quality_report, windows)
    automatic_cells, automatic_warnings = _automatic_decisions(
        quality_report,
        input_indices,
        windows,
        boundaries,
        settings,
    )
    warnings.extend(automatic_warnings)
    automatic = _coalesce_decisions(
        automatic_cells,
        require_same_automatic=False,
    )
    _snap_switch_boundaries(automatic, quality_report, settings)
    _refresh_decision_metrics(
        automatic,
        quality_report,
        windows,
        boundaries,
        settings,
        automatic_cells,
    )
    if not windows:
        warnings.append(
            ChannelSelectionWarning(
                "no_quality_windows",
                "No quality windows are available; exact input validity is "
                "used to retain the most valid original channel.",
                SpeechRegion(0, timeline),
            )
        )

    applied, override_warnings = _apply_overrides(
        automatic,
        quality_report,
        windows,
        boundaries,
        settings,
        automatic_cells,
        manual,
    )
    warnings.extend(override_warnings)
    final_decisions = _coalesce_decisions(applied)
    segments = _public_segments(final_decisions)
    _validate_partition(segments, timeline)
    return ChannelSelectionReport(
        sample_rate=quality_report.sample_rate,
        timeline_sample_count=timeline,
        reference_index=quality_report.reference_index,
        config=settings,
        segments=segments,
        warnings=_merge_warnings(warnings),
        quality_report=quality_report,
    )


# Descriptive aliases for callers that discover the operation from the ticket
# wording.  ``select_channels`` remains the canonical public spelling.
select_channel_segments = select_channels
select_optimal_channels = select_channels


__all__ = [
    "ChannelSelectionConfig",
    "ChannelSelectionOverride",
    "ChannelSelectionReport",
    "ChannelSelectionSegment",
    "ChannelSelectionWarning",
    "ManualChannelOverride",
    "select_channel_segments",
    "select_channels",
    "select_optimal_channels",
]
