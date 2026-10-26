from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

import numpy as np


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from multimic.align import (
    AlignmentConfig,
    InputAlignment,
    WindowAlignment,
    _candidate_clusters,
    _coerce_signal,
    _select_reference_windows,
    estimate_initial_offset,
    estimate_initial_offsets,
    estimate_time_alignment,
    estimate_time_alignments,
)
from multimic.pipeline import (
    ANALYSIS_SAMPLE_RATE,
    AnalysisSignal,
    AudioInputMetadata,
    PreparedAudioInput,
    PreparedMultiAudio,
    align_prepared_inputs,
    place_signal_on_timeline,
    warp_signal_to_reference_timeline,
)


SAMPLE_RATE = ANALYSIS_SAMPLE_RATE


def speech_like_master(
    *,
    seed: int = 1234,
    duration_seconds: float = 16.0,
) -> np.ndarray:
    """Build deterministic, non-periodic, syllable-modulated test audio."""

    sample_count = int(round(duration_seconds * SAMPLE_RATE))
    rng = np.random.default_rng(seed)
    white = rng.normal(0.0, 1.0, sample_count)
    kernel = np.hanning(25)
    kernel /= np.sum(kernel)
    colored = np.convolve(white, kernel, mode="same")

    envelope = np.zeros(sample_count, dtype=np.float64)
    for index, start_seconds in enumerate(
        np.arange(0.35, duration_seconds - 0.2, 0.67)
    ):
        burst_samples = int(round((0.24 + 0.035 * (index % 5)) * SAMPLE_RATE))
        start = int(round(start_seconds * SAMPLE_RATE))
        end = min(sample_count, start + burst_samples)
        envelope[start:end] += np.sin(np.linspace(0.0, np.pi, end - start)) ** 2

    time_axis = np.arange(sample_count, dtype=np.float64) / SAMPLE_RATE
    normalized = colored / max(float(np.std(colored)), 1.0e-12)
    signal = (
        0.30 * envelope * normalized
        + 0.018 * envelope * np.sin(2.0 * np.pi * 113.0 * time_axis)
    )
    return np.asarray(signal, dtype=np.float32)


def analysis_signal(
    samples: np.ndarray,
    mask: np.ndarray | None = None,
) -> AnalysisSignal:
    values = np.asarray(samples, dtype=np.float32)
    if mask is None:
        mask = np.ones(values.size, dtype=np.bool_)
    return AnalysisSignal(values, mask, SAMPLE_RATE)


def analysis_signal_at_rate(
    samples: np.ndarray,
    sample_rate: int,
    mask: np.ndarray | None = None,
) -> AnalysisSignal:
    values = np.asarray(samples, dtype=np.float32)
    if mask is None:
        mask = np.ones(values.size, dtype=np.bool_)
    return AnalysisSignal(values, mask, sample_rate)


def long_drift_recordings(
    *,
    sample_rate: int = 400,
    duration_seconds: float = 1805.0,
    offset_seconds: float = 0.25,
    drift_ppm: float = 200.0,
    nonlinear_drift_seconds: float | None = None,
) -> tuple[AnalysisSignal, AnalysisSignal]:
    """Create deterministic 30-minute signals without 16 kHz test memory cost.

    The generated target follows the public alignment convention
    ``reference = offset + time_scale * target``.  A non-``None`` nonlinear
    drift value replaces the constant scale with a quadratic accumulated
    timing error that reaches the requested number of seconds at the end.
    """

    sample_count = int(round(duration_seconds * sample_rate))
    rng = np.random.default_rng(20261023)
    white = rng.normal(0.0, 1.0, sample_count + 16)
    kernel = np.hanning(9)
    kernel /= np.sum(kernel)
    colored = np.convolve(white, kernel, mode="same")[:sample_count]
    sample_indexes = np.arange(sample_count, dtype=np.float64)
    envelope = 0.75 + 0.25 * np.sin(
        2.0 * np.pi * sample_indexes / (17.3 * sample_rate)
    )
    reference_samples = np.asarray(
        0.15 * envelope * colored / max(float(np.std(colored)), 1.0e-12),
        dtype=np.float32,
    )

    offset_samples = offset_seconds * sample_rate
    if nonlinear_drift_seconds is None:
        time_scale = 1.0 + drift_ppm / 1_000_000.0
        target_count = int(
            np.floor((sample_count - 1 - offset_samples) / time_scale)
        ) + 1
        target_indexes = np.arange(target_count, dtype=np.float64)
        reference_positions = offset_samples + time_scale * target_indexes
    else:
        accumulated_drift = nonlinear_drift_seconds * sample_rate
        target_count = max(
            2,
            int(np.floor(sample_count - 1 - offset_samples - accumulated_drift))
            + 1,
        )
        target_indexes = np.arange(target_count, dtype=np.float64)
        normalized = target_indexes / max(1.0, float(target_count - 1))
        reference_positions = (
            offset_samples
            + target_indexes
            + accumulated_drift * np.square(normalized)
        )

    target_samples = np.interp(
        reference_positions,
        sample_indexes,
        reference_samples,
    ).astype(np.float32)
    return (
        analysis_signal_at_rate(reference_samples, sample_rate),
        analysis_signal_at_rate(target_samples, sample_rate),
    )


