from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

import numpy as np


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from multimic.align import (
    AlignmentConfig,
    WindowAlignment,
    _candidate_clusters,
    _coerce_signal,
    _select_reference_windows,
    estimate_initial_offset,
    estimate_initial_offsets,
)
from multimic.pipeline import (
    ANALYSIS_SAMPLE_RATE,
    AnalysisSignal,
    AudioInputMetadata,
    PreparedAudioInput,
    PreparedMultiAudio,
    align_prepared_inputs,
    place_signal_on_timeline,
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


if __name__ == "__main__":
    unittest.main()
