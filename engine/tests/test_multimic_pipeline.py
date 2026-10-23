from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from multimic.pipeline import (
    ANALYSIS_SAMPLE_RATE,
    AnalysisSignal,
    MultiAudioPreparationError,
    SUPPORTED_AUDIO_EXTENSIONS,
    build_validity_mask,
    fingerprint_file,
    place_signal_on_timeline,
    prepare_multi_audio_inputs,
)
from test_audio_metadata import write_wave_file


class MultiAudioPipelineTests(unittest.TestCase):
    def test_mixed_sample_rates_and_channels_become_16k_mono(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mono_path = root / "mono_8k.wav"
            stereo_path = root / "stereo_48k.wav"
            surround_path = root / "surround_44k.wav"
            write_wave_file(mono_path, sample_rate=8_000, channels=1)
            write_wave_file(stereo_path, sample_rate=48_000, channels=2)
            write_wave_file(surround_path, sample_rate=44_100, channels=4)
            fingerprints_before = {
                path: fingerprint_file(path)
                for path in (mono_path, stereo_path, surround_path)
            }

            prepared = prepare_multi_audio_inputs(
                (mono_path, stereo_path, surround_path)
            )

            self.assertEqual(len(prepared), 3)
            self.assertEqual(prepared.analysis_sample_rate, ANALYSIS_SAMPLE_RATE)
            self.assertEqual(prepared.warnings, ())
            for item in prepared:
                self.assertEqual(item.sample_rate, ANALYSIS_SAMPLE_RATE)
                self.assertEqual(item.samples.dtype, np.float32)
                self.assertEqual(item.samples.ndim, 1)
                self.assertTrue(item.samples.flags.c_contiguous)
                self.assertEqual(item.valid_mask.dtype, np.bool_)
                self.assertEqual(item.valid_mask.shape, item.samples.shape)
                self.assertTrue(item.valid_mask.all())
                self.assertEqual(item.analysis.valid_intervals(), ((0, item.samples.size),))

            self.assertEqual(prepared.inputs[0].metadata.sample_rate, 8_000)
            self.assertEqual(prepared.inputs[0].metadata.channels, 1)
            self.assertEqual(
                prepared.inputs[0].metadata.downmix_method,
                "none_already_mono",
            )
            self.assertEqual(prepared.inputs[1].metadata.sample_rate, 48_000)
            self.assertEqual(prepared.inputs[1].metadata.channels, 2)
            self.assertEqual(
                prepared.inputs[1].metadata.downmix_method,
                "ffmpeg_libswresample_default_downmix",
            )
            self.assertEqual(prepared.inputs[2].metadata.sample_rate, 44_100)
            self.assertEqual(prepared.inputs[2].metadata.channels, 4)
            self.assertEqual(
                prepared.inputs[2].metadata.downmix_method,
                "ffmpeg_libswresample_default_downmix",
            )

            for item in prepared:
                before = fingerprints_before[item.metadata.path]
                self.assertEqual(item.metadata.size_bytes, before.size_bytes)
                self.assertEqual(item.metadata.sha256, before.sha256)
                self.assertEqual(fingerprint_file(item.metadata.path), before)

    def test_fewer_than_two_inputs_is_rejected(self) -> None:
        with self.assertRaises(MultiAudioPreparationError) as raised:
            prepare_multi_audio_inputs(("only.wav",))

        self.assertEqual(len(raised.exception.failures), 1)
        self.assertEqual(raised.exception.failures[0].code, "insufficient_inputs")

    def test_duplicate_source_does_not_satisfy_minimum_input_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "one_microphone.wav"
            write_wave_file(path, sample_rate=16_000, channels=1)

            with self.assertRaises(MultiAudioPreparationError) as raised:
                prepare_multi_audio_inputs((path, path))

        self.assertEqual(raised.exception.file_errors[0].input_index, 1)
        self.assertEqual(raised.exception.file_errors[0].code, "duplicate_input")
        self.assertEqual(len(raised.exception.partial_inputs), 1)

    def test_collects_each_file_failure_and_keeps_partial_successes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "valid.wav"
            missing = root / "missing.wav"
            corrupt = root / "corrupt.mp3"
            write_wave_file(valid, sample_rate=16_000, channels=1)
            corrupt.write_bytes(b"not an audio stream")

            with self.assertRaises(MultiAudioPreparationError) as raised:
                prepare_multi_audio_inputs((valid, missing, corrupt))

        error = raised.exception
        self.assertEqual([failure.input_index for failure in error.file_errors], [1, 2])
        self.assertEqual(
            [failure.code for failure in error.file_errors],
            ["not_found", "open_failed"],
        )
        self.assertEqual(len(error.partial_inputs), 1)
        self.assertEqual(error.partial_inputs[0].metadata.input_index, 0)

    def test_unsupported_extension_is_rejected_even_when_contents_decode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            disguised = root / "audio.txt"
            valid = root / "audio.wav"
            write_wave_file(disguised, sample_rate=16_000, channels=1)
            write_wave_file(valid, sample_rate=16_000, channels=1)

            with self.assertRaises(MultiAudioPreparationError) as raised:
                prepare_multi_audio_inputs((disguised, valid))

        self.assertEqual(raised.exception.file_errors[0].code, "unsupported_extension")
        self.assertEqual(len(raised.exception.partial_inputs), 1)

    def test_more_than_recommended_count_warns_but_is_processed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for index in range(9):
                path = root / f"input_{index}.wav"
                write_wave_file(
                    path,
                    sample_rate=8_000 + index * 1_000,
                    channels=1,
                    duration_seconds=0.01,
                )
                paths.append(path)

            prepared = prepare_multi_audio_inputs(paths)

        self.assertEqual(len(prepared), 9)
        self.assertEqual(len(prepared.warnings), 1)
        self.assertEqual(
            prepared.warnings[0].code,
            "recommended_input_limit_exceeded",
        )

    def test_recommended_count_warning_is_preserved_when_a_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for index in range(8):
                path = root / f"valid_{index}.wav"
                write_wave_file(path, sample_rate=16_000, channels=1, duration_seconds=0.01)
                paths.append(path)
            paths.append(root / "missing.wav")

            with self.assertRaises(MultiAudioPreparationError) as raised:
                prepare_multi_audio_inputs(paths)

        self.assertEqual(len(raised.exception.warnings), 1)
        self.assertEqual(
            raised.exception.warnings[0].code,
            "recommended_input_limit_exceeded",
        )

    def test_supported_extension_contract_is_case_insensitive(self) -> None:
        self.assertEqual(
            SUPPORTED_AUDIO_EXTENSIONS,
            {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".mp4"},
        )


class ValidityMaskTests(unittest.TestCase):
    def test_valid_bounds_are_distinct_from_zero_valued_audio(self) -> None:
        samples = np.array([7.0, 0.0, 9.0, 0.0], dtype=np.float32)
        signal = AnalysisSignal(
            samples=samples,
            valid_mask=build_validity_mask(4, valid_start_sample=1, valid_end_sample=3),
            sample_rate=16_000,
        )

        self.assertEqual(signal.valid_intervals(), ((1, 3),))
        np.testing.assert_array_equal(signal.valid_samples(), [0.0, 9.0])

    def test_timeline_padding_is_never_marked_as_recorded_silence(self) -> None:
        source = AnalysisSignal(
            samples=np.array([0.0, 1.0, 2.0], dtype=np.float32),
            valid_mask=np.ones(3, dtype=np.bool_),
            sample_rate=16_000,
        )

        placed = place_signal_on_timeline(
            source,
            start_sample=2,
            timeline_sample_count=7,
        )

        np.testing.assert_array_equal(
            placed.samples,
            np.array([0.0, 0.0, 0.0, 1.0, 2.0, 0.0, 0.0], dtype=np.float32),
        )
        np.testing.assert_array_equal(
            placed.valid_mask,
            np.array([False, False, True, True, True, False, False]),
        )
        np.testing.assert_array_equal(
            placed.samples[placed.valid_mask],
            source.samples,
        )

    def test_negative_start_crops_source_and_preserves_its_mask(self) -> None:
        source = AnalysisSignal(
            samples=np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32),
            valid_mask=np.array([True, False, True, True]),
            sample_rate=16_000,
        )

        placed = place_signal_on_timeline(
            source,
            start_sample=-1,
            timeline_sample_count=3,
        )

        np.testing.assert_array_equal(placed.samples, [2.0, 3.0, 4.0])
        np.testing.assert_array_equal(placed.valid_mask, [False, True, True])


if __name__ == "__main__":
    unittest.main()
