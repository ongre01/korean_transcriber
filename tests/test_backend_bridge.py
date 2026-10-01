from contextlib import redirect_stdout
from dataclasses import dataclass
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

import backend_bridge  # noqa: E402
from backend_protocol import (  # noqa: E402
    CompletedEvent,
    ErrorEvent,
    ProgressEvent,
    SegmentEvent,
    StateEvent,
    validate_stream,
)
import transcribe_npu  # noqa: E402


@dataclass
class FakeSegment:
    start: float
    end: float
    text: str
    speaker: int | None = None


class FakeEngine:
    TARGET_SAMPLE_RATE = 16_000

    def __init__(self):
        self.devices = ["CPU", "NPU.0", "GPU.0"]
        self.audio = [0.0] * 32_000
        self.segments = [
            FakeSegment(0.0, 0.75, "안녕하세요."),
            FakeSegment(0.75, 1.5, "회의를 시작합니다."),
        ]
        self.decode_error = None
        self.load_error = None
        self.transcribe_error = None
        self.save_error = None
        self.create_result_files = True
        self.loaded_device = None
        self.progress_enabled = True
        self.assigned_turns = None

    def available_devices(self):
        print(f"Available devices: {self.devices}")
        return self.devices

    def ensure_device(self, device):
        if not any(backend_bridge._matches_device(item, device) for item in self.devices):
            raise RuntimeError(f"device unavailable: {device}")
        return self.devices

    @staticmethod
    def read_optional_text(_path):
        return ""

    def decode_audio_16k_mono(self, _input_path):
        if self.decode_error:
            raise self.decode_error
        return self.audio

    def load_whisper_pipeline(self, _model_dir, device, _model_label):
        print("human model diagnostic")
        if self.load_error:
            raise self.load_error
        self.loaded_device = device
        return object()

    @staticmethod
    def configure_generation(_pipe, _language, _beams, _hotwords, _initial_prompt):
        return object()

    def transcribe_windows(
        self,
        _pipe,
        _config,
        _audio,
        duration,
        _window,
        _overlap,
        *,
        progress_callback,
        console_progress,
    ):
        self.assert_console_progress = console_progress
        if self.transcribe_error:
            raise self.transcribe_error
        if self.progress_enabled:
            progress_callback(0.0, duration, 0, 2)
            progress_callback(duration / 2.0, duration, 1, 2)
            progress_callback(duration, duration, 2, 2)
        return self.segments

    def assign_speakers(self, segments, turns):
        self.assigned_turns = turns
        for segment in segments:
            segment.speaker = 0

    def save_outputs(
        self,
        input_path,
        segments,
        _duration,
        suffix,
        *,
        diarized,
        output_dir,
    ):
        if self.save_error:
            raise self.save_error
        text_path = output_dir / f"{input_path.stem}{suffix}.txt"
        srt_path = output_dir / f"{input_path.stem}{suffix}.srt"
        if self.create_result_files:
            labels = [
                f"{segment.speaker}:{segment.text}" if diarized else segment.text
                for segment in segments
            ]
            text_path.write_text("\n".join(labels), encoding="utf-8")
            srt_path.write_text("SRT", encoding="utf-8")
        return text_path, srt_path


class BackendBridgeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.input_path = self.root / "한글 입력 폴더" / "회의 녹음.wav"
        self.input_path.parent.mkdir()
        self.input_path.write_bytes(b"fixture")
        self.model_dir = self.root / "모델 폴더"
        self.model_dir.mkdir()

    def arguments(self, *extra):
        return [
            "--input",
            str(self.input_path),
            "--model-dir",
            str(self.model_dir),
            *extra,
        ]

    def invoke(self, engine=None, *extra, diarize_function=None):
        output = io.StringIO()
        diagnostic = io.StringIO()
        engine = engine or FakeEngine()
        exit_code = backend_bridge.main(
            self.arguments(*extra),
            engine_module=engine,
            diarize_function=diarize_function,
            stdout=output,
            stderr=diagnostic,
        )
        lines = output.getvalue().splitlines()
        events = validate_stream(lines, exit_code)
        return exit_code, events, diagnostic.getvalue(), engine

    def test_success_emits_valid_json_lines_and_creates_results(self):
        output_dir = self.root / "결과 폴더 with spaces"
        exit_code, events, diagnostic, engine = self.invoke(
            None,
            "--device",
            "AUTO",
            "--output-dir",
            str(output_dir),
            "--output-suffix",
            "_완료",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(engine.loaded_device, "NPU")
        self.assertFalse(engine.assert_console_progress)
        self.assertIn("human model diagnostic", diagnostic)
        self.assertNotIn("human model diagnostic", "\n".join(map(str, events)))

        states = [event.value.value for event in events if isinstance(event, StateEvent)]
        self.assertEqual(
            states,
            [
                "preparing",
                "decoding_audio",
                "loading_model",
                "transcribing",
                "saving_result",
            ],
        )
        progress = [event for event in events if isinstance(event, ProgressEvent)]
        self.assertTrue(any(event.value is None for event in progress))
        self.assertTrue(any(event.value == 50 for event in progress))
        self.assertEqual(progress[-1].value, 100)
        self.assertEqual(progress[-1].processed_seconds, 2.0)
        self.assertEqual(progress[-1].total_seconds, 2.0)
        saving_index = next(
            index for index, event in enumerate(events)
            if isinstance(event, StateEvent) and event.value.value == "saving_result"
        )
        final_progress_index = max(
            index for index, event in enumerate(events)
            if isinstance(event, ProgressEvent) and event.value == 100
        )
        self.assertLess(saving_index, final_progress_index)
        self.assertLess(final_progress_index, len(events) - 1)
        self.assertTrue(all(
            event.value < 100
            for event in events[:saving_index]
            if isinstance(event, ProgressEvent) and event.value is not None
        ))
        self.assertEqual(
            [event.text for event in events if isinstance(event, SegmentEvent)],
            ["안녕하세요.", "회의를 시작합니다."],
        )

        completed = events[-1]
        self.assertIsInstance(completed, CompletedEvent)
        self.assertTrue(Path(completed.text_file).is_absolute())
        self.assertTrue(Path(completed.srt_file).is_absolute())
        self.assertEqual(Path(completed.text_file).parent, output_dir.resolve())
        self.assertEqual(
            Path(completed.text_file).read_text(encoding="utf-8"),
            "안녕하세요.\n회의를 시작합니다.",
        )

    def test_diarization_maps_auto_and_zero_based_speaker(self):
        call = {}

        def diarize(_audio, _segmentation, _embedding, **kwargs):
            call.update(kwargs)
            return [SimpleNamespace(start=0.0, end=2.0, speaker=0)]

        exit_code, events, _diagnostic, engine = self.invoke(
            None,
            "--diarization",
            "--num-speakers",
            "auto",
            diarize_function=diarize,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(call["num_speakers"], -1)
        self.assertEqual(engine.assigned_turns[0].speaker, 0)
        segments = [event for event in events if isinstance(event, SegmentEvent)]
        self.assertEqual([event.speaker for event in segments], [1, 1])
        self.assertEqual([event.text for event in segments], [
            "안녕하세요.", "회의를 시작합니다."
        ])
        self.assertEqual(len(segments), len(engine.segments))
        states = [event.value.value for event in events if isinstance(event, StateEvent)]
        self.assertIn("diarization", states)

    def test_diarization_off_never_calls_diarizer_or_emits_speakers(self):
        called = False

        def diarize(*_args, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("diarization must not run when disabled")

        exit_code, events, _diagnostic, engine = self.invoke(
            None, diarize_function=diarize
        )

        self.assertEqual(exit_code, 0)
        self.assertFalse(called)
        self.assertIsNone(engine.assigned_turns)
        segments = [event for event in events if isinstance(event, SegmentEvent)]
        self.assertEqual([event.speaker for event in segments], [None, None])
        states = [event.value.value for event in events if isinstance(event, StateEvent)]
        self.assertNotIn("diarization", states)

    def test_diarization_maps_fixed_count_and_emits_final_segments_once(self):
        call = {}

        def diarize(_audio, _segmentation, _embedding, **kwargs):
            call.update(kwargs)
            return [SimpleNamespace(start=0.0, end=2.0, speaker=0)]

        exit_code, events, _diagnostic, engine = self.invoke(
            None,
            "--diarization",
            "--num-speakers",
            "2",
            diarize_function=diarize,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(call["num_speakers"], 2)
        segments = [event for event in events if isinstance(event, SegmentEvent)]
        self.assertEqual(len(segments), len(engine.segments))
        self.assertEqual([event.text for event in segments], [
            "안녕하세요.", "회의를 시작합니다."
        ])
        self.assertEqual([event.speaker for event in segments], [1, 1])

    def test_missing_diarization_model_emits_error_and_a_retry_can_succeed(self):
        def missing_model(*_args, **_kwargs):
            raise RuntimeError("Speaker segmentation model not found")

        exit_code, events, _diagnostic, engine = self.invoke(
            None, "--diarization", diarize_function=missing_model
        )

        self.assertEqual(exit_code, 1)
        self.assertIsInstance(events[-1], ErrorEvent)
        self.assertIn("Speaker diarization failed", events[-1].message)
        self.assertFalse(any(isinstance(event, CompletedEvent) for event in events))
        self.assertIsNone(engine.assigned_turns)

        def diarize(_audio, _segmentation, _embedding, **_kwargs):
            return [SimpleNamespace(start=0.0, end=2.0, speaker=0)]

        retry_exit_code, retry_events, _diagnostic, _engine = self.invoke(
            None, "--diarization", diarize_function=diarize
        )
        self.assertEqual(retry_exit_code, 0)
        self.assertIsInstance(retry_events[-1], CompletedEvent)

    def test_empty_transcript_is_a_successful_completed_stream(self):
        engine = FakeEngine()
        engine.segments = []
        exit_code, events, _diagnostic, _engine = self.invoke(engine)

        self.assertEqual(exit_code, 0)
        self.assertFalse(any(isinstance(event, SegmentEvent) for event in events))
        self.assertIsInstance(events[-1], CompletedEvent)

    def test_zero_duration_is_indeterminate_until_result_saving_succeeds(self):
        engine = FakeEngine()
        engine.audio = []
        exit_code, events, _diagnostic, _engine = self.invoke(engine)

        self.assertEqual(exit_code, 0)
        final_progress_index = max(
            index for index, event in enumerate(events)
            if isinstance(event, ProgressEvent) and event.value == 100
        )
        self.assertTrue(all(
            event.value is None
            for event in events[:final_progress_index]
            if isinstance(event, ProgressEvent)
        ))
        self.assertIsInstance(events[-1], CompletedEvent)

    def test_missing_input_and_model_have_contract_exit_codes(self):
        self.input_path.unlink()
        exit_code, events, _diagnostic, _engine = self.invoke()
        self.assertEqual(exit_code, 2)
        self.assertIsInstance(events[-1], ErrorEvent)
        self.assertFalse(any(isinstance(event, CompletedEvent) for event in events))

        self.input_path.write_bytes(b"fixture")
        self.model_dir.rmdir()
        exit_code, events, _diagnostic, _engine = self.invoke()
        self.assertEqual(exit_code, 3)
        self.assertIsInstance(events[-1], ErrorEvent)
        self.assertFalse(any(isinstance(event, CompletedEvent) for event in events))

    def test_runtime_failures_never_emit_completed(self):
        cases = [
            ("decode_error", RuntimeError("broken audio"), "Audio decoding failed"),
            ("load_error", RuntimeError("broken model"), "Whisper model could not be loaded"),
            ("transcribe_error", RuntimeError("pipeline stopped"), "Transcription failed"),
            ("save_error", PermissionError("read only"), "Result files could not be saved"),
        ]
        for attribute, error, message in cases:
            with self.subTest(attribute=attribute):
                engine = FakeEngine()
                setattr(engine, attribute, error)
                exit_code, events, diagnostic, _engine = self.invoke(engine)
                self.assertEqual(exit_code, 1)
                self.assertIsInstance(events[-1], ErrorEvent)
                self.assertIn(message, events[-1].message)
                self.assertFalse(any(isinstance(event, CompletedEvent) for event in events))
                self.assertFalse(any(
                    isinstance(event, ProgressEvent) and event.value == 100
                    for event in events
                ))
                self.assertIn(str(error), diagnostic)

    def test_completed_requires_both_saved_files(self):
        engine = FakeEngine()
        engine.create_result_files = False
        exit_code, events, _diagnostic, _engine = self.invoke(engine)

        self.assertEqual(exit_code, 1)
        self.assertIsInstance(events[-1], ErrorEvent)
        self.assertFalse(any(isinstance(event, CompletedEvent) for event in events))

    def test_argument_error_is_a_valid_error_stream(self):
        output = io.StringIO()
        diagnostic = io.StringIO()
        exit_code = backend_bridge.main(
            ["--input", str(self.input_path)],
            stdout=output,
            stderr=diagnostic,
        )
        events = validate_stream(output.getvalue().splitlines(), exit_code)

        self.assertEqual(exit_code, 2)
        self.assertIsInstance(events[-1], ErrorEvent)
        self.assertIn("Invalid bridge arguments", events[-1].message)


class EngineCompatibilityTest(unittest.TestCase):
    def test_transcribe_windows_keeps_console_default_and_supports_callback(self):
        class FakePipeline:
            @staticmethod
            def generate(_audio, _config):
                chunk = SimpleNamespace(text="테스트", start_ts=0.0, end_ts=0.5)
                return SimpleNamespace(chunks=[chunk])

        audio = transcribe_npu.np.zeros(16_000, dtype=transcribe_npu.np.float32)
        console = io.StringIO()
        with redirect_stdout(console):
            segments = transcribe_npu.transcribe_windows(
                FakePipeline(), object(), audio, 1.0, 30.0, 0.0
            )
        self.assertEqual(segments[0].text, "테스트")
        self.assertIn("100.0%", console.getvalue())

        progress = []
        console = io.StringIO()
        with redirect_stdout(console):
            transcribe_npu.transcribe_windows(
                FakePipeline(),
                object(),
                audio,
                1.0,
                30.0,
                0.0,
                progress_callback=lambda *values: progress.append(values),
                console_progress=False,
            )
        self.assertEqual(console.getvalue(), "")
        self.assertEqual([values[0] for values in progress], [0.0, 1.0])

    def test_existing_cli_success_path_and_human_output_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.wav"
            input_path.write_bytes(b"fixture")
            model_dir = root / "model"
            model_dir.mkdir()
            text_path = root / "input.txt"
            srt_path = root / "input.srt"
            audio = transcribe_npu.np.zeros(
                16_000, dtype=transcribe_npu.np.float32
            )
            pipe = object()
            config = object()
            segments = [transcribe_npu.Segment(0.0, 0.5, "기존 CLI")]
            args = SimpleNamespace(
                input=str(input_path),
                model_dir=str(model_dir),
                model_label="INT8",
                device="CPU",
                beams=1,
                language="<|ko|>",
                window_seconds=120.0,
                overlap_seconds=4.0,
                output_suffix="",
                hotwords_file=str(root / "missing-hotwords.txt"),
                initial_prompt_file=str(root / "missing-prompt.txt"),
                diarize=False,
                num_speakers=-1,
                speaker_threshold=0.5,
                diarization_segmentation_model="unused",
                diarization_embedding_model="unused",
                diarization_device="CPU",
                diarization_no_fallback=False,
            )

            console = io.StringIO()
            with (
                patch.object(transcribe_npu, "parse_args", return_value=args),
                patch.object(transcribe_npu, "ensure_device", return_value=["CPU"]),
                patch.object(
                    transcribe_npu, "decode_audio_16k_mono", return_value=audio
                ),
                patch.object(
                    transcribe_npu, "load_whisper_pipeline", return_value=pipe
                ) as load_pipeline,
                patch.object(
                    transcribe_npu, "configure_generation", return_value=config
                ) as configure,
                patch.object(
                    transcribe_npu, "transcribe_windows", return_value=segments
                ) as transcribe,
                patch.object(
                    transcribe_npu,
                    "save_outputs",
                    return_value=(text_path, srt_path),
                ) as save,
                redirect_stdout(console),
            ):
                exit_code = transcribe_npu.main()

            self.assertEqual(exit_code, 0)
            self.assertIn("Korean Transcriber v7", console.getvalue())
            self.assertIn("Done", console.getvalue())
            self.assertIn(f"TXT : {text_path}", console.getvalue())
            load_pipeline.assert_called_once_with(model_dir.resolve(), "CPU", "INT8")
            configure.assert_called_once_with(pipe, "<|ko|>", 1, "", "")
            call = transcribe.call_args
            self.assertIs(call.args[0], pipe)
            self.assertIs(call.args[1], config)
            self.assertIs(call.args[2], audio)
            self.assertEqual(call.args[3:], (1.0, 120.0, 4.0))
            save.assert_called_once_with(
                input_path.resolve(), segments, 1.0, "", diarized=False
            )


@unittest.skipUnless(
    os.environ.get("KOREAN_TRANSCRIBER_TEST_AUDIO")
    and os.environ.get("KOREAN_TRANSCRIBER_TEST_MODEL_DIR"),
    "set KOREAN_TRANSCRIBER_TEST_AUDIO and KOREAN_TRANSCRIBER_TEST_MODEL_DIR",
)
class ActualModelBridgeTest(unittest.TestCase):
    def test_short_audio_with_installed_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            diagnostic = io.StringIO()
            exit_code = backend_bridge.main(
                [
                    "--input",
                    os.environ["KOREAN_TRANSCRIBER_TEST_AUDIO"],
                    "--model-dir",
                    os.environ["KOREAN_TRANSCRIBER_TEST_MODEL_DIR"],
                    "--device",
                    os.environ.get("KOREAN_TRANSCRIBER_TEST_DEVICE", "CPU"),
                    "--output-dir",
                    temporary,
                ],
                stdout=output,
                stderr=diagnostic,
            )
            events = validate_stream(output.getvalue().splitlines(), exit_code)
            self.assertEqual(exit_code, 0, diagnostic.getvalue())
            self.assertIsInstance(events[-1], CompletedEvent)


if __name__ == "__main__":
    unittest.main()
