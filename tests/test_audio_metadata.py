from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave


ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
sys.path.insert(0, str(ENGINE))

import audio_metadata  # noqa: E402


def write_wav(path: Path, seconds: float = 0.2, sample_rate: int = 16000) -> None:
    sample_count = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\0\0" * sample_count)


def write_compressed_audio(path: Path, codec: str, sample_rate: int = 16000) -> None:
    import av
    import numpy as np

    output = av.open(str(path), "w")
    try:
        stream = output.add_stream(codec, rate=sample_rate)
        stream.layout = "mono"
        frame = av.AudioFrame.from_ndarray(
            np.zeros((1, sample_rate // 5), dtype=np.int16),
            format="s16",
            layout="mono",
        )
        frame.sample_rate = sample_rate
        for packet in stream.encode(frame):
            output.mux(packet)
        for packet in stream.encode(None):
            output.mux(packet)
    finally:
        output.close()


class AudioMetadataTest(unittest.TestCase):
    def test_wav_with_spaces_and_korean_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "회의 녹음 파일.wav"
            write_wav(path)

            metadata = audio_metadata.inspect_audio(path)

            self.assertTrue(metadata["ok"])
            self.assertEqual(metadata["path"], str(path.resolve()))
            self.assertEqual(metadata["file_name"], path.name)
            self.assertEqual(metadata["size_bytes"], path.stat().st_size)
            self.assertAlmostEqual(metadata["duration_seconds"], 0.2, places=2)

    def test_corrupt_file_returns_safe_json_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "corrupt.mp3"
            path.write_bytes(b"not an audio file")

            completed = subprocess.run(
                [sys.executable, str(ENGINE / "audio_metadata.py"), "--input", str(path)],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertNotEqual(completed.returncode, 0)
            payload = json.loads(completed.stdout)
            self.assertFalse(payload["ok"])
            self.assertTrue(payload["error"])

    def test_deleted_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "deleted.m4a"
            with self.assertRaises(audio_metadata.MetadataFailure):
                audio_metadata.inspect_audio(path)

    def test_mp3_and_m4a_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            for suffix, codec in (("mp3", "mp3"), ("m4a", "aac")):
                with self.subTest(suffix=suffix):
                    path = Path(temporary_directory) / f"fixture.{suffix}"
                    write_compressed_audio(path, codec)

                    metadata = audio_metadata.inspect_audio(path)

                    self.assertTrue(metadata["ok"])
                    self.assertGreater(metadata["duration_seconds"], 0.0)


if __name__ == "__main__":
    unittest.main()
