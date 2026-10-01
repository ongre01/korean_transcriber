from __future__ import annotations

from pathlib import Path
import sys
import unittest

import numpy as np


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from vad import detect_speech_regions


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


if __name__ == "__main__":
    unittest.main()
