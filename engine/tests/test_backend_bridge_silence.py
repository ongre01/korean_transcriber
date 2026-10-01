from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest

import numpy as np


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import backend_bridge
from backend_protocol import StateEvent, StateValue, parse_event_line


class FakeEngine:
    TARGET_SAMPLE_RATE = 1_000

    def __init__(self, audio: np.ndarray) -> None:
        self.audio = audio
        self.transcribed_durations: list[float] = []
        self.model_load_count = 0

    @staticmethod
    def read_optional_text(_path: Path) -> str:
        return ""

    @staticmethod
    def available_devices() -> list[str]:
        return ["CPU"]

    @staticmethod
    def ensure_device(_device: str) -> list[str]:
        return ["CPU"]

    def decode_audio_16k_mono(self, _path: Path) -> np.ndarray:
        return self.audio

    def load_whisper_pipeline(self, *_args, **_kwargs):
        self.model_load_count += 1
        return object()

    @staticmethod
    def prepare_initial_prompt(_pipe, _initial_prompt: str):
        return SimpleNamespace(value="", warning=None)

    @staticmethod
    def configure_generation(_pipe, *_args, **_kwargs):
        return object()

    def transcribe_windows(self, _pipe, _config, _audio, duration, _window, _overlap,
                           *, progress_callback, console_progress):
        if console_progress:
            raise AssertionError("console progress must be disabled in the bridge")
        self.transcribed_durations.append(duration)
        progress_callback(duration, duration, 1, 1)
        return [SimpleNamespace(start=0.0, end=duration, text="발화", speaker=None)]

    @staticmethod
    def save_outputs(input_path, _segments, _duration, _suffix, *, diarized, output_dir):
        del diarized
        text_path = output_dir / f"{input_path.stem}.txt"
        srt_path = output_dir / f"{input_path.stem}.srt"
        text_path.write_text("발화\n", encoding="utf-8")
        srt_path.write_text("", encoding="utf-8")
        return text_path, srt_path


class BackendBridgeSilenceTests(unittest.TestCase):
    def test_vad_transcribes_only_speech_and_restores_timestamps(self) -> None:
        audio = np.zeros(3_000, dtype=np.float32)
        audio[500:900] = 0.1
        audio[1_900:2_300] = 0.1
        engine = FakeEngine(audio)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.wav"
            model_dir = root / "model"
            output_dir = root / "output"
            input_path.write_bytes(b"audio")
            model_dir.mkdir()
            output_dir.mkdir()
            args = backend_bridge.parse_arguments([
                "--input", str(input_path),
                "--model-dir", str(model_dir),
                "--output-dir", str(output_dir),
                "--device", "CPU",
                "--skip-silence",
                "--silence-threshold-db", "-40",
                "--silence-min-speech-duration", "0.3",
                "--silence-min-duration", "0.5",
                "--silence-padding-duration", "0.1",
            ])
            output = StringIO()
            result = backend_bridge.run_bridge(args, engine, backend_bridge.EventWriter(output))

        self.assertEqual(result, 0)
        self.assertEqual(len(engine.transcribed_durations), 2)
        self.assertLess(sum(engine.transcribed_durations), len(audio) / engine.TARGET_SAMPLE_RATE)
        events = [parse_event_line(line) for line in output.getvalue().splitlines()]
        self.assertIn(StateEvent(StateValue.DETECTING_SPEECH), events)
        segment_events = [event for event in events if event.__class__.__name__ == "SegmentEvent"]
        self.assertEqual(len(segment_events), 2)
        self.assertGreater(segment_events[0].start, 0.0)
        self.assertGreater(segment_events[1].start, segment_events[0].end)

    def test_silence_only_input_skips_model_and_diarization(self) -> None:
        engine = FakeEngine(np.zeros(1_000, dtype=np.float32))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "silence.wav"
            model_dir = root / "model"
            output_dir = root / "output"
            input_path.write_bytes(b"audio")
            model_dir.mkdir()
            output_dir.mkdir()
            args = backend_bridge.parse_arguments([
                "--input", str(input_path),
                "--model-dir", str(model_dir),
                "--output-dir", str(output_dir),
                "--device", "CPU",
                "--skip-silence",
            ])
            output = StringIO()
            result = backend_bridge.run_bridge(args, engine, backend_bridge.EventWriter(output))

        self.assertEqual(result, 0)
        self.assertEqual(engine.model_load_count, 0)
        self.assertEqual(engine.transcribed_durations, [])
        self.assertIn("음성 구간이 감지되지 않아", output.getvalue())


if __name__ == "__main__":
    unittest.main()
