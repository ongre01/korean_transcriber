from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

import numpy as np


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from multimic.align import InputAlignment, MultiAudioAlignment
from multimic.pipeline import (
    AnalysisSignal,
    AudioInputMetadata,
    PreparedAudioInput,
    PreparedMultiAudio,
)
from multimic.quality import (
    SpeechAnalysisConfig,
    analyze_aligned_speech,
    analyze_prepared_speech,
)


def analysis_signal(
    samples: np.ndarray,
    *,
    sample_rate: int = 1_000,
    valid_mask: np.ndarray | None = None,
) -> AnalysisSignal:
    values = np.asarray(samples, dtype=np.float32)
    return AnalysisSignal(
        samples=values,
        valid_mask=(
            np.ones(values.size, dtype=np.bool_)
            if valid_mask is None
            else valid_mask
        ),
        sample_rate=sample_rate,
    )


def add_tone(
    samples: np.ndarray,
    start: int,
    end: int,
    *,
    sample_rate: int = 1_000,
    frequency: float = 120.0,
    amplitude: float = 0.2,
) -> None:
    time = np.arange(end - start, dtype=np.float64) / sample_rate
    alternating_level = np.where(
        (np.arange(end - start) // max(1, sample_rate // 10)) % 2 == 0,
        1.0,
        0.25,
    )
    samples[start:end] = (
        amplitude
        * alternating_level
        * np.sin(2.0 * np.pi * frequency * time)
    )


def config(**overrides: object) -> SpeechAnalysisConfig:
    values: dict[str, object] = {
        "threshold_db": -40.0,
        "min_speech_duration": 0.2,
        "min_silence_duration": 0.1,
        "padding_duration": 0.0,
    }
    values.update(overrides)
    return SpeechAnalysisConfig(**values)


def metadata(index: int, signal: AnalysisSignal) -> AudioInputMetadata:
    duration = signal.samples.size / signal.sample_rate
    return AudioInputMetadata(
        input_index=index,
        path=Path(f"input_{index}.wav"),
        file_name=f"input_{index}.wav",
        extension=".wav",
        size_bytes=signal.samples.nbytes,
        sha256=str(index) * 64,
        duration_seconds=duration,
        decoded_duration_seconds=duration,
        sample_rate=signal.sample_rate,
        channels=1,
        channel_layout="mono",
        codec="pcm_s16le",
        container_format="wav",
        analysis_sample_rate=signal.sample_rate,
        analysis_duration_seconds=duration,
        downmix_method="none_already_mono",
        resample_method="none",
    )


def successful_alignment(
    input_index: int,
    reference_index: int,
    offset_samples: int,
    sample_rate: int,
) -> InputAlignment:
    return InputAlignment(
        input_index=input_index,
        reference_index=reference_index,
        offset_samples=offset_samples,
        offset_ms=offset_samples * 1_000.0 / sample_rate,
        confidence=1.0,
        method="reference" if input_index == reference_index else "test_offset",
        failure=None,
        selected_window_count=3,
        accepted_window_count=3,
        consistent_window_count=3,
        offset_dispersion_ms=0.0,
        time_scale=1.0,
        estimated_time_scale=1.0,
        drift_ppm=0.0,
        correction_method="reference" if input_index == reference_index else "none",
        correction_applied=False,
    )


class MultiMicSpeechAnalysisTests(unittest.TestCase):
    def test_single_input_speech_survives_union(self) -> None:
        quiet = np.zeros(6_000, dtype=np.float32)
        local_speech = np.zeros(6_000, dtype=np.float32)
        add_tone(local_speech, 2_000, 3_000)

        report = analyze_aligned_speech(
            (analysis_signal(quiet), analysis_signal(local_speech)),
            config=config(),
        )

        self.assertEqual(len(report.speech_regions), 1)
        self.assertLessEqual(report.speech_regions[0].start_sample, 2_000)
        self.assertGreaterEqual(report.speech_regions[0].end_sample, 3_000)
        matching = [
            candidate
            for candidate in report.candidates
            if candidate.start_sample <= 2_100 < candidate.end_sample
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].supporting_input_indices, (1,))
        self.assertEqual(matching[0].status, "single_input")

    def test_overlapping_speech_keeps_corroborating_inputs(self) -> None:
        first = np.zeros(5_000, dtype=np.float32)
        second = np.zeros(5_000, dtype=np.float32)
        add_tone(first, 1_000, 2_500)
        add_tone(second, 2_000, 3_500, frequency=180.0)

        report = analyze_aligned_speech(
            (analysis_signal(first), analysis_signal(second)),
            config=config(),
        )

        self.assertEqual(len(report.speech_regions), 1)
        corroborated = [
            candidate
            for candidate in report.candidates
            if candidate.status == "corroborated"
        ]
        self.assertTrue(corroborated)
        self.assertEqual(corroborated[0].supporting_input_indices, (0, 1))

    def test_invalid_high_energy_is_unavailable_not_silence_or_speech(self) -> None:
        high_energy = np.full(6_000, 0.8, dtype=np.float32)
        mask = np.ones(6_000, dtype=np.bool_)
        mask[:3_000] = False
        high_energy[3_000:] = 0.0
        silence = np.zeros(6_000, dtype=np.float32)

        report = analyze_aligned_speech(
            (
                analysis_signal(high_energy, valid_mask=mask),
                analysis_signal(silence),
            ),
            config=config(),
        )

        self.assertEqual(report.inputs[0].activity_regions, ())
        self.assertEqual(report.speech_regions, ())
        first_window = report.windows[0]
        self.assertEqual(first_window.region.start_sample, 0)
        self.assertEqual(first_window.region.end_sample, 3_000)
        self.assertEqual(first_window.evidence_for_input(0).status, "unavailable")
        self.assertEqual(first_window.evidence_for_input(1).status, "no_speech")

    def test_default_and_custom_feature_window_boundaries(self) -> None:
        signals = (
            analysis_signal(np.zeros(6_000, dtype=np.float32)),
            analysis_signal(np.zeros(6_000, dtype=np.float32)),
        )

        default_report = analyze_aligned_speech(signals, config=config())
        custom_report = analyze_aligned_speech(
            signals,
            config=config(window_seconds=2.0, hop_seconds=1.0),
        )

        self.assertEqual(
            [(window.start_sample, window.end_sample) for window in default_report.windows],
            [(0, 3_000), (1_000, 4_000), (2_000, 5_000), (3_000, 6_000)],
        )
        self.assertEqual(
            [(window.start_sample, window.end_sample) for window in custom_report.windows],
            [
                (0, 2_000),
                (1_000, 3_000),
                (2_000, 4_000),
                (3_000, 5_000),
                (4_000, 6_000),
            ],
        )

    def test_non_grid_tail_keeps_the_configured_hop(self) -> None:
        signals = (
            analysis_signal(np.zeros(6_500, dtype=np.float32)),
            analysis_signal(np.zeros(6_500, dtype=np.float32)),
        )

        report = analyze_aligned_speech(signals, config=config())

        self.assertEqual(
            (report.windows[-1].start_sample, report.windows[-1].end_sample),
            (4_000, 6_500),
        )

    def test_stationary_broadband_noise_is_not_confirmed_as_speech(self) -> None:
        rng = np.random.default_rng(207)
        noise = rng.normal(0.0, 0.12, 6_000).astype(np.float32)
        silence = np.zeros(6_000, dtype=np.float32)

        report = analyze_aligned_speech(
            (analysis_signal(noise), analysis_signal(silence)),
            config=config(),
        )

        self.assertTrue(report.inputs[0].activity_regions)
        self.assertEqual(report.inputs[0].speech_regions, ())
        self.assertTrue(report.inputs[0].noise_suspected_regions)
        self.assertEqual(report.speech_regions, ())
        self.assertTrue(report.uncertain_regions)
        self.assertTrue(report.candidates)
        self.assertTrue(
            all(candidate.status == "noise_risk" for candidate in report.candidates)
        )
        diagnostic = report.inputs[0].region_diagnostics[0]
        self.assertEqual(diagnostic.decision_code, "stationary_broadband_noise")
        self.assertIsNotNone(diagnostic.rms_dbfs)
        self.assertIsNotNone(diagnostic.spectral_flatness)
        json.dumps(report.to_dict(), allow_nan=False)

    def test_constant_and_tonal_noise_remain_uncertain_candidates(self) -> None:
        sample_count = 4_000
        time = np.arange(sample_count, dtype=np.float64) / 1_000
        cases = (
            (
                "constant_signal",
                np.full(sample_count, 0.2, dtype=np.float32),
            ),
            (
                "stationary_tonal_activity",
                (0.2 * np.sin(2.0 * np.pi * 60.0 * time)).astype(np.float32),
            ),
        )
        for expected_code, noisy_values in cases:
            with self.subTest(expected_code=expected_code):
                report = analyze_aligned_speech(
                    (
                        analysis_signal(noisy_values),
                        analysis_signal(np.zeros(sample_count, dtype=np.float32)),
                    ),
                    config=config(),
                )

                self.assertTrue(report.union_regions)
                self.assertEqual(report.confirmed_regions, ())
                self.assertTrue(report.uncertain_regions)
                self.assertTrue(report.windows[0].has_speech_candidate)
                self.assertFalse(report.windows[0].has_likely_speech)
                self.assertEqual(
                    report.inputs[0].region_diagnostics[0].decision_code,
                    expected_code,
                )

    def test_candidate_is_split_when_input_availability_changes(self) -> None:
        speech = np.zeros(4_000, dtype=np.float32)
        add_tone(speech, 0, 3_000)
        partial_mask = np.zeros(4_000, dtype=np.bool_)
        partial_mask[:1_000] = True

        report = analyze_aligned_speech(
            (
                analysis_signal(speech),
                analysis_signal(
                    np.zeros(4_000, dtype=np.float32),
                    valid_mask=partial_mask,
                ),
            ),
            config=config(),
        )

        before_boundary = next(
            candidate
            for candidate in report.candidates
            if candidate.start_sample <= 500 < candidate.end_sample
        )
        after_boundary = next(
            candidate
            for candidate in report.candidates
            if candidate.start_sample <= 1_500 < candidate.end_sample
        )
        self.assertEqual(before_boundary.valid_input_indices, (0, 1))
        self.assertEqual(after_boundary.valid_input_indices, (0,))
        self.assertEqual(len(report.union_regions), 1)

    def test_prepared_input_regions_are_on_reference_timeline(self) -> None:
        sample_rate = 1_000
        reference = analysis_signal(np.zeros(6_000, dtype=np.float32))
        target_values = np.zeros(4_000, dtype=np.float32)
        add_tone(target_values, 1_000, 2_000)
        target = analysis_signal(target_values)
        prepared = PreparedMultiAudio(
            inputs=(
                PreparedAudioInput(metadata(0, reference), reference),
                PreparedAudioInput(metadata(1, target), target),
            ),
            warnings=(),
            analysis_sample_rate=sample_rate,
        )
        alignment = MultiAudioAlignment(
            reference_index=0,
            sample_rate=sample_rate,
            inputs=(
                successful_alignment(0, 0, 0, sample_rate),
                successful_alignment(1, 0, 1_000, sample_rate),
            ),
            reference_windows=((0, 500),),
        )

        report = analyze_prepared_speech(
            prepared,
            alignment,
            config=config(),
        )

        self.assertEqual(report.timeline_sample_count, 6_000)
        self.assertEqual(len(report.speech_regions), 1)
        self.assertLessEqual(report.speech_regions[0].start_sample, 2_000)
        self.assertGreaterEqual(report.speech_regions[0].end_sample, 3_000)

    def test_prepared_analysis_rejects_a_non_identity_reference(self) -> None:
        sample_rate = 1_000
        reference = analysis_signal(np.zeros(2_000, dtype=np.float32))
        target = analysis_signal(np.zeros(2_000, dtype=np.float32))
        prepared = PreparedMultiAudio(
            inputs=(
                PreparedAudioInput(metadata(0, reference), reference),
                PreparedAudioInput(metadata(1, target), target),
            ),
            warnings=(),
            analysis_sample_rate=sample_rate,
        )
        reference_alignment = replace(
            successful_alignment(0, 0, 0, sample_rate),
            offset_samples=10,
            offset_ms=10.0,
        )
        alignment = MultiAudioAlignment(
            reference_index=0,
            sample_rate=sample_rate,
            inputs=(
                reference_alignment,
                successful_alignment(1, 0, 0, sample_rate),
            ),
            reference_windows=((0, 500),),
        )

        with self.assertRaisesRegex(ValueError, "identity transform"):
            analyze_prepared_speech(prepared, alignment, config=config())

    def test_invalid_config_and_unaligned_inputs_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SpeechAnalysisConfig(window_seconds=1.0, hop_seconds=2.0)
        with self.assertRaises(ValueError):
            SpeechAnalysisConfig(window_seconds=float("nan"))
        signals = (
            analysis_signal(np.zeros(1_000, dtype=np.float32)),
            analysis_signal(np.zeros(1_000, dtype=np.float32)),
        )
        with self.assertRaises(ValueError):
            analyze_aligned_speech(
                signals,
                config=SpeechAnalysisConfig(
                    window_seconds=1.0e308,
                    hop_seconds=1.0e308,
                ),
            )
        with self.assertRaises(ValueError):
            analyze_aligned_speech(
                (
                    analysis_signal(np.zeros(1_000, dtype=np.float32)),
                    analysis_signal(np.zeros(900, dtype=np.float32)),
                )
            )

    def test_numpy_sample_rate_serializes_as_strict_json(self) -> None:
        sample_rate = np.int64(1_000)
        signals = (
            analysis_signal(
                np.zeros(1_000, dtype=np.float32),
                sample_rate=sample_rate,
            ),
            analysis_signal(
                np.zeros(1_000, dtype=np.float32),
                sample_rate=sample_rate,
            ),
        )

        report = analyze_aligned_speech(signals, config=config())

        json.dumps(report.to_dict(), allow_nan=False)


if __name__ == "__main__":
    unittest.main()
