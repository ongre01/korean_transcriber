from __future__ import annotations

from io import StringIO
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
from unittest import mock


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import backend_bridge
from backend_bridge import EventWriter
from backend_protocol import (
    CompletedEvent,
    ErrorEvent,
    ProtocolError,
    parse_event_line,
    validate_stream,
)


class MinimalEngine:
    TARGET_SAMPLE_RATE = 16000

    def available_devices(self):
        return ["CPU"]

    def read_optional_text(self, _path: Path) -> str:
        return ""

    def decode_audio_16k_mono(self, _input_path: Path):
        return [0.0] * self.TARGET_SAMPLE_RATE

    def load_whisper_pipeline(self, *_arguments, **_keyword_arguments):
        return object()

    def prepare_initial_prompt(self, _pipe, prompt: str):
        return SimpleNamespace(value=prompt, warning=None)

    def configure_generation(self, *_arguments, **_keyword_arguments):
        return object()

    def transcribe_windows(self, _pipe, _config, _audio, duration, _window, _overlap,
                           *, progress_callback, console_progress):
        progress_callback(duration, duration, 1, 1)
        return [SimpleNamespace(start=0.0, end=duration, text="단일 입력", speaker=None)]

    def save_outputs(self, input_path, _segments, _duration, _suffix, *, diarized, output_dir):
        text_path = output_dir / f"{input_path.stem}.txt"
        srt_path = output_dir / f"{input_path.stem}.srt"
        text_path.write_text("단일 입력\n", encoding="utf-8")
        srt_path.write_text("1\n", encoding="utf-8")
        return text_path, srt_path


class EventWriterContractTests(unittest.TestCase):
    def test_contract_violations_are_rejected_before_writing(self) -> None:
        cases = {
            "unknown state": lambda writer: writer.state("aligning"),
            "progress range": lambda writer: writer.progress(101),
            "empty segment": lambda writer: writer.emit(
                "segment", start=0.0, end=1.0, speaker=None, text=" ",
            ),
            "missing srt": lambda writer: writer.emit("completed", text_file="C:/a.txt"),
        }
        for name, emit in cases.items():
            with self.subTest(name=name):
                output = StringIO()
                with self.assertRaises(ProtocolError):
                    emit(EventWriter(output))
                self.assertEqual(output.getvalue(), "")

    def test_non_json_numbers_are_rejected_before_writing(self) -> None:
        output = StringIO()

        with self.assertRaises(ValueError):
            EventWriter(output).progress(10, float("nan"), 1.0)

        self.assertEqual(output.getvalue(), "")

    def test_no_event_follows_a_terminal_event(self) -> None:
        output = StringIO()
        writer = EventWriter(output)
        writer.error("failed")

        for emit in (
            lambda: writer.state("preparing"),
            lambda: writer.warning("late"),
            lambda: writer.error("again"),
            lambda: writer.completed("C:/a.txt", "C:/a.srt"),
        ):
            with self.assertRaises(ProtocolError):
                emit()

        self.assertEqual(writer.terminal, "error")
        self.assertEqual(output.getvalue().splitlines(), ['{"type":"error","message":"failed"}'])

    def test_single_input_completed_has_no_multi_input_fields(self) -> None:
        output = StringIO()
        writer = EventWriter(output)
        writer.completed(Path("C:/Results/회의.txt"), Path("C:/Results/회의.srt"))

        event = parse_event_line(output.getvalue().strip())
        self.assertEqual(writer.terminal, "completed")
        self.assertEqual(
            event,
            CompletedEvent(str(Path("C:/Results/회의.txt")), str(Path("C:/Results/회의.srt"))),
        )
        self.assertFalse(event.is_multi_input)

    def test_multi_input_completed_serializes_all_artifacts(self) -> None:
        output = StringIO()
        directory = Path("C:/Results")
        EventWriter(output).completed(
            directory / "회의.txt",
            directory / "회의.srt",
            merged_audio_file=directory / "merged_16k_mono.wav",
            alignment_file=directory / "alignment.json",
            selection_file=directory / "channel_selection.json",
        )

        event = parse_event_line(output.getvalue().strip())
        self.assertTrue(event.is_multi_input)
        self.assertEqual(event.text_file, str(directory / "회의.txt"))
        self.assertEqual(event.merged_audio_file, str(directory / "merged_16k_mono.wav"))
        self.assertEqual(event.alignment_file, str(directory / "alignment.json"))
        self.assertEqual(event.selection_file, str(directory / "channel_selection.json"))

    def test_partial_multi_input_artifacts_are_rejected(self) -> None:
        output = StringIO()
        writer = EventWriter(output)

        with self.assertRaises(ProtocolError):
            writer.completed(
                "C:/a.txt",
                "C:/a.srt",
                merged_audio_file="C:/merged_16k_mono.wav",
            )

        self.assertIsNone(writer.terminal)
        self.assertEqual(output.getvalue(), "")

    def test_blank_error_message_uses_fallback(self) -> None:
        output = StringIO()
        EventWriter(output).error("  ")

        self.assertEqual(
            parse_event_line(output.getvalue().strip()),
            ErrorEvent("Backend processing failed."),
        )


