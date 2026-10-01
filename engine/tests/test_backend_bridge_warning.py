from __future__ import annotations

import argparse
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from backend_bridge import EventWriter, run_bridge
from backend_protocol import WarningEvent, parse_event_line


class FakeEngine:
    TARGET_SAMPLE_RATE = 16000

    def __init__(self) -> None:
        self.configure_arguments = None

    def available_devices(self):
        return ["NPU"]

    def ensure_device(self, device: str):
        assert device == "NPU"
        return self.available_devices()

    def read_optional_text(self, path: Path) -> str:
        return "회의 용어" if path.name == "initial_prompt.txt" else ""

    def decode_audio_16k_mono(self, _input_path: Path):
        return [0.0] * self.TARGET_SAMPLE_RATE

    def load_whisper_pipeline(self, _model_dir: Path, _device: str, _label: str):
        return object()

    def prepare_initial_prompt(self, _pipe, _prompt: str):
        return SimpleNamespace(warning="초기 프롬프트를 안전하게 제외했습니다.")

    def configure_generation(self, *arguments):
        self.configure_arguments = arguments
        return object()

    def transcribe_windows(self, _pipe, _config, _audio, duration, _window, _overlap,
                           *, progress_callback, console_progress):
        self.assert_console_progress_disabled(console_progress)
        progress_callback(duration, duration, 1, 1)
        return [SimpleNamespace(start=0.0, end=duration, text="테스트", speaker=None)]

    def assign_speakers(self, _segments, _turns):
        raise AssertionError("diarization must not run")

    def save_outputs(self, input_path, _segments, _duration, _suffix, *, diarized, output_dir):
        assert not diarized
        text_path = output_dir / f"{input_path.stem}.txt"
        srt_path = output_dir / f"{input_path.stem}.srt"
        text_path.write_text("테스트\n", encoding="utf-8")
        srt_path.write_text("1\n", encoding="utf-8")
        return text_path, srt_path

    @staticmethod
    def assert_console_progress_disabled(console_progress: bool) -> None:
        assert not console_progress


class BackendBridgeWarningTests(unittest.TestCase):
    def test_bridge_emits_warning_and_continues_without_initial_prompt_argument(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_path = root / "input.wav"
            model_dir = root / "model"
            output_dir = root / "output"
            input_path.write_bytes(b"audio")
            model_dir.mkdir()
            output = io.StringIO()
            engine = FakeEngine()
            args = argparse.Namespace(
                input=str(input_path),
                model_dir=str(model_dir),
                output_dir=str(output_dir),
                device="NPU",
                model_label="",
                hotwords_file=str(root / "hotwords.txt"),
                initial_prompt_file=str(root / "initial_prompt.txt"),
                language="<|ko|>",
                beams=1,
                window_seconds=120.0,
                overlap_seconds=4.0,
                diarization=False,
                output_suffix="",
                num_speakers=-1,
                speaker_threshold=0.5,
                diarization_min_duration_on=0.3,
                diarization_min_duration_off=0.5,
                diarization_segmentation_model="",
                diarization_embedding_model="",
                diarization_device="NPU",
                diarization_no_fallback=False,
            )

            exit_code = run_bridge(args, engine, EventWriter(output))

        events = [parse_event_line(line) for line in output.getvalue().splitlines()]
        self.assertEqual(exit_code, 0)
        self.assertTrue(any(isinstance(event, WarningEvent) for event in events))
        self.assertEqual(len(engine.configure_arguments), 4)


if __name__ == "__main__":
    unittest.main()
