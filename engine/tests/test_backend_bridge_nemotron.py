from __future__ import annotations

from contextlib import redirect_stderr
from dataclasses import dataclass, replace
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import backend_bridge
from backend_protocol import (
    CompletedEvent, ErrorEvent, ProgressEvent, SegmentEvent, StateEvent, WarningEvent,
    validate_stream,
)
import transcribe_npu
from test_backend_bridge_multi_pipeline import MultiInputBridgeTestCase


@dataclass(frozen=True)
class StubConfig:
    executable: str
    model: Path
    config: Path | None


class BackendBridgeNemotronTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.input = self.root / "회의 녹음.wav"
        self.input.write_bytes(b"source must remain unchanged")
        self.output = self.root / "output"
        self.audio = np.zeros(32_000, dtype=np.float32)
        self.engine = SimpleNamespace(
            TARGET_SAMPLE_RATE=16_000,
            decode_audio_16k_mono=Mock(return_value=self.audio),
            read_optional_text=lambda path: "회의 용어" if path.name == "hotwords.txt" else "한국어 회의",
            save_outputs=transcribe_npu.save_outputs,
            available_devices=Mock(side_effect=AssertionError("Nemotron must not initialize OpenVINO")),
            load_whisper_pipeline=Mock(side_effect=AssertionError("Nemotron must not load Whisper")),
        )
        self.transcribe = Mock(return_value=[transcribe_npu.Segment(0.25, 1.5, "회의를 시작합니다.")])
        self.adapter = SimpleNamespace(
            NemotronConfig=StubConfig,
            validate_config=Mock(side_effect=lambda config: replace(
                config, model=config.model.resolve(), config=config.config.resolve()
            )),
            transcribe_audio=self.transcribe,
        )
        self.argv = [
            "--input", str(self.input), "--engine", "nemotron",
            "--nemotron-model", str(self.root / "decoder.gguf"),
            "--output-dir", str(self.output),
        ]

    def run_bridge(self, *extra: str):
        output, diagnostic = StringIO(), StringIO()
        with patch.dict(sys.modules, {"nemotron": self.adapter}), redirect_stderr(diagnostic):
            code = backend_bridge.main(
                [*self.argv, *extra], engine_module=self.engine,
                diarize_function=Mock(side_effect=AssertionError("Must use native diarization")),
                stdout=output, stderr=diagnostic,
            )
        return code, validate_stream(output.getvalue().splitlines(), code), diagnostic.getvalue()

    def test_engine_specific_required_arguments_and_legacy_default(self) -> None:
        args = backend_bridge.parse_arguments(["--input", "a.wav", "--model-dir", "whisper"])
        self.assertEqual(args.engine, "whisper")
        args = backend_bridge.parse_arguments(self.argv)
        self.assertIsNone(args.model_dir)
        self.assertEqual(Path(args.nemotron_config), backend_bridge.DEFAULT_NEMOTRON_CONFIG)
        for arguments, message in (
            (["--input", "a.wav"], "--model-dir"),
            (["--input", "a.wav", "--engine", "nemotron"], "--nemotron-model"),
            ([*self.argv, "--diarization", "--num-speakers", "2"], "automatic speaker"),
            ([*self.argv, "--skip-silence"], "Whisper-only"),
            ([*self.argv, "--device", "NPU"], "Whisper-only"),
        ):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(backend_bridge.BridgeArgumentError, message):
                backend_bridge.parse_arguments(arguments)

    def test_nemotron_uses_shared_protocol_and_saves_without_whisper(self) -> None:
        code, events, diagnostic = self.run_bridge()
        self.assertEqual(code, 0, diagnostic)
        self.assertEqual([event for event in events if isinstance(event, SegmentEvent)], [
            SegmentEvent(0.25, 1.5, None, "회의를 시작합니다.")
        ])
        self.assertIsInstance(events[-1], CompletedEvent)
        self.assertIn("회의를 시작합니다.", Path(events[-1].text_file).read_text(encoding="utf-8-sig"))
        self.assertIn("00:00:00,250 --> 00:00:01,500", Path(events[-1].srt_file).read_text(encoding="utf-8-sig"))
        self.assertEqual(self.input.read_bytes(), b"source must remain unchanged")
        call = self.transcribe.call_args
        self.assertIs(call.args[0], self.audio)
        self.assertEqual(call.args[1], 2.0)
        self.assertEqual(call.kwargs["hotwords"], "회의 용어")
        self.assertEqual(call.kwargs["initial_prompt"], "한국어 회의")
        self.assertEqual(call.kwargs["language"], "<|ko|>")
        self.engine.available_devices.assert_not_called()
        self.engine.load_whisper_pipeline.assert_not_called()
        self.assertFalse(list(self.output.glob("*.partial")))

    def test_native_diarization_preserves_times_and_converts_speaker_numbers_once(self) -> None:
        self.transcribe.return_value = [
            transcribe_npu.Segment(0.1, 0.8, "첫 발화", 4),
            transcribe_npu.Segment(0.7, 1.9, "겹친 발화", 7),
        ]
        code, events, diagnostic = self.run_bridge("--diarization")
        self.assertEqual(code, 0, diagnostic)
        segments = [event for event in events if isinstance(event, SegmentEvent)]
        self.assertEqual(segments, [SegmentEvent(0.1, 0.8, 5, "첫 발화"), SegmentEvent(0.7, 1.9, 8, "겹친 발화")])
        self.assertTrue(self.transcribe.call_args.kwargs["diarization"])
        self.assertIn("화자 8", Path(events[-1].text_file).read_text(encoding="utf-8-sig"))

    def test_adapter_warning_reaches_gui(self) -> None:
        def transcribe(*args, **kwargs):
            kwargs["warning_callback"]("단어 시간 정보가 없어 전체 녹음 구간으로 표시합니다.")
            return [transcribe_npu.Segment(0, 2, "전체 전사")]
        self.transcribe.side_effect = transcribe
        code, events, diagnostic = self.run_bridge()
        self.assertEqual(code, 0, diagnostic)
        self.assertTrue(any(isinstance(event, WarningEvent) for event in events))

    def test_failure_or_cancel_preserves_existing_results_and_removes_staging(self) -> None:
        self.output.mkdir()
        existing = self.output / (self.input.stem + ".txt")
        existing.write_text("previous result", encoding="utf-8")
        for error, expected in ((RuntimeError("runtime failed"), 1), (KeyboardInterrupt(), 130)):
            with self.subTest(error=type(error).__name__):
                self.transcribe.side_effect = error
                code, events, _ = self.run_bridge()
                self.assertEqual(code, expected)
                self.assertIsInstance(events[-1], ErrorEvent)
                self.assertEqual(existing.read_text(encoding="utf-8"), "previous result")
                self.assertEqual(list(self.output.iterdir()), [existing])

    def test_missing_runtime_is_reported_before_audio_decode(self) -> None:
        self.adapter.validate_config.side_effect = FileNotFoundError("nemo-speech 실행 파일을 찾을 수 없습니다.")
        code, events, _ = self.run_bridge()
        self.assertEqual(code, 1)
        self.assertIn("nemo-speech", events[-1].message)
        self.engine.decode_audio_16k_mono.assert_not_called()

    def test_61_minute_recording_merges_three_complete_chunks_on_original_timeline(self) -> None:
        # A range preserves sample identity without allocating an hour of audio.
        audio = range((61 * 60 + 9) * 16_000)
        self.engine.decode_audio_16k_mono.return_value = audio
        captured = []

        def transcribe(chunk, duration, config, **options):
            captured.append((chunk, duration, config, options))
            return [transcribe_npu.Segment(0, duration, f"구간 {len(captured)}")]

        self.transcribe.side_effect = transcribe
        code, events, diagnostic = self.run_bridge("--language", "<|en|>")
        self.assertEqual(code, 0, diagnostic)
        self.assertEqual(len(captured), 3)
        expected_segments = []
        for index, (chunk, duration, config, options) in enumerate(captured):
            start, end = index * len(audio) // 3, (index + 1) * len(audio) // 3
            self.assertEqual(chunk, range(start, end))
            self.assertLessEqual(len(chunk), 29 * 60 * 16_000)
            self.assertEqual(duration, len(chunk) / 16_000)
            self.assertIs(config, captured[0][2])
            self.assertEqual(config.config, backend_bridge.DEFAULT_NEMOTRON_CONFIG.resolve())
            self.assertEqual(options["language"], "<|en|>")
            self.assertEqual(options["hotwords"], "회의 용어")
            self.assertEqual(options["initial_prompt"], "한국어 회의")
            self.assertFalse(options["diarization"])
            self.assertTrue(callable(options["warning_callback"]))
            self.assertIn(f"{index + 1}/3", diagnostic)
            expected_segments.append(SegmentEvent(
                start / 16_000, end / 16_000, None, f"구간 {index + 1}"
            ))
        self.assertEqual(sum(len(item[0]) for item in captured), len(audio))
        self.assertEqual([event for event in events if isinstance(event, SegmentEvent)], expected_segments)
        warnings = [event for event in events if isinstance(event, WarningEvent)]
        self.assertEqual(len(warnings), 1)
        self.assertIn("분할", warnings[0].message)
        self.assertLess(events.index(warnings[0]), events.index(expected_segments[0]))
        progress = [event for event in events if isinstance(event, ProgressEvent) and event.value is not None]
        self.assertEqual(progress, [
            ProgressEvent(0), ProgressEvent(33), ProgressEvent(66), ProgressEvent(99),
            ProgressEvent(100, 3669.0, 3669.0),
        ])
        saving = next(index for index, event in enumerate(events)
                      if isinstance(event, StateEvent) and event.value.value == "saving_result")
        self.assertTrue(all(event.value < 100 for event in events[:saving]
                            if isinstance(event, ProgressEvent) and event.value is not None))
        completed = events[-1]
        self.assertIsInstance(completed, CompletedEvent)
        text = Path(completed.text_file).read_text(encoding="utf-8-sig")
        srt = Path(completed.srt_file).read_text(encoding="utf-8-sig")
        for segment in expected_segments:
            self.assertEqual(text.count(segment.text), 1)
            self.assertEqual(srt.count(segment.text), 1)
            self.assertIn(
                f"{transcribe_npu.srt_timestamp(segment.start)} --> "
                f"{transcribe_npu.srt_timestamp(segment.end)}", srt,
            )
        self.assertEqual(self.input.read_bytes(), b"source must remain unchanged")
        self.assertEqual({path.suffix for path in self.output.iterdir()}, {".txt", ".srt"})

    def test_default_and_custom_limits_keep_exact_samples_and_avoid_tiny_tail(self) -> None:
        for extra, limit in (
            ((), 29 * 60 * 16_000),
            (("--nemotron-config", str(self.root / "custom.yaml")), 60 * 60 * 16_000),
        ):
            for sample_count in (limit - 1, limit, limit + 1):
                with self.subTest(config=extra, sample_count=sample_count):
                    audio = range(sample_count)
                    self.engine.decode_audio_16k_mono.return_value = audio
                    self.transcribe.reset_mock()
                    code, events, diagnostic = self.run_bridge(*extra)
                    self.assertEqual(code, 0, diagnostic)
                    count = 1 if sample_count <= limit else 2
                    calls = self.transcribe.call_args_list
                    self.assertEqual(len(calls), count)
                    lengths = [len(call.args[0]) for call in calls]
                    self.assertEqual(sum(lengths), sample_count)
                    self.assertLessEqual(max(lengths), limit)
                    self.assertLessEqual(max(lengths) - min(lengths), 1)
                    for index, call in enumerate(calls):
                        self.assertEqual(call.args[0], range(
                            index * sample_count // count, (index + 1) * sample_count // count
                        ))
                        self.assertEqual(call.args[1], len(call.args[0]) / 16_000)
                    if count == 1:
                        self.assertIs(calls[0].args[0], audio)
                        self.assertFalse(any(isinstance(event, WarningEvent) for event in events))

    def test_chunk_speaker_ids_are_unique_without_assuming_same_speaker_across_chunks(self) -> None:
        self.engine.decode_audio_16k_mono.return_value = range((61 * 60 + 9) * 16_000)
        local_speakers = ([7, 0, None, 7], [0, 7, 0], [None, 7])
        self.transcribe.side_effect = [
            [transcribe_npu.Segment(index, index + 0.5, f"발화 {index}", speaker)
             for index, speaker in enumerate(speakers)]
            for speakers in local_speakers
        ]
        code, events, diagnostic = self.run_bridge("--diarization")
        self.assertEqual(code, 0, diagnostic)
        self.assertEqual(
            [event.speaker for event in events if isinstance(event, SegmentEvent)],
            [1, 2, None, 1, 3, 4, 3, None, 5],
        )
        self.assertTrue(all(call.kwargs["diarization"] for call in self.transcribe.call_args_list))
        warnings = [event.message for event in events if isinstance(event, WarningEvent)]
        self.assertTrue(any("화자" in message for message in warnings))
        text = Path(events[-1].text_file).read_text(encoding="utf-8-sig")
        self.assertIn("화자 5", text)

    def test_split_without_diarization_does_not_publish_speaker_labels(self) -> None:
        self.engine.decode_audio_16k_mono.return_value = range(29 * 60 * 16_000 + 1)
        self.transcribe.return_value = [transcribe_npu.Segment(0.25, 1.5, "발화", 7)]
        code, events, diagnostic = self.run_bridge()
        self.assertEqual(code, 0, diagnostic)
        segments = [event for event in events if isinstance(event, SegmentEvent)]
        self.assertEqual(len(segments), 2)
        self.assertTrue(all(segment.speaker is None for segment in segments))
        self.assertNotIn("화자", Path(events[-1].text_file).read_text(encoding="utf-8-sig"))

    def test_later_chunk_failure_or_cancel_keeps_all_results_unpublished(self) -> None:
        self.engine.decode_audio_16k_mono.return_value = range((61 * 60 + 9) * 16_000)
        self.output.mkdir()
        existing = {
            self.output / (self.input.stem + ".txt"): b"previous text",
            self.output / (self.input.stem + ".srt"): b"previous subtitles",
        }
        for path, contents in existing.items():
            path.write_bytes(contents)
        self.engine.save_outputs = Mock(wraps=transcribe_npu.save_outputs)
        for error, expected in ((RuntimeError("second chunk failed"), 1), (KeyboardInterrupt(), 130)):
            with self.subTest(error=type(error).__name__):
                self.transcribe.reset_mock()
                self.transcribe.side_effect = [
                    [transcribe_npu.Segment(0.25, 1.5, "공개되면 안 되는 첫 구간")],
                    error,
                    AssertionError("No third request after a failed or cancelled chunk"),
                ]
                code, events, _ = self.run_bridge()
                self.assertEqual(code, expected)
                self.assertEqual(self.transcribe.call_count, 2)
                self.assertIsInstance(events[-1], ErrorEvent)
                self.assertFalse(any(isinstance(event, (SegmentEvent, CompletedEvent)) for event in events))
                self.assertFalse(any(isinstance(event, ProgressEvent) and event.value == 100 for event in events))
                self.engine.save_outputs.assert_not_called()
                self.assertEqual(set(self.output.iterdir()), set(existing))
                for path, contents in existing.items():
                    self.assertEqual(path.read_bytes(), contents)


