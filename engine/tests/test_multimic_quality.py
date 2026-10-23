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
    QualityScoringConfig,
    SpeechAnalysisConfig,
    analyze_aligned_quality,
    analyze_aligned_speech,
    analyze_prepared_quality,
    analyze_prepared_speech,
    score_aligned_quality,
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


class MultiMicChannelQualityTests(unittest.TestCase):
    def test_local_broadband_noise_lowers_the_affected_channel_score(self) -> None:
        sample_count = 5_000
        clean = np.zeros(sample_count, dtype=np.float32)
        add_tone(clean, 250, 4_750, amplitude=0.24)
        noisy = clean.copy()
        rng = np.random.default_rng(808)
        noisy[2_000:3_000] += rng.normal(0.0, 0.10, 1_000).astype(
            np.float32
        )

        report = analyze_aligned_quality(
            (analysis_signal(noisy), analysis_signal(clean)),
            speech_config=config(window_seconds=1.0, hop_seconds=1.0),
        )

        window = next(item for item in report.windows if item.start_sample == 2_000)
        noisy_score = window.score_for_input(0)
        clean_score = window.score_for_input(1)
        self.assertIsNotNone(noisy_score.score)
        self.assertIsNotNone(clean_score.score)
        self.assertGreater(clean_score.score, noisy_score.score)
        self.assertIsNotNone(noisy_score.estimated_snr_db)
        self.assertIsNotNone(clean_score.estimated_snr_db)
        self.assertGreater(clean_score.estimated_snr_db, noisy_score.estimated_snr_db)

    def test_noise_only_during_speech_cannot_increase_estimated_snr(self) -> None:
        rng = np.random.default_rng(810)
        clean = rng.normal(0.0, 0.002, 6_000).astype(np.float32)
        add_tone(clean, 2_000, 3_000, amplitude=0.24)
        noisy = clean.copy()
        noisy[2_000:3_000] += rng.normal(0.0, 0.05, 1_000).astype(
            np.float32
        )

        report = analyze_aligned_quality(
            (analysis_signal(noisy), analysis_signal(clean)),
            speech_config=config(),
        )

        window = next(item for item in report.windows if item.start_sample == 1_000)
        noisy_score = window.score_for_input(0)
        clean_score = window.score_for_input(1)
        self.assertIsNotNone(noisy_score.estimated_snr_db)
        self.assertIsNotNone(clean_score.estimated_snr_db)
        self.assertGreater(clean_score.estimated_snr_db, noisy_score.estimated_snr_db)
        self.assertGreater(clean_score.score, noisy_score.score)

    def test_hard_clipping_has_an_explicit_penalty_and_lowers_score(self) -> None:
        clean = np.zeros(4_000, dtype=np.float32)
        add_tone(clean, 0, 4_000, amplitude=0.32)
        clipped = np.clip(clean * 5.0, -1.0, 1.0).astype(np.float32)

        report = analyze_aligned_quality(
            (analysis_signal(clean), analysis_signal(clipped)),
            speech_config=config(window_seconds=1.0, hop_seconds=1.0),
        )

        window = report.windows[1]
        normal_score = window.score_for_input(0)
        clipped_score = window.score_for_input(1)
        self.assertGreater(clipped_score.clipped_sample_count, 0)
        self.assertGreater(clipped_score.clipping_ratio, 0.0)
        self.assertGreater(clipped_score.clipping_penalty, 0.0)
        self.assertIn("clipping_suspected", clipped_score.diagnostic_codes)
        self.assertGreater(normal_score.score, clipped_score.score)

    def test_isolated_near_full_scale_peaks_are_not_called_clipping(self) -> None:
        values = np.zeros(2_000, dtype=np.float32)
        add_tone(values, 0, 2_000, amplitude=0.2)
        values[250] = 0.99
        values[1_250] = -0.99

        report = analyze_aligned_quality(
            (analysis_signal(values), analysis_signal(values)),
            speech_config=config(window_seconds=1.0, hop_seconds=1.0),
        )

        for window in report.windows:
            score = window.score_for_input(0)
            self.assertEqual(score.clipped_sample_count, 0)
            self.assertEqual(score.clipping_penalty, 0.0)

    def test_clipping_plateau_is_found_despite_a_higher_isolated_peak(self) -> None:
        values = np.zeros(2_000, dtype=np.float32)
        add_tone(values, 0, 2_000, amplitude=0.2)
        values[200:210] = 0.99
        values[700] = 1.0

        report = analyze_aligned_quality(
            (analysis_signal(values), analysis_signal(values)),
            speech_config=config(window_seconds=1.0, hop_seconds=1.0),
        )

        score = report.windows[0].score_for_input(0)
        self.assertEqual(score.clipped_sample_count, 10)
        self.assertGreater(score.clipping_penalty, 0.0)

    def test_muted_inputs_are_not_scored_when_speech_is_expected(self) -> None:
        speech = np.zeros(2_000, dtype=np.float32)
        add_tone(speech, 0, 2_000)
        silence = np.zeros(2_000, dtype=np.float32)
        too_quiet = np.full(2_000, 1.0e-5, dtype=np.float32)

        report = analyze_aligned_quality(
            (
                analysis_signal(speech),
                analysis_signal(silence),
                analysis_signal(too_quiet),
            ),
            speech_config=config(window_seconds=1.0, hop_seconds=1.0),
        )

        for window in report.windows:
            self.assertIsNotNone(window.score_for_input(0).score)
            for input_index in (1, 2):
                item = window.score_for_input(input_index)
                self.assertTrue(item.available)
                self.assertIsNone(item.score)
                self.assertIn("muted_or_too_quiet", item.diagnostic_codes)

    def test_clean_silence_outranks_noise_in_no_speech_window(self) -> None:
        rng = np.random.default_rng(811)
        noisy = np.zeros(3_000, dtype=np.float32)
        clean = np.zeros(3_000, dtype=np.float32)
        add_tone(noisy, 0, 750)
        add_tone(clean, 0, 750)
        noisy[2_000:3_000] = rng.normal(0.0, 0.08, 1_000).astype(
            np.float32
        )

        report = analyze_aligned_quality(
            (analysis_signal(noisy), analysis_signal(clean)),
            speech_config=config(window_seconds=1.0, hop_seconds=1.0),
        )

        window = next(item for item in report.windows if item.start_sample == 2_000)
        noisy_score = window.score_for_input(0)
        clean_score = window.score_for_input(1)
        self.assertFalse(window.has_likely_speech)
        self.assertIsNotNone(noisy_score.score)
        self.assertIsNotNone(clean_score.score)
        self.assertIn("noise_suspected", noisy_score.diagnostic_codes)
        self.assertGreater(clean_score.score, noisy_score.score)

    def test_input_without_valid_samples_never_receives_a_score(self) -> None:
        speech = np.zeros(3_000, dtype=np.float32)
        add_tone(speech, 0, 3_000)
        unavailable_mask = np.zeros(3_000, dtype=np.bool_)

        report = analyze_aligned_quality(
            (
                analysis_signal(speech, valid_mask=unavailable_mask),
                analysis_signal(speech),
            ),
            speech_config=config(window_seconds=1.0, hop_seconds=1.0),
        )

        for window in report.windows:
            missing = window.score_for_input(0)
            self.assertIsNone(missing.score)
            self.assertFalse(missing.scoreable)
            self.assertEqual(missing.diagnostic_codes, ("no_valid_audio",))
            self.assertIsNotNone(window.score_for_input(1).score)
        self.assertIsNone(report.input_summary(0).score)

    def test_input_summary_accounts_for_unscoreable_timeline_coverage(self) -> None:
        speech = np.zeros(3_000, dtype=np.float32)
        add_tone(speech, 0, 3_000)
        partial_mask = np.zeros(3_000, dtype=np.bool_)
        partial_mask[:1_000] = True

        report = analyze_aligned_quality(
            (
                analysis_signal(speech),
                analysis_signal(speech, valid_mask=partial_mask),
            ),
            speech_config=config(window_seconds=1.0, hop_seconds=1.0),
        )

        complete = report.input_summary(0)
        partial = report.input_summary(1)
        self.assertEqual(complete.score_coverage_ratio, 1.0)
        self.assertAlmostEqual(partial.score_coverage_ratio, 1.0 / 3.0)
        self.assertEqual(partial.unscoreable_window_count, 2)
        self.assertGreater(complete.score, partial.score)

    def test_internal_recording_gap_is_penalized_separately_from_padding(self) -> None:
        speech = np.zeros(3_000, dtype=np.float32)
        add_tone(speech, 0, 3_000)
        gap_mask = np.ones(3_000, dtype=np.bool_)
        gap_mask[900:1_100] = False

        report = analyze_aligned_quality(
            (
                analysis_signal(speech),
                analysis_signal(speech, valid_mask=gap_mask),
            ),
            speech_config=config(window_seconds=1.0, hop_seconds=1.0),
        )

        for start_sample in (0, 1_000):
            window = next(
                item
                for item in report.windows
                if item.start_sample == start_sample
            )
            intact = window.score_for_input(0)
            interrupted = window.score_for_input(1)
            self.assertEqual(interrupted.internal_gap_count, 1)
            self.assertEqual(interrupted.internal_gap_sample_count, 100)
            self.assertGreater(interrupted.invalid_penalty, 0.0)
            self.assertGreater(interrupted.gap_penalty, 0.0)
            self.assertIn(
                "recording_gap_detected",
                interrupted.diagnostic_codes,
            )
            self.assertGreater(intact.score, interrupted.score)

    def test_louder_but_clipped_noisy_input_does_not_win_by_rms(self) -> None:
        quiet_clean = np.zeros(3_000, dtype=np.float32)
        add_tone(quiet_clean, 0, 3_000, amplitude=0.12)
        rng = np.random.default_rng(809)
        loud_bad = np.clip(
            quiet_clean * 10.0
            + rng.normal(0.0, 0.20, quiet_clean.size),
            -1.0,
            1.0,
        ).astype(np.float32)

        report = analyze_aligned_quality(
            (analysis_signal(loud_bad), analysis_signal(quiet_clean)),
            speech_config=config(window_seconds=1.0, hop_seconds=1.0),
        )

        for window in report.windows:
            loud = window.score_for_input(0)
            quiet = window.score_for_input(1)
            self.assertGreater(loud.rms_dbfs, quiet.rms_dbfs)
            self.assertGreater(quiet.score, loud.score)

    def test_quality_configuration_and_report_are_strict_json(self) -> None:
        invalid_settings = (
            {"snr_bad_db": 10.0, "snr_good_db": 10.0},
            {"clipping_threshold": 0.0},
            {"validity_weight": 0.0, "speech_weight": 0.0, "snr_weight": 0.0},
            {"noise_penalty_weight": float("nan")},
        )
        for values in invalid_settings:
            with self.subTest(values=values), self.assertRaises(ValueError):
                QualityScoringConfig(**values)

        speech = np.zeros(2_000, dtype=np.float32)
        add_tone(speech, 0, 2_000)
        report = analyze_aligned_quality(
            (analysis_signal(speech), analysis_signal(speech)),
            speech_config=config(window_seconds=1.0, hop_seconds=1.0),
        )

        payload = report.to_dict()
        json.dumps(payload, allow_nan=False)
        self.assertEqual(
            payload["speech_analysis"]["speech_regions"],
            [region.to_dict() for region in report.speech_report.speech_regions],
        )
        self.assertEqual(
            QualityScoringConfig(**payload["config"]),
            report.config,
        )
        for window in report.windows:
            for item in window.inputs:
                self.assertTrue(item.score is None or 0.0 <= item.score <= 1.0)
                self.assertTrue(
                    item.snr_score is None or 0.0 <= item.snr_score <= 1.0
                )

    def test_precomputed_speech_report_is_validated_and_preserved(self) -> None:
        speech = np.zeros(2_000, dtype=np.float32)
        add_tone(speech, 0, 2_000)
        signals = (analysis_signal(speech), analysis_signal(speech))
        speech_report = analyze_aligned_speech(
            signals,
            config=config(window_seconds=1.0, hop_seconds=1.0),
        )

        report = score_aligned_quality(signals, speech_report)

        self.assertIs(report.speech_report, speech_report)
        with self.assertRaisesRegex(TypeError, "SpeechActivityReport"):
            score_aligned_quality(signals, None)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "timeline length"):
            score_aligned_quality(
                signals,
                replace(speech_report, timeline_sample_count=1_999),
            )

        changed_validity = np.ones(2_000, dtype=np.bool_)
        changed_validity[750:1_250] = False
        stale_signals = (
            signals[0],
            analysis_signal(speech, valid_mask=changed_validity),
        )
        with self.assertRaisesRegex(ValueError, "validity does not match"):
            score_aligned_quality(stale_signals, speech_report)

    def test_prepared_quality_uses_the_reference_timeline(self) -> None:
        sample_rate = 1_000
        reference_values = np.zeros(3_000, dtype=np.float32)
        target_values = np.zeros(1_000, dtype=np.float32)
        add_tone(reference_values, 1_000, 2_000)
        add_tone(target_values, 0, 1_000)
        reference = analysis_signal(reference_values)
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

        report = analyze_prepared_quality(
            prepared,
            alignment,
            speech_config=config(window_seconds=1.0, hop_seconds=1.0),
        )

        self.assertEqual(report.timeline_sample_count, 3_000)
        middle = next(item for item in report.windows if item.start_sample == 1_000)
        self.assertIsNotNone(middle.score_for_input(0).score)
        self.assertIsNotNone(middle.score_for_input(1).score)
        final = next(item for item in report.windows if item.start_sample == 2_000)
        unavailable = final.score_for_input(1)
        self.assertIsNone(unavailable.score)
        self.assertEqual(unavailable.valid_sample_count, 0)
        self.assertEqual(unavailable.diagnostic_codes, ("no_valid_audio",))


class MultiMicSpeechAnalysisAdditionalTests(unittest.TestCase):
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