class BridgeTerminalEventTests(unittest.TestCase):
    def run_main_with(self, fake_run_bridge):
        output = StringIO()
        diagnostic = StringIO()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.wav"
            input_path.write_bytes(b"audio")
            with mock.patch.object(backend_bridge, "run_bridge", side_effect=fake_run_bridge):
                result = backend_bridge.main(
                    ["--input", str(input_path), "--model-dir", str(root)],
                    engine_module=object(),
                    stdout=output,
                    stderr=diagnostic,
                )
        return result, output.getvalue().splitlines(), diagnostic.getvalue()

    @staticmethod
    def complete(writer: EventWriter) -> None:
        writer.state("saving_result")
        writer.progress(100)
        writer.completed("C:/a.txt", "C:/a.srt")

    def test_failure_after_completed_does_not_emit_second_terminal_event(self) -> None:
        def fake_run_bridge(_args, _engine, writer, **_keywords):
            self.complete(writer)
            raise RuntimeError("late cleanup failure")

        result, lines, diagnostic = self.run_main_with(fake_run_bridge)

        self.assertEqual(result, 0)
        events = validate_stream(lines, result)
        self.assertIsInstance(events[-1], CompletedEvent)
        self.assertFalse(any(isinstance(event, ErrorEvent) for event in events))
        self.assertIn("late cleanup failure", diagnostic)

    def test_cancellation_after_completed_keeps_success_exit_code(self) -> None:
        def fake_run_bridge(_args, _engine, writer, **_keywords):
            self.complete(writer)
            raise KeyboardInterrupt

        result, lines, _diagnostic = self.run_main_with(fake_run_bridge)

        self.assertEqual(result, 0)
        self.assertIsInstance(validate_stream(lines, result)[-1], CompletedEvent)

    def test_failure_after_error_keeps_the_first_error(self) -> None:
        def fake_run_bridge(_args, _engine, writer, **_keywords):
            writer.error("first failure")
            raise backend_bridge.BridgeFailure("second failure", exit_code=2)

        result, lines, diagnostic = self.run_main_with(fake_run_bridge)

        self.assertEqual(result, 2)
        self.assertEqual(validate_stream(lines, result), [ErrorEvent("first failure")])
        self.assertIn("second failure", diagnostic)

    def test_return_without_terminal_event_is_reported_once(self) -> None:
        def fake_run_bridge(_args, _engine, writer, **_keywords):
            writer.state("preparing")
            return 0

        result, lines, _diagnostic = self.run_main_with(fake_run_bridge)

        self.assertEqual(result, 1)
        events = validate_stream(lines, result)
        self.assertEqual(events[-1], ErrorEvent("Backend finished without reporting a result."))

    def test_invalid_event_is_reported_as_single_error(self) -> None:
        def fake_run_bridge(_args, _engine, writer, **_keywords):
            writer.state("preparing")
            writer.state("unknown_stage")
            return 0

        result, lines, diagnostic = self.run_main_with(fake_run_bridge)

        self.assertEqual(result, 1)
        events = validate_stream(lines, result)
        self.assertEqual(len(events), 2)
        self.assertIn("unknown state value: unknown_stage", events[-1].message)
        self.assertNotIn("unknown_stage", lines[0])
        self.assertIn("ProtocolError", diagnostic)

    def test_single_input_run_keeps_existing_completed_contract(self) -> None:
        output = StringIO()
        diagnostic = StringIO()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.wav"
            model_dir = root / "model"
            input_path.write_bytes(b"audio")
            model_dir.mkdir()

            result = backend_bridge.main(
                ["--input", str(input_path), "--model-dir", str(model_dir)],
                engine_module=MinimalEngine(),
                stdout=output,
                stderr=diagnostic,
            )
            text_file = str((root / "input.txt").resolve())
            srt_file = str((root / "input.srt").resolve())

        self.assertEqual(result, 0, diagnostic.getvalue())
        events = validate_stream(output.getvalue().splitlines(), result)
        self.assertEqual(events[-1], CompletedEvent(text_file, srt_file))
        self.assertNotIn("merged_audio_file", output.getvalue())


class ProtocolStdoutIsolationTests(unittest.TestCase):
    def test_descriptor_level_stdout_writes_go_to_stderr(self) -> None:
        script = textwrap.dedent(
            """
            import os
            import subprocess
            import sys

            import backend_bridge

            backend_bridge._configure_standard_streams()
            stream = backend_bridge._isolate_protocol_stdout()
            os.write(1, b"native stdout noise\\n")
            print("python print noise", flush=True)
            subprocess.run([sys.executable, "-c", "print('child stdout noise')"], check=True)
            backend_bridge.EventWriter(stream).error("격리된 프로토콜")
            """
        )
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)

        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ENGINE_DIR,
            env=environment,
            capture_output=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        self.assertEqual(
            result.stdout.splitlines(),
            ['{"type":"error","message":"격리된 프로토콜"}'.encode("utf-8")],
        )
        for noise in (b"native stdout noise", b"python print noise", b"child stdout noise"):
            with self.subTest(noise=noise):
                self.assertIn(noise, result.stderr)


if __name__ == "__main__":
    unittest.main()
