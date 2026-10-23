from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

import numpy as np


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import multimic
from multimic.quality import (
    ChannelQualityReport,
    InputQualityScore,
    InputQualitySummary,
    InputSpeechEvidence,
    QualityAnalysisWindow,
    QualityScoringConfig,
    SpeechActivityReport,
    SpeechAnalysisConfig,
    SpeechCandidateRegion,
    analyze_aligned_quality,
)
from multimic.pipeline import AnalysisSignal
from multimic.select import (
    ChannelSelectionConfig,
    ChannelSelectionOverride,
    select_channels,
)
from vad import SpeechRegion, merge_speech_regions


SAMPLE_RATE = 1_000


def selection_analysis_signal(samples: np.ndarray) -> AnalysisSignal:
    values = np.asarray(samples, dtype=np.float32)
    return AnalysisSignal(
        samples=values,
        valid_mask=np.ones(values.size, dtype=np.bool_),
        sample_rate=SAMPLE_RATE,
    )


def selection_add_tone(
    samples: np.ndarray,
    start: int,
    end: int,
    *,
    frequency: float = 120.0,
    amplitude: float = 0.2,
) -> None:
    time = np.arange(end - start, dtype=np.float64) / SAMPLE_RATE
    alternating_level = np.where(
        (np.arange(end - start) // max(1, SAMPLE_RATE // 10)) % 2 == 0,
        1.0,
        0.25,
    )
    samples[start:end] = (
        amplitude
        * alternating_level
        * np.sin(2.0 * np.pi * frequency * time)
    )


def selection_speech_config() -> SpeechAnalysisConfig:
    return SpeechAnalysisConfig(
        window_seconds=1.0,
        hop_seconds=1.0,
        threshold_db=-40.0,
        min_speech_duration=0.2,
        min_silence_duration=0.1,
        padding_duration=0.0,
    )


def quality_score(
    input_index: int,
    score: float | None,
    *,
    valid_sample_count: int = 3_000,
    measurement_confidence: float = 1.0,
) -> InputQualityScore:
    valid_ratio = 1.0 if valid_sample_count else 0.0
    return InputQualityScore(
        input_index=input_index,
        score=score,
        measurement_confidence=measurement_confidence,
        valid_sample_count=valid_sample_count,
        valid_ratio=valid_ratio,
        rms_dbfs=-20.0 if valid_sample_count else None,
        expected_speech_sample_count=0,
        speech_sample_count=0,
        speech_presence_score=1.0 if score is not None else 0.0,
        estimated_snr_db=20.0 if score is not None else None,
        snr_score=score,
        snr_estimation_method="test" if score is not None else None,
        snr_confidence=measurement_confidence if score is not None else 0.0,
        clipped_sample_count=0,
        clipping_ratio=0.0,
        clipping_penalty=0.0,
        invalid_sample_count=0,
        invalid_penalty=0.0,
        internal_gap_count=0,
        internal_gap_sample_count=0,
        gap_penalty=0.0,
        noise_suspected_sample_count=0,
        noise_suspected_ratio=0.0,
        noise_penalty=0.0,
        base_score=score,
        penalty_score=0.0 if score is not None else None,
        diagnostic_codes=(),
    )


def make_report(
    timeline_sample_count: int,
    windows: list[
        tuple[
            int,
            int,
            tuple[float | None | tuple[float | None, int], ...],
        ]
    ],
    *,
    reference_index: int = 0,
    valid_regions: tuple[tuple[SpeechRegion, ...], ...] | None = None,
    candidates: tuple[tuple[int, int, tuple[int, ...]], ...] = (),
) -> ChannelQualityReport:
    input_count = len(windows[0][2]) if windows else len(valid_regions or ())
    if valid_regions is None:
        valid_regions = tuple(
            (SpeechRegion(0, timeline_sample_count),)
            for _ in range(input_count)
        )

    candidate_values = tuple(
        SpeechCandidateRegion(
            region=SpeechRegion(start, end),
            activity_input_indices=supporting,
            supporting_input_indices=supporting,
            valid_input_indices=tuple(
                index
                for index, regions in enumerate(valid_regions)
                if any(
                    region.start_sample < end
                    and region.end_sample > start
                    for region in regions
                )
            ),
            status=(
                "single_input"
                if len(supporting) == 1
                else "corroborated"
                if supporting
                else "noise_risk"
            ),
            diagnostic_codes=(),
        )
        for start, end, supporting in candidates
    )
    evidence: list[InputSpeechEvidence] = []
    for input_index in range(input_count):
        speech_regions = tuple(
            item.region
            for item in candidate_values
            if input_index in item.supporting_input_indices
        )
        speech_count = sum(region.sample_count for region in speech_regions)
        valid_count = sum(
            region.sample_count for region in valid_regions[input_index]
        )
        evidence.append(
            InputSpeechEvidence(
                input_index=input_index,
                valid_regions=valid_regions[input_index],
                activity_regions=speech_regions,
                speech_regions=speech_regions,
                noise_suspected_regions=(),
                region_diagnostics=(),
                valid_sample_count=valid_count,
                activity_sample_count=speech_count,
                speech_sample_count=speech_count,
                activity_ratio=(speech_count / valid_count if valid_count else None),
                speech_ratio=(speech_count / valid_count if valid_count else None),
                diagnostic_codes=(),
            )
        )

    combined_speech = tuple(
        merge_speech_regions(
            item.region
            for item in candidate_values
            if item.supporting_input_indices
        )
    )
    speech_report = SpeechActivityReport(
        sample_rate=SAMPLE_RATE,
        timeline_sample_count=timeline_sample_count,
        reference_index=reference_index,
        config=SpeechAnalysisConfig(),
        inputs=tuple(evidence),
        candidates=candidate_values,
        speech_regions=combined_speech,
        uncertain_regions=(),
        windows=(),
        warnings=(),
    )

    quality_windows: list[QualityAnalysisWindow] = []
    for start, end, raw_scores in windows:
        scores: list[InputQualityScore] = []
        for input_index, raw in enumerate(raw_scores):
            if isinstance(raw, tuple):
                value, valid_count = raw
            else:
                value = raw
                valid_count = end - start
            scores.append(
                quality_score(
                    input_index,
                    value,
                    valid_sample_count=valid_count,
                )
            )
        quality_windows.append(
            QualityAnalysisWindow(
                region=SpeechRegion(start, end),
                inputs=tuple(scores),
                has_speech_candidate=any(
                    item.start_sample < end and item.end_sample > start
                    for item in candidate_values
                ),
                has_likely_speech=any(
                    item.supporting_input_indices
                    and item.start_sample < end
                    and item.end_sample > start
                    for item in candidate_values
                ),
            )
        )

    summaries = tuple(
        InputQualitySummary(
            input_index=index,
            score=0.5,
            measurement_confidence=1.0,
            score_coverage_ratio=1.0,
            scored_window_count=len(quality_windows),
            unscoreable_window_count=0,
            diagnostic_codes=(),
        )
        for index in range(input_count)
    )
    return ChannelQualityReport(
        sample_rate=SAMPLE_RATE,
        timeline_sample_count=timeline_sample_count,
        reference_index=reference_index,
        speech_report=speech_report,
        config=QualityScoringConfig(),
        inputs=summaries,
        windows=tuple(quality_windows),
    )


def assert_complete_partition(
    testcase: unittest.TestCase,
    report,
) -> None:
    testcase.assertTrue(report.segments)
    testcase.assertEqual(report.segments[0].start_sample, 0)
    testcase.assertEqual(
        report.segments[-1].end_sample,
        report.timeline_sample_count,
    )
    testcase.assertEqual(
        sum(segment.sample_count for segment in report.segments),
        report.timeline_sample_count,
    )
    for previous, current in zip(report.segments, report.segments[1:]):
        testcase.assertEqual(previous.end_sample, current.start_sample)


class ChannelSelectionTests(unittest.TestCase):
    def test_public_package_exports_selection_api(self) -> None:
        self.assertIs(multimic.select_channels, select_channels)
        self.assertIs(
            multimic.ChannelSelectionOverride,
            ChannelSelectionOverride,
        )
        self.assertIn("ChannelSelectionReport", multimic.__all__)

    def test_real_quality_pipeline_avoids_local_broadband_noise(self) -> None:
        sample_count = 5_000
        clean = np.zeros(sample_count, dtype=np.float32)
        selection_add_tone(clean, 250, 4_750, amplitude=0.24)
        noisy = clean.copy()
        noisy[2_000:3_000] += np.random.default_rng(808).normal(
            0.0, 0.10, 1_000
        ).astype(np.float32)
        quality = analyze_aligned_quality(
            (
                selection_analysis_signal(noisy),
                selection_analysis_signal(clean),
            ),
            speech_config=selection_speech_config(),
        )

        selected = select_channels(
            quality,
            config=ChannelSelectionConfig(
                minimum_hold_seconds=0.0,
                boundary_search_seconds=0.0,
            ),
        )

        noise_owner = next(
            segment.selected_input_index
            for segment in selected.segments
            if segment.start_sample <= 2_500 < segment.end_sample
        )
        self.assertEqual(noise_owner, 1)
        assert_complete_partition(self, selected)

    def test_real_vad_pipeline_preserves_single_input_speech(self) -> None:
        sample_count = 5_000
        first = np.zeros(sample_count, dtype=np.float32)
        second = np.zeros(sample_count, dtype=np.float32)
        selection_add_tone(first, 100, 800, amplitude=0.15)
        selection_add_tone(second, 100, 800, amplitude=0.24)
        selection_add_tone(
            first,
            1_100,
            1_500,
            amplitude=0.2,
            frequency=180.0,
        )
        quality = analyze_aligned_quality(
            (
                selection_analysis_signal(first),
                selection_analysis_signal(second),
            ),
            speech_config=selection_speech_config(),
        )
        unique = next(
            candidate
            for candidate in quality.speech_report.candidates
            if candidate.status == "single_input"
        )

        selected = select_channels(quality)

        selected_unique_samples = sum(
            max(
                0,
                min(segment.end_sample, unique.end_sample)
                - max(segment.start_sample, unique.start_sample),
            )
            for segment in selected.segments
            if segment.selected_input_index == 0
        )
        self.assertEqual(selected_unique_samples, unique.sample_count)
        self.assertEqual(unique.supporting_input_indices, (0,))
        assert_complete_partition(self, selected)

    def test_overlapping_quality_windows_produce_complete_partition(self) -> None:
        report = make_report(
            6_500,
            [
                (0, 3_000, (0.8, 0.4)),
                (1_000, 4_000, (0.8, 0.4)),
                (2_000, 5_000, (0.8, 0.4)),
                (3_000, 6_000, (0.8, 0.4)),
                (4_000, 6_500, (0.8, 0.4)),
            ],
        )

        selected = select_channels(report)

        assert_complete_partition(self, selected)
        self.assertEqual(len(selected.segments), 1)
        self.assertEqual(selected.segments[0].selected_input_index, 0)

    def test_hysteresis_rejects_small_alternating_score_changes(self) -> None:
        report = make_report(
            5_000,
            [
                (0, 2_000, (0.62, 0.60)),
                (1_000, 3_000, (0.60, 0.63)),
                (2_000, 4_000, (0.64, 0.60)),
                (3_000, 5_000, (0.60, 0.64)),
            ],
        )

        selected = select_channels(
            report,
            config=ChannelSelectionConfig(minimum_hold_seconds=0.0),
        )

        self.assertEqual(selected.switch_count, 0)
        self.assertEqual(selected.segments[0].selected_input_index, 0)
        self.assertIn("hysteresis_hold", selected.segments[0].reason_codes)

    def test_metric_refresh_preserves_per_cell_hold_confidence(self) -> None:
        report = make_report(
            2_000,
            [
                (0, 1_000, (0.52, 0.50)),
                (1_000, 2_000, (0.50, 0.53)),
            ],
        )

        selected = select_channels(
            report,
            config=ChannelSelectionConfig(minimum_hold_seconds=0.0),
        )

        self.assertEqual(len(selected.segments), 1)
        self.assertIn("hysteresis_hold", selected.segments[0].reason_codes)
        self.assertAlmostEqual(selected.segments[0].confidence, 0.4)

    def test_minimum_hold_delays_clear_quality_switch(self) -> None:
        report = make_report(
            6_000,
            [
                (0, 3_000, (0.9, 0.3)),
                (1_000, 4_000, (0.3, 0.9)),
                (2_000, 5_000, (0.3, 0.9)),
                (3_000, 6_000, (0.3, 0.9)),
            ],
        )

        selected = select_channels(report)

        self.assertEqual(selected.switch_count, 1)
        self.assertEqual(selected.segments[0].region, SpeechRegion(0, 3_000))
        self.assertEqual(selected.segments[1].region, SpeechRegion(3_000, 6_000))

    def test_unscoreable_current_channel_bypasses_hold(self) -> None:
        report = make_report(
            4_000,
            [
                (0, 2_000, (0.8, 0.4)),
                (1_000, 3_000, (None, 0.7)),
                (2_000, 4_000, (None, 0.7)),
            ],
        )

        selected = select_channels(
            report,
            config=ChannelSelectionConfig(minimum_hold_seconds=10.0),
        )

        self.assertEqual(selected.segments[0].region, SpeechRegion(0, 1_000))
        self.assertEqual(selected.segments[1].selected_input_index, 1)
        self.assertIn(
            "current_input_unscoreable",
            selected.segments[1].reason_codes,
        )

    def test_single_input_speech_bypasses_hold_and_preserves_interval(self) -> None:
        report = make_report(
            5_000,
            [
                (0, 3_000, (0.2, 0.9)),
                (1_000, 4_000, (0.2, 0.9)),
                (2_000, 5_000, (0.2, 0.9)),
            ],
            candidates=((1_000, 1_400, (0,)),),
        )

        selected = select_channels(report)

        containing = next(
            segment
            for segment in selected.segments
            if segment.start_sample <= 1_000 and segment.end_sample >= 1_400
        )
        self.assertEqual(containing.selected_input_index, 0)
        self.assertIn("single_input_speech", containing.reason_codes)
        self.assertEqual(selected.segments[0].selected_input_index, 1)

    def test_all_unscoreable_uses_most_valid_input_and_warns(self) -> None:
        report = make_report(
            3_000,
            [
                (0, 3_000, ((None, 500), (None, 3_000))),
            ],
            valid_regions=(
                (SpeechRegion(0, 500),),
                (SpeechRegion(0, 3_000),),
            ),
        )

        selected = select_channels(report)

        self.assertEqual(selected.segments[0].selected_input_index, 1)
        self.assertEqual(selected.segments[0].confidence, 0.0)
        self.assertIn("fallback_most_valid", selected.segments[0].reason_codes)
        self.assertIn(
            "all_channels_unscoreable",
            {warning.code for warning in selected.warnings},
        )
        assert_complete_partition(self, selected)

    def test_no_valid_channel_keeps_reference_and_timeline(self) -> None:
        report = make_report(
            2_000,
            [(0, 2_000, ((None, 0), (None, 0)))],
            reference_index=1,
            valid_regions=((), ()),
        )

        selected = select_channels(report)

        self.assertEqual(selected.segments[0].selected_input_index, 1)
        self.assertIn(
            "no_valid_channel",
            {warning.code for warning in selected.warnings},
        )
        assert_complete_partition(self, selected)

    def test_automatic_score_coverage_excludes_invalid_timeline_tail(self) -> None:
        report = make_report(
            2_000,
            [(0, 2_000, (0.8, 0.7))],
            valid_regions=(
                (SpeechRegion(0, 1_000),),
                (SpeechRegion(0, 1_000),),
            ),
        )

        selected = select_channels(report)

        self.assertEqual(len(selected.segments), 1)
        self.assertEqual(selected.segments[0].quality_score, 0.8)
        self.assertEqual(
            selected.segments[0].quality_score_coverage_ratio,
            0.5,
        )
        self.assertIn(
            "no_valid_channel",
            {warning.code for warning in selected.warnings},
        )

    def test_switch_inside_speech_snaps_to_nearest_speech_boundary(self) -> None:
        report = make_report(
            5_000,
            [
                (0, 3_000, (0.9, 0.3)),
                (3_000, 5_000, (0.3, 0.9)),
            ],
            candidates=((2_500, 3_300, (0, 1)),),
        )

        selected = select_channels(
            report,
            config=ChannelSelectionConfig(
                minimum_hold_seconds=0.0,
                boundary_search_seconds=0.6,
            ),
        )

        self.assertEqual(selected.segments[0].end_sample, 3_300)
        self.assertEqual(selected.segments[1].start_sample, 3_300)
        assert_complete_partition(self, selected)

    def test_snapped_segment_metrics_match_the_moved_boundary(self) -> None:
        report = make_report(
            3_000,
            [
                (0, 1_000, (0.9, None)),
                (1_000, 3_000, (0.1, 0.9)),
            ],
            candidates=((500, 1_500, (0, 1)),),
        )

        selected = select_channels(
            report,
            config=ChannelSelectionConfig(
                minimum_hold_seconds=0.0,
                boundary_search_seconds=0.6,
            ),
        )

        right = next(
            item
            for item in selected.segments
            if item.selected_input_index == 1 and item.end_sample == 3_000
        )
        self.assertEqual(right.start_sample, 500)
        self.assertAlmostEqual(right.quality_score, 0.9)
        self.assertAlmostEqual(right.quality_score_coverage_ratio, 0.8)
        assert_complete_partition(self, selected)

    def test_boundary_snap_does_not_take_single_input_speech_away(self) -> None:
        report = make_report(
            2_000,
            [
                (0, 1_000, (0.9, 0.3)),
                (1_000, 2_000, (0.3, 0.9)),
            ],
            candidates=(
                (600, 1_050, (0, 1)),
                (1_050, 1_100, (1,)),
                (1_100, 1_200, (0, 1)),
            ),
        )

        selected = select_channels(
            report,
            config=ChannelSelectionConfig(
                minimum_hold_seconds=0.0,
                boundary_search_seconds=1.0,
            ),
        )

        speech_segment = next(
            item
            for item in selected.segments
            if item.start_sample <= 1_050 and item.end_sample >= 1_100
        )
        self.assertEqual(speech_segment.selected_input_index, 1)
        assert_complete_partition(self, selected)

    def test_manual_override_splits_plan_without_changing_auto_choice(
        self,
    ) -> None:
        report = make_report(
            4_000,
            [
                (0, 2_000, (0.9, 0.3)),
                (1_000, 3_000, (0.9, 0.3)),
                (2_000, 4_000, (0.9, 0.3)),
            ],
        )

        selected = select_channels(
            report,
            overrides=(ChannelSelectionOverride(1_500, 2_500, 1),),
        )

        self.assertEqual(
            [
                (
                    item.start_sample,
                    item.end_sample,
                    item.selected_input_index,
                    item.selection_source,
                )
                for item in selected.segments
            ],
            [
                (0, 1_500, 0, "automatic"),
                (1_500, 2_500, 1, "manual_override"),
                (2_500, 4_000, 0, "automatic"),
            ],
        )
        self.assertEqual(selected.segments[1].automatic_input_index, 0)
        self.assertEqual(selected.to_dict()["version"], 1)
        json.dumps(selected.to_dict(), allow_nan=False)

    def test_override_split_recomputes_each_automatic_piece_metrics(
        self,
    ) -> None:
        report = make_report(
            2_000,
            [
                (0, 1_000, (0.9, 0.3)),
                (1_000, 2_000, (0.6, 0.3)),
            ],
        )

        selected = select_channels(
            report,
            overrides=(ChannelSelectionOverride(1_000, 1_500, 1),),
        )

        automatic = [
            item
            for item in selected.segments
            if item.selection_source == "automatic"
        ]
        self.assertEqual(
            [(item.start_sample, item.end_sample) for item in automatic],
            [(0, 1_000), (1_500, 2_000)],
        )
        self.assertAlmostEqual(automatic[0].quality_score, 0.9)
        self.assertAlmostEqual(automatic[1].quality_score, 0.6)
        assert_complete_partition(self, selected)

    def test_override_does_not_change_the_automatic_switch_time(self) -> None:
        report = make_report(
            5_000,
            [
                (0, 2_000, (0.9, 0.3)),
                (1_000, 3_000, (0.3, 0.9)),
                (2_000, 4_000, (0.3, 0.9)),
                (3_000, 5_000, (0.3, 0.9)),
            ],
        )
        baseline = select_channels(report)

        selected = select_channels(
            report,
            overrides=(ChannelSelectionOverride(2_500, 2_600, 0),),
        )

        self.assertEqual(baseline.segments[1].start_sample, 3_000)
        first_automatic_one = next(
            item
            for item in selected.segments
            if item.selection_source == "automatic"
            and item.selected_input_index == 1
        )
        self.assertEqual(first_automatic_one.start_sample, 3_000)
        manual = next(item for item in selected.segments if item.manual_override)
        self.assertEqual(manual.automatic_input_index, 0)

    def test_unavailable_manual_override_warns_and_records_score_coverage(
        self,
    ) -> None:
        report = make_report(
            3_000,
            [
                (0, 1_000, (0.9, 0.7)),
                (1_000, 2_000, (0.9, 0.7)),
                (2_000, 3_000, (0.9, 0.7)),
            ],
            valid_regions=(
                (SpeechRegion(0, 3_000),),
                (SpeechRegion(0, 1_000),),
            ),
        )

        selected = select_channels(
            report,
            overrides=(ChannelSelectionOverride(0, 3_000, 1),),
        )

        manual = next(item for item in selected.segments if item.manual_override)
        self.assertAlmostEqual(manual.quality_score_coverage_ratio, 1.0 / 3.0)
        self.assertAlmostEqual(manual.confidence, 1.0 / 3.0)
        self.assertIn(
            "manual_override_unavailable",
            {warning.code for warning in selected.warnings},
        )

    def test_adjacent_overrides_preserve_distinct_user_reasons(self) -> None:
        report = make_report(3_000, [(0, 3_000, (0.9, 0.4))])

        selected = select_channels(
            report,
            overrides=(
                ChannelSelectionOverride(1_000, 1_500, 1, "first choice"),
                ChannelSelectionOverride(1_500, 2_000, 1, "second choice"),
            ),
        )

        manual = [item for item in selected.segments if item.manual_override]
        self.assertEqual(len(manual), 2)
        self.assertEqual(
            [item.override_reason for item in manual],
            ["first choice", "second choice"],
        )
        self.assertTrue(
            all(item.reason_codes == ("manual_override",) for item in manual)
        )

    def test_normal_silence_does_not_emit_low_quality_warning(self) -> None:
        samples = np.zeros(5_000, dtype=np.float32)
        signals = tuple(
            AnalysisSignal(
                samples.copy(),
                np.ones(samples.size, dtype=np.bool_),
                SAMPLE_RATE,
            )
            for _ in range(2)
        )
        quality = analyze_aligned_quality(
            signals,
            speech_config=SpeechAnalysisConfig(
                window_seconds=1.0,
                hop_seconds=1.0,
            ),
        )

        selected = select_channels(quality)

        self.assertNotIn(
            "all_channels_low_quality",
            {warning.code for warning in selected.warnings},
        )
        assert_complete_partition(self, selected)

    def test_noise_risk_candidate_emits_low_quality_warning(self) -> None:
        report = make_report(
            3_000,
            [(0, 3_000, (0.0, 0.1))],
            candidates=((0, 3_000, ()),),
        )

        selected = select_channels(report)

        self.assertIn(
            "all_channels_low_quality",
            {warning.code for warning in selected.warnings},
        )
        assert_complete_partition(self, selected)

    def test_overlapping_and_out_of_range_overrides_are_rejected(self) -> None:
        report = make_report(2_000, [(0, 2_000, (0.8, 0.4))])

        with self.assertRaisesRegex(ValueError, "must not overlap"):
            select_channels(
                report,
                overrides=(
                    ChannelSelectionOverride(100, 1_000, 0),
                    ChannelSelectionOverride(900, 1_500, 1),
                ),
            )
        with self.assertRaisesRegex(ValueError, "outside the input range"):
            select_channels(
                report,
                overrides=(ChannelSelectionOverride(100, 1_000, 2),),
            )
        with self.assertRaisesRegex(ValueError, "outside the reference timeline"):
            select_channels(
                report,
                overrides=(ChannelSelectionOverride(100, 2_001, 1),),
            )

    def test_configuration_validation_and_input_immutability(self) -> None:
        for values in (
            {"hysteresis_margin": float("nan")},
            {"hysteresis_margin": 1.1},
            {"minimum_hold_seconds": -0.1},
            {"boundary_search_seconds": -0.1},
            {"low_quality_threshold": True},
        ):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    ChannelSelectionConfig(**values)

        report = make_report(2_000, [(0, 2_000, (0.8, 0.4))])
        original = report.to_dict()
        first = select_channels(report).to_dict()
        second = select_channels(report).to_dict()
        self.assertEqual(first, second)
        self.assertEqual(report.to_dict(), original)

        switching = make_report(
            2_000,
            [
                (0, 1_000, (0.9, 0.3)),
                (1_000, 2_000, (0.3, 0.9)),
            ],
        )
        with self.assertRaisesRegex(ValueError, "minimum_hold_seconds"):
            select_channels(
                switching,
                config=ChannelSelectionConfig(
                    minimum_hold_seconds=1.0e308
                ),
            )
        with self.assertRaisesRegex(ValueError, "boundary_search_seconds"):
            select_channels(
                switching,
                config=ChannelSelectionConfig(
                    minimum_hold_seconds=0.0,
                    boundary_search_seconds=1.0e308,
                ),
            )

    def test_no_quality_windows_falls_back_to_the_most_valid_channel(
        self,
    ) -> None:
        report = make_report(
            1_000,
            [],
            valid_regions=((), (SpeechRegion(0, 1_000),)),
        )

        selected = select_channels(report)

        self.assertEqual(len(selected.segments), 1)
        self.assertEqual(selected.segments[0].selected_input_index, 1)
        self.assertIn(
            "fallback_most_valid", selected.segments[0].reason_codes
        )
        self.assertTrue(
            {"all_channels_unscoreable", "no_quality_windows"}
            <= {warning.code for warning in selected.warnings}
        )
        assert_complete_partition(self, selected)

    def test_no_window_override_tracks_partial_channel_validity(self) -> None:
        report = make_report(
            1_000,
            [],
            valid_regions=(
                (SpeechRegion(0, 1_000),),
                (SpeechRegion(0, 100),),
            ),
        )

        selected = select_channels(
            report,
            overrides=(ChannelSelectionOverride(0, 1_000, 1),),
        )

        self.assertEqual(len(selected.segments), 1)
        self.assertEqual(selected.segments[0].selected_input_index, 1)
        self.assertAlmostEqual(selected.segments[0].confidence, 0.1)
        warning = next(
            item
            for item in selected.warnings
            if item.code == "manual_override_unavailable"
        )
        self.assertEqual(warning.region, SpeechRegion(100, 1_000))
        assert_complete_partition(self, selected)

    def test_empty_timeline_returns_empty_selection(self) -> None:
        report = make_report(0, [], valid_regions=((), ()))

        selected = select_channels(report)

        self.assertEqual(selected.segments, ())
        self.assertEqual(selected.switch_count, 0)
        json.dumps(selected.to_dict(), allow_nan=False)


if __name__ == "__main__":
    unittest.main()