def cropped_recordings() -> tuple[AnalysisSignal, AnalysisSignal, AnalysisSignal]:
    """Return reference, +500 ms placement, and -500 ms placement inputs."""

    master = speech_like_master()
    start = 3 * SAMPLE_RATE
    length = 9 * SAMPLE_RATE
    reference = master[start : start + length]
    positive = master[start + SAMPLE_RATE // 2 : start + SAMPLE_RATE // 2 + length]
    negative = master[start - SAMPLE_RATE // 2 : start - SAMPLE_RATE // 2 + length]

    positive = (
        0.65 * positive
        + np.random.default_rng(100).normal(0.0, 0.003, length)
    ).astype(np.float32)
    negative = (
        -0.80 * negative
        + np.random.default_rng(101).normal(0.0, 0.003, length)
    ).astype(np.float32)
    return (
        analysis_signal(reference),
        analysis_signal(positive),
        analysis_signal(negative),
    )


def metadata(index: int, sample_count: int) -> AudioInputMetadata:
    duration = sample_count / SAMPLE_RATE
    return AudioInputMetadata(
        input_index=index,
        path=Path(f"input_{index}.wav"),
        file_name=f"input_{index}.wav",
        extension=".wav",
        size_bytes=sample_count * 2,
        sha256=f"sha256-{index}",
        duration_seconds=duration,
        decoded_duration_seconds=duration,
        sample_rate=SAMPLE_RATE,
        channels=1,
        channel_layout="mono",
        codec="pcm_s16le",
        container_format="wav",
        analysis_sample_rate=SAMPLE_RATE,
        analysis_duration_seconds=duration,
        downmix_method="none_already_mono",
        resample_method="none_same_sample_rate",
    )


class InitialOffsetTests(unittest.TestCase):
    def test_candidate_cluster_never_counts_a_vote_outside_tolerance(self) -> None:
        def candidate(offset: int, correlation: float) -> WindowAlignment:
            return WindowAlignment(
                reference_start_sample=0,
                reference_end_sample=1,
                offset_samples=offset,
                correlation=correlation,
                second_peak_correlation=0.0,
                peak_prominence=0.10,
                polarity=1,
                gcc_phat_offset_samples=None,
                gcc_phat_agrees=None,
                accepted=True,
            )

        tolerance = 50
        clusters = _candidate_clusters(
            (
                (candidate(176, 1.0),),
                (candidate(116, 0.30),),
                (candidate(135, 0.30),),
            ),
            tolerance,
        )

        self.assertTrue(clusters)
        self.assertLess(max(cluster.count for cluster in clusters), 3)
        for cluster in clusters:
            for _, observation in cluster.choices:
                self.assertLessEqual(
                    abs(observation.offset_samples - cluster.estimated_offset),
                    tolerance,
                )

    def test_estimates_both_half_second_placement_offsets(self) -> None:
        reference, positive, negative = cropped_recordings()

        report = estimate_initial_offsets((reference, positive, negative))

        self.assertTrue(report.success)
        self.assertEqual(report.inputs[0].offset_samples, 0)
        self.assertEqual(report.inputs[0].offset_ms, 0.0)
        self.assertEqual(report.inputs[0].confidence, 1.0)
        self.assertLessEqual(
            abs(report.inputs[1].offset_samples - SAMPLE_RATE // 2),
            320,
        )
        self.assertLessEqual(
            abs(report.inputs[2].offset_samples + SAMPLE_RATE // 2),
            320,
        )
        self.assertGreater(report.inputs[1].confidence, 0.5)
        self.assertGreater(report.inputs[2].confidence, 0.5)

        # The reported value is a placement offset, not a correlator lag.
        placed = place_signal_on_timeline(
            positive,
            start_sample=report.inputs[1].offset_samples,
            timeline_sample_count=reference.samples.size,
        )
        overlap = placed.valid_mask & reference.valid_mask
        correlation = np.corrcoef(
            reference.samples[overlap],
            placed.samples[overlap],
        )[0, 1]
        self.assertGreater(correlation, 0.95)

    def test_nonzero_reference_index_preserves_input_order_and_sign(self) -> None:
        master = speech_like_master(seed=22)
        length = 8 * SAMPLE_RATE
        early = analysis_signal(master[2 * SAMPLE_RATE : 10 * SAMPLE_RATE])
        reference = analysis_signal(
            master[
                2 * SAMPLE_RATE + SAMPLE_RATE // 2 :
                10 * SAMPLE_RATE + SAMPLE_RATE // 2
            ]
        )
        late = analysis_signal(master[3 * SAMPLE_RATE : 11 * SAMPLE_RATE])

        report = estimate_initial_offsets(
            (early, reference, late),
            reference_index=1,
        )

        self.assertEqual([item.input_index for item in report.inputs], [0, 1, 2])
        self.assertEqual(report.inputs[1].offset_samples, 0)
        self.assertLessEqual(
            abs(report.inputs[0].offset_samples + SAMPLE_RATE // 2),
            320,
        )
        self.assertLessEqual(
            abs(report.inputs[2].offset_samples - SAMPLE_RATE // 2),
            320,
        )

    def test_unrelated_recording_is_a_file_level_failure(self) -> None:
        reference, related, _ = cropped_recordings()
        unrelated = analysis_signal(
            speech_like_master(seed=987)[3 * SAMPLE_RATE : 12 * SAMPLE_RATE]
        )

        report = estimate_initial_offsets((reference, related, unrelated))

        self.assertTrue(report.inputs[0].success)
        self.assertTrue(report.inputs[1].success)
        rejected = report.inputs[2]
        self.assertFalse(report.success)
        self.assertFalse(rejected.success)
        self.assertIsNone(rejected.offset_samples)
        self.assertIsNone(rejected.offset_ms)
        self.assertIn(
            rejected.failure_code,
            {"low_correlation", "ambiguous_peak", "inconsistent_offsets"},
        )
        self.assertLess(rejected.confidence, 0.45)
        json.dumps(report.to_dict(), allow_nan=False)

    def test_two_shared_windows_are_not_enough_to_claim_alignment(self) -> None:
        sample_count = 9 * SAMPLE_RATE
        reference_samples = speech_like_master(seed=31)[:sample_count]
        target_samples = speech_like_master(seed=32)[:sample_count].copy()
        reference = analysis_signal(reference_samples)
        config = AlignmentConfig()
        windows = _select_reference_windows(
            _coerce_signal(reference, "reference"),
            config,
        )
        self.assertEqual(len(windows), config.max_windows)
        for window_index in (1, 6):
            start, end = windows[window_index]
            target_samples[start:end] = reference_samples[start:end]

        result = estimate_initial_offsets(
            (reference, analysis_signal(target_samples)),
            config=config,
        ).inputs[1]

        self.assertFalse(result.success)
        self.assertIsNone(result.offset_samples)
        self.assertEqual(result.failure_code, "insufficient_shared_audio")
        self.assertTrue(result.failure_reason)

    def test_invalid_mask_pattern_cannot_become_correlation_evidence(self) -> None:
        sample_count = 8 * SAMPLE_RATE
        mask = np.ones(sample_count, dtype=np.bool_)
        invalid = np.random.default_rng(77).choice(
            sample_count,
            size=sample_count // 200,
            replace=False,
        )
        mask[invalid] = False
        reference = analysis_signal(
            np.full(sample_count, 0.20, dtype=np.float32),
            mask,
        )
        target = analysis_signal(
            np.full(sample_count, -0.07, dtype=np.float32),
            mask.copy(),
        )

        report = estimate_initial_offsets((reference, target))

        self.assertFalse(report.success)
        self.assertEqual(
            [item.failure_code for item in report.inputs],
            ["reference_insufficient_signal", "reference_insufficient_signal"],
        )
        self.assertTrue(all(item.offset_samples is None for item in report.inputs))

    def test_shorter_target_uses_only_comparable_recorded_windows(self) -> None:
        master = speech_like_master(seed=55)
        start = 3 * SAMPLE_RATE
        reference = analysis_signal(master[start : start + 9 * SAMPLE_RATE])
        target = analysis_signal(
            master[
                start + SAMPLE_RATE // 2 :
                start + SAMPLE_RATE // 2 + 4 * SAMPLE_RATE
            ]
        )

        result = estimate_initial_offsets((reference, target)).inputs[1]

        self.assertTrue(result.success, result.failure_reason)
        self.assertLessEqual(
            abs(result.offset_samples - SAMPLE_RATE // 2),
            320,
        )
        self.assertGreaterEqual(result.consistent_window_count, 3)

    def test_conflicting_window_offsets_fail_as_inconsistent(self) -> None:
        master = speech_like_master(seed=66, duration_seconds=18.0)
        start = 3 * SAMPLE_RATE
        sample_count = 9 * SAMPLE_RATE
        reference_samples = master[start : start + sample_count].copy()
        target_samples = reference_samples.copy()
        split = sample_count // 2
        target_samples[split:] = master[
            start + split + SAMPLE_RATE // 2 :
            start + sample_count + SAMPLE_RATE // 2
        ]

        result = estimate_initial_offsets(
            (analysis_signal(reference_samples), analysis_signal(target_samples))
        ).inputs[1]

        self.assertFalse(result.success)
        self.assertEqual(result.failure_code, "inconsistent_offsets")
        self.assertIsNone(result.offset_samples)

    def test_shared_secondary_peaks_outvote_window_local_decoys(self) -> None:
        window_samples = int(0.75 * SAMPLE_RATE)
        starts = np.asarray([3, 8, 13, 18]) * SAMPLE_RATE
        decoy_shifts = (
            np.asarray([-1.6, -1.1, 0.9, 1.5]) * SAMPLE_RATE
        ).astype(int)
        sample_count = 23 * SAMPLE_RATE
        reference_samples = np.zeros(sample_count, dtype=np.float32)
        target_samples = np.zeros(sample_count, dtype=np.float32)
        rng = np.random.default_rng(20261023)

        for start, decoy_shift in zip(starts, decoy_shifts):
            event = rng.normal(0.0, 0.15, window_samples).astype(np.float32)
            reference_samples[start : start + window_samples] = event
            noise = rng.normal(0.0, 0.15, window_samples).astype(np.float32)
            target_samples[start : start + window_samples] = (
                0.65 * event + 0.90 * noise
            )
            decoy_start = start + decoy_shift
            target_samples[decoy_start : decoy_start + window_samples] = event

        config = AlignmentConfig(
            max_windows=4,
            min_reference_windows=4,
            min_consistent_windows=3,
        )
        result = estimate_initial_offset(
            analysis_signal(reference_samples),
            analysis_signal(target_samples),
            config=config,
        )

        self.assertTrue(result.success, result.failure_reason)
        self.assertLessEqual(abs(result.offset_samples), 320)
        self.assertEqual(result.consistent_window_count, 4)
        self.assertTrue(
            any(
                observation.selected_peak_rank is not None
                and observation.selected_peak_rank > 1
                for observation in result.window_observations
            )
        )

    def test_peak_at_configured_search_boundary_is_not_accepted(self) -> None:
        master = speech_like_master(seed=88, duration_seconds=18.0)
        start = 3 * SAMPLE_RATE
        sample_count = 8 * SAMPLE_RATE
        reference = analysis_signal(master[start : start + sample_count])
        target = analysis_signal(
            master[
                start + 2 * SAMPLE_RATE :
                start + 2 * SAMPLE_RATE + sample_count
            ]
        )

        result = estimate_initial_offsets((reference, target)).inputs[1]

        self.assertFalse(result.success)
        self.assertEqual(result.failure_code, "offset_at_search_boundary")
        self.assertIsNone(result.offset_samples)

    def test_periodic_audio_is_not_reported_as_a_reliable_alignment(self) -> None:
        sample_count = 8 * SAMPLE_RATE
        time_axis = np.arange(sample_count) / SAMPLE_RATE
        for frequency in (200.0, 631.0):
            with self.subTest(frequency=frequency):
                tone = np.asarray(
                    0.2 * np.sin(2.0 * np.pi * frequency * time_axis),
                    dtype=np.float32,
                )
                shifted = np.roll(tone, SAMPLE_RATE // 2)

                result = estimate_initial_offsets(
                    (analysis_signal(tone), analysis_signal(shifted))
                ).inputs[1]

                self.assertFalse(result.success)
                self.assertIsNone(result.offset_samples)
                self.assertIn(
                    result.failure_code,
                    {
                        "ambiguous_peak",
                        "inconsistent_offsets",
                        "low_confidence",
                        "offset_at_search_boundary",
                    },
                )
                self.assertEqual(
                    result.accepted_window_count,
                    sum(
                        observation.accepted
                        for observation in result.window_observations
                    ),
                )

    def test_silent_reference_fails_every_file_without_inventing_zero_offsets(
        self,
    ) -> None:
        silence = analysis_signal(np.zeros(5 * SAMPLE_RATE, dtype=np.float32))
        other = analysis_signal(speech_like_master(seed=5)[: 5 * SAMPLE_RATE])

        report = estimate_initial_offsets((silence, other))

        self.assertFalse(report.success)
        self.assertEqual(len(report.failures), 2)
        for result in report.inputs:
            self.assertEqual(result.failure_code, "reference_insufficient_signal")
            self.assertIsNone(result.offset_samples)
            self.assertEqual(result.confidence, 0.0)

    def test_invalid_target_padding_is_excluded_from_window_evidence(self) -> None:
        reference, positive, _ = cropped_recordings()
        mask = positive.valid_mask.copy()
        mask[:SAMPLE_RATE] = False
        mask[-SAMPLE_RATE:] = False
        masked = AnalysisSignal(positive.samples.copy(), mask, SAMPLE_RATE)

        result = estimate_initial_offsets((reference, masked)).inputs[1]

        self.assertTrue(result.success, result.failure_reason)
        self.assertLessEqual(
            abs(result.offset_samples - SAMPLE_RATE // 2),
            320,
        )
        self.assertLess(result.accepted_window_count, result.selected_window_count)

    def test_legacy_single_window_configuration_keeps_positive_offset(self) -> None:
        reference, positive, _ = cropped_recordings()
        config = AlignmentConfig(
            max_windows=1,
            min_reference_windows=1,
            min_consistent_windows=1,
        )

        pair_result = estimate_initial_offset(
            reference,
            positive,
            config=config,
        )
        batch_result = estimate_initial_offsets(
            (reference, positive),
            config=config,
        ).inputs[1]

        for result in (pair_result, batch_result):
            self.assertTrue(result.success, result.failure_reason)
            self.assertEqual(result.selected_window_count, 1)
            self.assertEqual(result.consistent_window_count, 1)
            self.assertLessEqual(
                abs(result.offset_samples - SAMPLE_RATE // 2),
                320,
            )

    def test_contract_errors_are_distinct_from_alignment_failures(self) -> None:
        reference, positive, _ = cropped_recordings()
        wrong_rate = AnalysisSignal(
            positive.samples,
            positive.valid_mask,
            SAMPLE_RATE // 2,
        )

        with self.assertRaisesRegex(ValueError, "sample rates must match"):
            estimate_initial_offsets((reference, wrong_rate))
        with self.assertRaisesRegex(ValueError, "outside the input range"):
            estimate_initial_offsets((reference, positive), reference_index=2)
        with self.assertRaisesRegex(ValueError, "reference_index must be an integer"):
            estimate_initial_offsets((reference, positive), reference_index=True)
        with self.assertRaisesRegex(ValueError, "min_reference_windows"):
            AlignmentConfig(max_windows=2, min_reference_windows=3)
        with self.assertRaisesRegex(ValueError, "input_index must not be negative"):
            estimate_initial_offset(reference, positive, input_index=-1)
        with self.assertRaisesRegex(ValueError, "finite number"):
            AlignmentConfig(window_seconds="0.75")  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "drift_min_anchors"):
            AlignmentConfig(max_windows=4, drift_min_anchors=5)

        # A legacy short-input setup may intentionally use only two windows;
        # drift analysis will conservatively decline it instead of making the
        # offset-only configuration itself invalid.
        AlignmentConfig(
            max_windows=2,
            min_reference_windows=2,
            min_consistent_windows=2,
        )


class DriftAlignmentTests(unittest.TestCase):
    def test_thirty_minute_linear_drift_is_corrected_and_diagnosed(self) -> None:
        reference, target = long_drift_recordings()

        report = estimate_time_alignments((reference, target))
        result = report.inputs[1]

        self.assertTrue(report.success, result.failure_reason)
        self.assertTrue(result.success, result.failure_reason)
        self.assertFalse(result.requires_manual_review)
        self.assertIsNone(result.warning_code)
        self.assertTrue(result.correction_applied)
        self.assertEqual(result.correction_method, "affine")
        self.assertAlmostEqual(result.offset_ms, 250.0, delta=20.0)
        self.assertAlmostEqual(result.estimated_time_scale, 1.0002, delta=30e-6)
        self.assertAlmostEqual(result.time_scale, 1.0002, delta=30e-6)
        self.assertAlmostEqual(result.drift_ppm, 200.0, delta=30.0)
        self.assertGreater(abs(result.drift_span_ms), 200.0)
        self.assertGreater(
            result.pre_correction_error_ms,
            result.post_correction_error_ms,
        )
        self.assertLessEqual(result.post_correction_error_ms, 20.0)
        self.assertLessEqual(
            result.max_post_correction_error_ms,
            AlignmentConfig().drift_residual_tolerance_ms,
        )
        self.assertGreaterEqual(len(result.drift_anchors), 3)

        reference_result = report.inputs[0]
        self.assertTrue(reference_result.success)
        self.assertEqual(reference_result.offset_samples, 0)
        self.assertEqual(reference_result.time_scale, 1.0)
        self.assertEqual(reference_result.estimated_time_scale, 1.0)
        self.assertEqual(reference_result.drift_ppm, 0.0)
        self.assertEqual(reference_result.drift_span_ms, 0.0)
        self.assertEqual(reference_result.correction_method, "reference")
        self.assertFalse(reference_result.correction_applied)
        self.assertFalse(reference_result.requires_manual_review)

        payload = report.to_dict()
        result_payload = payload["inputs"][1]
        for field in (
            "time_scale",
            "estimated_time_scale",
            "drift_ppm",
            "drift_span_ms",
            "pre_correction_error_ms",
            "post_correction_error_ms",
            "max_post_correction_error_ms",
            "correction_method",
            "correction_applied",
            "requires_manual_review",
            "warning_code",
            "drift_anchors",
        ):
            self.assertIn(field, result_payload)
        json.dumps(payload, allow_nan=False)

        warped_reference = warp_signal_to_reference_timeline(
            reference,
            reference_result,
            reference.samples.size,
        )
        np.testing.assert_array_equal(warped_reference.samples, reference.samples)
        np.testing.assert_array_equal(
            warped_reference.valid_mask,
            reference.valid_mask,
        )

        warped_target = warp_signal_to_reference_timeline(
            target,
            result,
            reference.samples.size,
        )
        self.assertEqual(warped_target.samples.size, reference.samples.size)
        self.assertEqual(warped_target.valid_mask.size, reference.valid_mask.size)
        overlap = warped_target.valid_mask & reference.valid_mask
        self.assertGreater(np.count_nonzero(overlap), reference.samples.size * 0.95)
        correlation = np.corrcoef(
            warped_target.samples[overlap],
            reference.samples[overlap],
        )[0, 1]
        self.assertGreater(correlation, 0.98)

    def test_nonlinear_drift_requires_manual_review_and_is_not_warped(self) -> None:
        reference, target = long_drift_recordings(
            nonlinear_drift_seconds=0.80,
        )

        result = estimate_time_alignment(reference, target)

        self.assertFalse(result.success)
        self.assertTrue(result.requires_manual_review)
        self.assertEqual(result.status, "manual_review")
        self.assertFalse(result.correction_applied)
        self.assertEqual(result.failure_code, "unstable_drift")
        self.assertEqual(result.warning_code, "manual_review_required")
        self.assertIsNotNone(result.estimated_time_scale)
        self.assertGreaterEqual(len(result.drift_anchors), 3)
        self.assertGreater(
            result.max_post_correction_error_ms,
            AlignmentConfig().drift_residual_tolerance_ms,
        )
        self.assertTrue(any(not anchor.inlier for anchor in result.drift_anchors))
        json.dumps(result.to_dict(), allow_nan=False)
        with self.assertRaisesRegex(ValueError, "manual review"):
            warp_signal_to_reference_timeline(
                target,
                result,
                reference.samples.size,
            )

    def test_affine_warp_uses_inverse_mapping_and_conservative_mask(self) -> None:
        source = AnalysisSignal(
            samples=np.array([0.0, 10.0, 20.0, 30.0], dtype=np.float32),
            valid_mask=np.array([True, True, False, True]),
            sample_rate=4,
        )
        alignment = InputAlignment(
            input_index=1,
            reference_index=0,
            offset_samples=1,
            offset_ms=250.0,
            confidence=1.0,
            method="test",
            failure=None,
            selected_window_count=3,
            accepted_window_count=3,
            consistent_window_count=3,
            offset_dispersion_ms=0.0,
            window_observations=(),
            time_scale=2.0,
            estimated_time_scale=2.0,
            drift_ppm=1_000_000.0,
            drift_span_ms=0.0,
            pre_correction_error_ms=0.0,
            post_correction_error_ms=0.0,
            max_post_correction_error_ms=0.0,
            correction_method="affine",
            correction_applied=True,
            requires_manual_review=False,
            drift_anchors=(),
        )

        warped = warp_signal_to_reference_timeline(
            source,
            alignment,
            8,
            chunk_size=2,
        )

        np.testing.assert_array_equal(
            warped.valid_mask,
            np.array([False, True, True, True, False, False, False, True]),
        )
        np.testing.assert_allclose(
            warped.samples,
            np.array([0.0, 0.0, 5.0, 10.0, 0.0, 0.0, 0.0, 30.0]),
        )
        with self.assertRaisesRegex(ValueError, "timeline_sample_count"):
            warp_signal_to_reference_timeline(source, alignment, True)


class FallbackSearchTests(unittest.TestCase):
    NARROW_ONLY = AlignmentConfig(fallback_max_offset_seconds=None)

    @staticmethod
    def late_target_recordings(
        offset_seconds: float,
    ) -> tuple[AnalysisSignal, AnalysisSignal]:
        """Return a reference and a target that started ``offset_seconds`` later."""

        master = speech_like_master(seed=4242, duration_seconds=40.0)
        start = 3 * SAMPLE_RATE
        length = 20 * SAMPLE_RATE
        late = start + int(round(offset_seconds * SAMPLE_RATE))
        target = (
            0.70 * master[late : late + length]
            + np.random.default_rng(102).normal(0.0, 0.003, length)
        ).astype(np.float32)
        return (
            analysis_signal(master[start : start + length]),
            analysis_signal(target),
        )

    def test_offset_beyond_narrow_search_is_resolved_by_fallback(self) -> None:
        reference, target = self.late_target_recordings(6.0)

        narrow = estimate_time_alignments(
            (reference, target),
            config=self.NARROW_ONLY,
        ).inputs[1]
        report = estimate_time_alignments((reference, target))
        single = estimate_time_alignment(reference, target)

        self.assertEqual(narrow.status, "failed")
        self.assertTrue(report.success, report.inputs[1].failure_reason)
        for result in (report.inputs[1], single):
            self.assertTrue(result.success, result.failure_reason)
            self.assertLessEqual(
                abs(result.offset_samples - 6 * SAMPLE_RATE),
                320,
            )
            self.assertGreater(result.confidence, 0.5)
        json.dumps(report.to_dict(), allow_nan=False)

    def test_long_recording_offset_beyond_narrow_search_keeps_drift_model(
        self,
    ) -> None:
        reference, target = long_drift_recordings(offset_seconds=6.0)
        # At this 400 Hz test rate the default 0.75 s window has only 300
        # samples, so chance peaks can pass the correlation floor.  An 8 s
        # window restores the chance-correlation level of 16 kHz windows.
        config = AlignmentConfig(window_seconds=8.0, hop_seconds=2.0)

        narrow = estimate_time_alignments(
            (reference, target),
            config=replace(config, fallback_max_offset_seconds=None),
        ).inputs[1]
        result = estimate_time_alignments(
            (reference, target),
            config=config,
        ).inputs[1]

        self.assertEqual(narrow.status, "failed")
        self.assertTrue(result.success, result.failure_reason)
        self.assertEqual(result.correction_method, "affine")
        self.assertAlmostEqual(result.offset_ms, 6000.0, delta=20.0)
        self.assertAlmostEqual(result.drift_ppm, 200.0, delta=30.0)

    def test_inputs_resolved_by_narrow_search_are_unchanged(self) -> None:
        reference, positive, negative = cropped_recordings()
        signals = (reference, positive, negative)

        self.assertEqual(
            estimate_time_alignments(signals).to_dict(),
            estimate_time_alignments(signals, config=self.NARROW_ONLY).to_dict(),
        )

    def test_unrelated_recording_keeps_narrow_search_failure(self) -> None:
        reference, _, _ = cropped_recordings()
        unrelated = analysis_signal(
            speech_like_master(seed=987)[3 * SAMPLE_RATE : 12 * SAMPLE_RATE]
        )

        result = estimate_time_alignments((reference, unrelated)).inputs[1]
        narrow = estimate_time_alignments(
            (reference, unrelated),
            config=self.NARROW_ONLY,
        ).inputs[1]

        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.offset_samples)
        self.assertEqual(result.to_dict(), narrow.to_dict())

    def test_fallback_bound_is_validated(self) -> None:
        for value in (0.0, -1.0, float("nan"), True, "30"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError,
                    "fallback_max_offset_seconds",
                ):
                    AlignmentConfig(
                        fallback_max_offset_seconds=value,  # type: ignore[arg-type]
                    )
        AlignmentConfig(fallback_max_offset_seconds=None)
        # A bound that does not widen the search simply disables the retry.
        AlignmentConfig(max_offset_seconds=60.0, fallback_max_offset_seconds=30.0)


class AlignmentPipelineTests(unittest.TestCase):
    def test_prepared_input_wrapper_is_non_mutating(self) -> None:
        reference, positive, _ = cropped_recordings()
        original_samples = tuple(
            signal.samples.copy() for signal in (reference, positive)
        )
        original_masks = tuple(
            signal.valid_mask.copy() for signal in (reference, positive)
        )
        prepared = PreparedMultiAudio(
            inputs=tuple(
                PreparedAudioInput(
                    metadata=metadata(index, signal.samples.size),
                    analysis=signal,
                )
                for index, signal in enumerate((reference, positive))
            ),
            warnings=(),
            analysis_sample_rate=SAMPLE_RATE,
        )

        report = align_prepared_inputs(prepared)

        self.assertTrue(report.success)
        self.assertLessEqual(
            abs(report.inputs[1].offset_samples - SAMPLE_RATE // 2),
            320,
        )
        for index, item in enumerate(prepared.inputs):
            np.testing.assert_array_equal(item.samples, original_samples[index])
            np.testing.assert_array_equal(item.valid_mask, original_masks[index])

    def test_prepared_input_wrapper_uses_drift_aware_estimator(self) -> None:
        reference, positive, _ = cropped_recordings()
        prepared = PreparedMultiAudio(
            inputs=tuple(
                PreparedAudioInput(
                    metadata=metadata(index, signal.samples.size),
                    analysis=signal,
                )
                for index, signal in enumerate((reference, positive))
            ),
            warnings=(),
            analysis_sample_rate=SAMPLE_RATE,
        )
        expected = mock.sentinel.alignment_report
        config = AlignmentConfig()

        with mock.patch(
            "multimic.pipeline.estimate_time_alignments",
            return_value=expected,
        ) as estimator:
            result = align_prepared_inputs(
                prepared,
                reference_index=1,
                config=config,
            )

        self.assertIs(result, expected)
        estimator.assert_called_once()
        signals = estimator.call_args.args[0]
        self.assertEqual(len(signals), 2)
        self.assertIs(signals[0], reference)
        self.assertIs(signals[1], positive)
        self.assertEqual(estimator.call_args.kwargs["reference_index"], 1)
        self.assertIs(estimator.call_args.kwargs["config"], config)


if __name__ == "__main__":
    unittest.main()
