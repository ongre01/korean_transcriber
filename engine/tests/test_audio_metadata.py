from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave

import numpy as np


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from audio_metadata import MetadataFailure, inspect_audio


def write_wave_file(
    path: Path,
    *,
    sample_rate: int,
    channels: int,
    duration_seconds: float = 0.08,
) -> None:
    sample_count = max(1, round(sample_rate * duration_seconds))
    time = np.arange(sample_count, dtype=np.float64) / sample_rate
    mono = np.sin(2.0 * np.pi * 440.0 * time) * 0.25
    if channels == 1:
        audio = mono[:, np.newaxis]
    else:
        audio = np.stack(
            [mono * (1.0 - channel * 0.1) for channel in range(channels)],
            axis=1,
        )
    pcm = np.rint(audio * 32767.0).astype("<i2")

    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())


class AudioMetadataTests(unittest.TestCase):
    def test_reports_decoded_sample_rate_and_channel_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stereo_48k.wav"
            write_wave_file(path, sample_rate=48_000, channels=2)

            payload = inspect_audio(path)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["sample_rate"], 48_000)
        self.assertEqual(payload["channels"], 2)
        self.assertIn(payload["channel_layout"], {"stereo", "2 channels"})
        self.assertEqual(payload["codec"], "pcm_s16le")
        self.assertGreater(payload["duration_seconds"], 0.0)
        self.assertGreater(payload["decoded_duration_seconds"], 0.0)

    def test_missing_file_has_stable_error_code(self) -> None:
        missing = Path("definitely_missing_audio.wav")
        with self.assertRaises(MetadataFailure) as raised:
            inspect_audio(missing)
        self.assertEqual(raised.exception.code, "not_found")

    def test_cli_error_is_one_json_line_with_path_and_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.wav"
            result = subprocess.run(
                [sys.executable, str(ENGINE_DIR / "audio_metadata.py"), "--input", str(missing)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

        self.assertEqual(result.returncode, 1)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "not_found")
        self.assertEqual(Path(payload["path"]), missing.resolve())


if __name__ == "__main__":
    unittest.main()
