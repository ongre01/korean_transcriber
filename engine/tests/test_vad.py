from __future__ import annotations

from pathlib import Path
import sys
import unittest

import numpy as np


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from vad import SpeechRegion, detect_speech_regions, merge_speech_regions


class SpeechDetectionTests(unittest.TestCase):
    sample_rate = 1_000

    def detect(self, audio: np.ndarray, **overrides):
        options = {
            "threshold_db": -40.0,
            "min_speech_duration": 0.3,
            "min_silence_duration": 0.5,
            "padding_duration": 0.1,
        }
        options.update(overrides)
        return detect_speech_regions(audio, self.sample_rate, **options)

    def test_silence_produces_no_regions(self) -> None:
        self.assertEqual(self.detect(np.zeros(self.sample_rate, dtype=np.float32)), [])

    def test_regions_keep_source_sample_positions(self) -> None:
        audio = np.zeros(3_000, dtype=np.float32)
        audio[500:900] = 0.1
        audio[1_900:2_300] = 0.1

        regions = self.detect(audio)

        self.assertEqual(len(regions), 2)
        self.assertLessEqual(regions[0].start_sample, 500)
        self.assertGreaterEqual(regions[0].end_sample, 900)
        self.assertLessEqual(regions[1].start_sample, 1_900)
        self.assertGreaterEqual(regions[1].end_sample, 2_300)
        self.assertLess(regions[0].end_sample, regions[1].start_sample)

    def test_short_silence_is_merged_before_padding(self) -> None:
        audio = np.zeros(2_000, dtype=np.float32)
        audio[300:700] = 0.1
        audio[900:1_300] = 0.1

        regions = self.detect(audio, min_silence_duration=0.3, padding_duration=0.0)

        self.assertEqual(len(regions), 1)
        self.assertLessEqual(regions[0].start_sample, 300)
        self.assertGreaterEqual(regions[0].end_sample, 1_300)

    def test_too_short_speech_is_removed(self) -> None:
        audio = np.zeros(1_000, dtype=np.float32)
        audio[400:550] = 0.1

        self.assertEqual(self.detect(audio), [])

    def test_invalid_samples_are_not_treated_as_recorded_silence_or_speech(self) -> None:
        audio = np.full(2_000, 0.5, dtype=np.float32)
        valid_mask = np.zeros(audio.size, dtype=np.bool_)

        regions = self.detect(audio, valid_mask=valid_mask)

        self.assertEqual(regions, [])

    def test_invalid_gap_is_not_bridged_and_padding_stays_inside_valid_audio(self) -> None:
        audio = np.zeros(2_000, dtype=np.float32)
        valid_mask = np.ones(audio.size, dtype=np.bool_)
        valid_mask[900:1_100] = False
        audio[700:900] = 0.1
        audio[1_100:1_300] = 0.1

        regions = self.detect(
            audio,
            valid_mask=valid_mask,
            min_speech_duration=0.1,
            min_silence_duration=1.0,
            padding_duration=0.2,
        )

        self.assertEqual(len(regions), 2)
        self.assertLessEqual(regions[0].end_sample, 900)
        self.assertGreaterEqual(regions[1].start_sample, 1_100)

    def test_public_region_union_sorts_and_merges_touching_bounds(self) -> None:
        self.assertEqual(
            merge_speech_regions(
                (
                    SpeechRegion(20, 30),
                    SpeechRegion(0, 10),
                    SpeechRegion(10, 15),
                )
            ),
            [SpeechRegion(0, 15), SpeechRegion(20, 30)],
        )

    def test_region_bounds_must_be_non_negative_integers(self) -> None:
        for start, end in ((0.5, 2), (False, 2), (2, 1), (-1, 2)):
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValueError):
                    SpeechRegion(start, end)


if __name__ == "__main__":
    unittest.main()