class NemotronMultiInputTests(MultiInputBridgeTestCase):
    def test_merged_audio_chunking_publishes_only_after_all_requests_succeed(self) -> None:
        paths = self.write_inputs()
        source_contents = {path: path.read_bytes() for path in paths}
        manifest = self.write_manifest(paths)
        # Use a small request limit to exercise real alignment, rendering and decoding.
        for failure in (False, True):
            with self.subTest(failure=failure):
                output = self.root / ("failure" if failure else "success")
                responses = [
                    [transcribe_npu.Segment(0.25, 1.5, f"구간 {index + 1}", 0)]
                    for index in range(3)
                ]
                if failure:
                    responses[1] = RuntimeError("second merged chunk failed")
                with patch.object(backend_bridge, "NEMOTRON_DEFAULT_CHUNK_SECONDS", 3), patch(
                    "nemotron.validate_config", side_effect=lambda config: config
                ), patch("nemotron.transcribe_audio", side_effect=responses) as transcribe, redirect_stderr(StringIO()):
                    code, events, diagnostic = self.run_main(
                        manifest, "--engine", "nemotron", "--device", "AUTO",
                        "--nemotron-model", str(self.root / "decoder.gguf"),
                        "--output-dir", str(output), "--diarization",
                    )
                self.assertEqual(self.engine.model_load_count, 0)
                self.assertEqual({path: path.read_bytes() for path in paths}, source_contents)
                self.assertEqual(transcribe.call_count, 2 if failure else 3, diagnostic)
                if failure:
                    self.assertEqual(code, 1, diagnostic)
                    self.assertIsInstance(events[-1], ErrorEvent)
                    self.assertFalse(any(isinstance(event, (SegmentEvent, CompletedEvent)) for event in events))
                    self.assert_no_multi_input_outputs(output)
                    continue
                self.assertEqual(code, 0, diagnostic)
                audio = self.engine.decoded_audio[-1]
                for index, call in enumerate(transcribe.call_args_list):
                    np.testing.assert_array_equal(
                        call.args[0], audio[index * len(audio) // 3:(index + 1) * len(audio) // 3]
                    )
                self.assertEqual([event for event in events if isinstance(event, SegmentEvent)], [
                    SegmentEvent(index * 3 + 0.25, index * 3 + 1.5, index + 1, f"구간 {index + 1}")
                    for index in range(3)
                ])
                completed = events[-1]
                self.assertTrue(completed.is_multi_input)
                for field in ("text_file", "srt_file", "alignment_file", "selection_file", "merged_audio_file"):
                    artifact = Path(getattr(completed, field))
                    self.assertTrue(artifact.is_file())
                    self.assertEqual(artifact.parent, output.resolve())
                self.assert_no_staging(output)

    def test_merged_audio_uses_nemotron_and_publishes_all_artifacts(self) -> None:
        paths = self.write_inputs()
        captured = []

        def transcribe(audio, duration, config, **options):
            captured.append(audio.copy())
            return [transcribe_npu.Segment(1.0, 2.5, "다중 녹음 전사", 0)]

        with patch("nemotron.validate_config", side_effect=lambda config: config), patch(
            "nemotron.transcribe_audio", side_effect=transcribe
        ):
            code, events, diagnostic = self.run_main(
                self.write_manifest(paths),
                "--engine", "nemotron", "--device", "AUTO",
                "--nemotron-model", str(self.root / "decoder.gguf"),
                "--diarization",
            )
        self.assertEqual(code, 0, diagnostic)
        self.assertEqual(self.engine.model_load_count, 0)
        self.assertEqual(len(captured), 1)
        np.testing.assert_array_equal(captured[0], self.engine.decoded_audio[0])
        self.assertEqual(len(captured[0]), self.reference_sample_count)
        self.assertEqual([event for event in events if isinstance(event, SegmentEvent)], [
            SegmentEvent(1.0, 2.5, 1, "다중 녹음 전사")
        ])
        completed = events[-1]
        self.assertTrue(completed.is_multi_input)
        for field in ("text_file", "srt_file", "alignment_file", "selection_file", "merged_audio_file"):
            self.assertTrue(Path(getattr(completed, field)).is_file())
        self.assert_no_staging(self.recordings)


if __name__ == "__main__":
    unittest.main()
