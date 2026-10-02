from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import numpy as np


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from transcribe_npu import remove_pathological_repetition, transcribe_windows


class TranscriptRepetitionTests(unittest.TestCase):
    def test_discards_a_repeated_single_token_decoder_loop(self) -> None:
        text = ', '.join(['BMS'] * 40)

        self.assertEqual(remove_pathological_repetition(text), '')

    def test_discards_a_repeated_multi_token_decoder_loop(self) -> None:
        text = ', '.join(['뱀, 오류, 시간, 키엔'] * 12)

        self.assertEqual(remove_pathological_repetition(text), '')

    def test_preserves_an_ordinary_short_backchannel(self) -> None:
        self.assertEqual(remove_pathological_repetition('네, 네, 네'), '네, 네, 네')

    def test_preserves_text_before_a_repeated_suffix(self) -> None:
        text = '다음 단계로 진행하겠습니다. ' + ', '.join(['랩트'] * 24)

        self.assertEqual(remove_pathological_repetition(text), '다음 단계로 진행하겠습니다.')

    def test_transcription_omits_a_filtered_chunk(self) -> None:
        class FakePipe:
            @staticmethod
            def generate(_audio, _config):
                return SimpleNamespace(chunks=[SimpleNamespace(
                    text=', '.join(['BMS'] * 40),
                    start_ts=0.0,
                    end_ts=1.0,
                )])

        segments = transcribe_windows(
            FakePipe(),
            object(),
            np.zeros(16_000, dtype=np.float32),
            duration=1.0,
            window=30.0,
            overlap=0.0,
            console_progress=False,
        )

        self.assertEqual(segments, [])


if __name__ == '__main__':
    unittest.main()
