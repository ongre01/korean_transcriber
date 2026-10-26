from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import backend_bridge
from backend_protocol import (
    CompletedEvent,
    ErrorEvent,
    ProgressEvent,
    SegmentEvent,
    StateEvent,
    WarningEvent,
    validate_stream,
)
import multimic
import multimic_quality
import transcribe_npu
from test_multimic_align import speech_like_master
from test_multimic_quality_cli import write_source_wav


SAMPLE_RATE = transcribe_npu.TARGET_SAMPLE_RATE
LATER_START_MS = 500
# Both microphones are silent for this reference-timeline interval, which
# makes the timeline of the transcribed audio observable.
GAP_SECONDS = (4.0, 5.5)
# Well above the later microphone's added noise and well below speech.
ONSET_THRESHOLD = 0.02
MULTI_STAGES = [
    "decoding_audio",
    "aligning_audio",
    "detecting_speech",
    "assessing_channels",
    "selecting_channels",
    "rendering_merged_audio",
]


class MergedAudioEngine:
    """``transcribe_npu`` with only the OpenVINO Whisper model replaced.

    The fake transcriber returns one segment from the first to the last loud
    sample of every audio chunk it receives, so the reported times show which
    timeline the bridge transcribed and how it restored chunk offsets.
    """

    TARGET_SAMPLE_RATE = SAMPLE_RATE
    assign_speakers = staticmethod(transcribe_npu.assign_speakers)
    save_outputs = staticmethod(transcribe_npu.save_outputs)

    def __init__(self) -> None:
        self.decoded_paths: list[Path] = []
        self.decoded_audio: list[np.ndarray] = []
        self.transcribed_audio: list[np.ndarray] = []
        self.model_load_count = 0

    @staticmethod
    def available_devices() -> list[str]:
        return ["CPU"]

    @staticmethod
    def ensure_device(_device: str) -> list[str]:
        return ["CPU"]

    @staticmethod
    def read_optional_text(_path: Path) -> str:
        return ""

    def decode_audio_16k_mono(self, path: Path) -> np.ndarray:
        audio = transcribe_npu.decode_audio_16k_mono(path)
        self.decoded_paths.append(Path(path))
        self.decoded_audio.append(audio)
        return audio

    def load_whisper_pipeline(self, *_arguments, **_keyword_arguments):
        self.model_load_count += 1
        return object()

    @staticmethod
    def prepare_initial_prompt(_pipe, _initial_prompt: str):
        return SimpleNamespace(value="", warning=None)

    @staticmethod
    def configure_generation(*_arguments, **_keyword_arguments):
        return object()

    def transcribe_windows(self, _pipe, _config, audio, duration, _window, _overlap,
                           *, progress_callback, console_progress):
        if console_progress:
            raise AssertionError("console progress must be disabled in the bridge")
        values = np.asarray(audio)
        self.transcribed_audio.append(values)
        progress_callback(duration, duration, 1, 1)
        loud = np.flatnonzero(np.abs(values) > ONSET_THRESHOLD)
        if loud.size == 0:
            return []
        return [
            transcribe_npu.Segment(
                loud[0] / SAMPLE_RATE,
                (loud[-1] + 1) / SAMPLE_RATE,
                f"발화 {len(self.transcribed_audio)}",
            )
        ]


def loud_bounds(audio: np.ndarray, start: int = 0, end: int | None = None) -> tuple[int, int]:
    loud = np.flatnonzero(np.abs(audio[start:end]) > ONSET_THRESHOLD)
    return start + int(loud[0]), start + int(loud[-1]) + 1


def state_values(events) -> list[str]:
    return [event.value.value for event in events if isinstance(event, StateEvent)]


class MultiInputBridgeTestCase(unittest.TestCase):
    """Two offset recordings of one meeting and a bridge run helper."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.recordings = self.root / "회의 녹음"
        self.recordings.mkdir()
        self.model_dir = self.root / "model"
        self.model_dir.mkdir()
        self.engine = MergedAudioEngine()

    def write_inputs(self, *, gap: bool = True, noisy_reference: bool = False) -> list[Path]:
        master = speech_like_master(seed=77, duration_seconds=16.0)
        start = 3 * SAMPLE_RATE
        length = 9 * SAMPLE_RATE
        delay = LATER_START_MS * SAMPLE_RATE // 1_000
        if gap:
            gap_start, gap_end = (int(value * SAMPLE_RATE) for value in GAP_SECONDS)
            master[start + gap_start : start + gap_end] = 0.0
        reference = master[start : start + length].copy()
        if noisy_reference:
            # Broadband noise on the reference defeats automatic alignment.
            reference[3 * SAMPLE_RATE : 5 * SAMPLE_RATE] += np.random.default_rng(
                9
            ).normal(0.0, 0.08, 2 * SAMPLE_RATE).astype(np.float32)
        later = (
            0.65 * master[start + delay : start + delay + length]
            + np.random.default_rng(5).normal(0.0, 0.003, length)
        ).astype(np.float32)

        self.reference_sample_count = reference.size
        paths = [self.recordings / "테이블 중앙.wav", self.recordings / "발표자 근처.wav"]
        write_source_wav(paths[0], reference)
        write_source_wav(paths[1], later)
        return paths

    def write_manifest(
        self,
        paths: list[Path],
        *,
        manual_offsets: dict[str, int] | None = None,
        overrides: list[dict[str, int]] | None = None,
    ) -> Path:
        payload: dict[str, object] = {
            "version": 1,
            "inputs": [
                {"path": str(paths[0]), "label": "테이블 중앙"},
                {"path": str(paths[1]), "label": "발표자 근처"},
            ],
            "reference_index": 0,
            "alignment": {"mode": "auto", "manual_offsets_ms": manual_offsets or {}},
            "selection": {"mode": "automatic", "window_seconds": 3.0, "hop_seconds": 1.0},
        }
        if overrides is not None:
            payload["selection"]["overrides"] = overrides  # type: ignore[index]
        path = self.root / "다중 입력 manifest.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def run_main(self, manifest_path: Path, *arguments: str, diarize_function=None):
        output = StringIO()
        diagnostic = StringIO()
        result = backend_bridge.main(
            [
                "--multi-input-manifest", str(manifest_path),
                "--model-dir", str(self.model_dir),
                "--device", "CPU",
                *arguments,
            ],
            engine_module=self.engine,
            diarize_function=diarize_function,
            stdout=output,
            stderr=diagnostic,
        )
        events = validate_stream(output.getvalue().splitlines(), result)
        return result, events, diagnostic.getvalue()

    def assert_no_staging(self, directory: Path) -> None:
        self.assertFalse(
            [path.name for path in directory.iterdir() if path.name.endswith(".partial")]
        )

    def assert_no_multi_input_outputs(self, directory: Path) -> None:
        names = {path.name for path in directory.iterdir()}
        for name in (
            multimic.MERGED_AUDIO_FILE_NAME,
            multimic.ALIGNMENT_FILE_NAME,
            multimic.CHANNEL_SELECTION_FILE_NAME,
            "merged_16k_mono.txt",
            "merged_16k_mono.srt",
        ):
            self.assertNotIn(name, names)
        self.assert_no_staging(directory)


class BackendBridgeMultiPipelineTests(MultiInputBridgeTestCase):
    def test_multi_input_is_transcribed_on_the_reference_timeline(self) -> None:
        paths = self.write_inputs()
        sources_before = {path: multimic.fingerprint_file(path) for path in paths}

        result, events, diagnostic = self.run_main(self.write_manifest(paths))

        self.assertEqual(result, 0, diagnostic)
        self.assertEqual(
            state_values(events),
            ["preparing", *MULTI_STAGES, "loading_model", "transcribing", "saving_result"],
        )
        for index, event in enumerate(events):
            if isinstance(event, StateEvent) and event.value.value in MULTI_STAGES:
                self.assertEqual(events[index + 1], ProgressEvent(None))

        # Without --output-dir, results go next to the reference recording.
        completed = events[-1]
        self.assertIsInstance(completed, CompletedEvent)
        self.assertTrue(completed.is_multi_input)
        merged_path = self.recordings / multimic.MERGED_AUDIO_FILE_NAME
        self.assertEqual(completed.merged_audio_file, str(merged_path.resolve()))
        self.assertEqual(
            completed.alignment_file,
            str((self.recordings / multimic.ALIGNMENT_FILE_NAME).resolve()),
        )
        self.assertEqual(
            completed.selection_file,
            str((self.recordings / multimic.CHANNEL_SELECTION_FILE_NAME).resolve()),
        )
        self.assertEqual(
            completed.text_file,
            str((self.recordings / "merged_16k_mono.txt").resolve()),
        )
        for name in ("text_file", "srt_file", "merged_audio_file", "alignment_file", "selection_file"):
            self.assertTrue(Path(getattr(completed, name)).is_file(), name)

        # Whisper received the merged WAV while it was still staged with its
        # diagnostics, exactly as long as the reference recording, with the
        # silent interval at reference time.  Publishing removed the staging.
        self.assertEqual(len(self.engine.decoded_paths), 1)
        staged_path = self.engine.decoded_paths[0]
        self.assertEqual(staged_path.name, multimic.MERGED_AUDIO_FILE_NAME)
        self.assertEqual(staged_path.parent.parent, self.recordings.resolve())
        self.assertTrue(staged_path.parent.name.endswith(".partial"))
        self.assertFalse(staged_path.parent.exists())
        self.assert_no_staging(self.recordings)
        audio = self.engine.decoded_audio[0]
        self.assertEqual(audio.size, self.reference_sample_count)
        self.assertEqual(len(self.engine.transcribed_audio), 1)
        np.testing.assert_array_equal(self.engine.transcribed_audio[0], audio)
        gap_start, gap_end = (int(value * SAMPLE_RATE) for value in GAP_SECONDS)
        margin = SAMPLE_RATE // 20
        self.assertLess(
            float(np.max(np.abs(audio[gap_start + margin : gap_end - margin]))),
            ONSET_THRESHOLD,
        )

        duration = self.reference_sample_count / SAMPLE_RATE
        progress = [event for event in events if isinstance(event, ProgressEvent)]
        self.assertEqual(progress[-1], ProgressEvent(100, duration, duration))

        first, last = loud_bounds(audio)
        segments = [event for event in events if isinstance(event, SegmentEvent)]
        self.assertEqual(
            segments,
            [SegmentEvent(first / SAMPLE_RATE, last / SAMPLE_RATE, None, "발화 1")],
        )
        srt = Path(completed.srt_file).read_text(encoding="utf-8-sig")
        self.assertIn(
            f"{transcribe_npu.srt_timestamp(first / SAMPLE_RATE)} --> "
            f"{transcribe_npu.srt_timestamp(last / SAMPLE_RATE)}",
            srt,
        )
        text = Path(completed.text_file).read_text(encoding="utf-8-sig")
        self.assertEqual(text, "발화 1\n")

        alignment = json.loads(Path(completed.alignment_file).read_text(encoding="utf-8"))
        self.assertEqual([item["label"] for item in alignment["sources"]], ["테이블 중앙", "발표자 근처"])
        self.assertNotIn("path", alignment["sources"][0])
        self.assertLessEqual(abs(alignment["inputs"][1]["offset_ms"] - LATER_START_MS), 20.0)
        self.assertEqual(
            {path: multimic.fingerprint_file(path) for path in paths},
            sources_before,
        )

    def test_skip_silence_and_diarization_keep_reference_timestamps(self) -> None:
        paths = self.write_inputs()
        output_dir = self.root / "결과 폴더"
        diarized_audio: list[np.ndarray] = []
        boundary = sum(GAP_SECONDS) / 2.0

        def diarize(audio, _segmentation, _embedding, **_options):
            diarized_audio.append(np.asarray(audio))
            return [
                SimpleNamespace(start=0.0, end=boundary, speaker=0),
                SimpleNamespace(start=boundary, end=len(audio) / SAMPLE_RATE, speaker=1),
            ]

        result, events, diagnostic = self.run_main(
            self.write_manifest(paths),
            "--output-dir", str(output_dir),
            "--skip-silence",
            "--diarization",
            diarize_function=diarize,
        )

        self.assertEqual(result, 0, diagnostic)
        # The merged WAV goes through the single-input stages unchanged, so
        # --skip-silence runs its own speech detection on it.
        self.assertEqual(
            state_values(events),
            [
                "preparing",
                *MULTI_STAGES,
                "detecting_speech",
                "loading_model",
                "transcribing",
                "diarization",
                "saving_result",
            ],
        )
        completed = events[-1]
        self.assertIsInstance(completed, CompletedEvent)
        self.assertEqual(Path(completed.merged_audio_file).parent, output_dir.resolve())
        self.assertEqual(Path(completed.text_file).parent, output_dir.resolve())

        audio = self.engine.decoded_audio[0]
        self.assertEqual(len(diarized_audio), 1)
        np.testing.assert_array_equal(diarized_audio[0], audio)

        gap_start, gap_end = (int(value * SAMPLE_RATE) for value in GAP_SECONDS)
        first = loud_bounds(audio, 0, gap_start)
        second = loud_bounds(audio, gap_end)
        segments = [event for event in events if isinstance(event, SegmentEvent)]
        self.assertEqual(len(segments), 2)
        for segment, (start, end), speaker in zip(segments, (first, second), (1, 2)):
            self.assertAlmostEqual(segment.start, start / SAMPLE_RATE, places=6)
            self.assertAlmostEqual(segment.end, end / SAMPLE_RATE, places=6)
            self.assertEqual(segment.speaker, speaker)
        self.assertGreaterEqual(segments[1].start, GAP_SECONDS[1] - 0.03)
        self.assertLessEqual(segments[0].end, GAP_SECONDS[0] + 0.03)

        srt = Path(completed.srt_file).read_text(encoding="utf-8-sig")
        for segment in segments:
            self.assertIn(
                f"{transcribe_npu.srt_timestamp(segment.start)} --> "
                f"{transcribe_npu.srt_timestamp(segment.end)}",
                srt,
            )
        text = Path(completed.text_file).read_text(encoding="utf-8-sig")
        self.assertIn("화자 1", text)
        self.assertIn("화자 2", text)

    def test_reviewed_plan_is_the_transcribed_plan(self) -> None:
        paths = self.write_inputs()
        manifest_path = self.write_manifest(
            paths,
            overrides=[{"start_sample": 0, "end_sample": 2 * SAMPLE_RATE, "input_index": 1}],
        )
        reviewed = multimic_quality.plan_channel_selection(
            backend_bridge.load_multi_input_manifest(manifest_path)
        )

        result, events, diagnostic = self.run_main(manifest_path)

        self.assertEqual(result, 0, diagnostic)
        history = json.loads(Path(events[-1].selection_file).read_text(encoding="utf-8"))
        self.assertEqual(
            [
                (item["start_sample"], item["end_sample"], item["selected_input_index"])
                for item in history["segments"]
            ],
            [
                (item.start_sample, item.end_sample, item.selected_input_index)
                for item in reviewed.selection.segments
            ],
        )
        self.assertTrue(any(item["manual_override"] for item in history["segments"]))
        # Before the later microphone starts, the override has no recording.
        warnings = [event.message for event in events if isinstance(event, WarningEvent)]
        self.assertTrue(any("manual_override_unavailable" in message for message in warnings))
        self.assertTrue(any("무음으로 채워" in message for message in warnings))

    def test_unresolved_alignment_is_a_user_error_with_stderr_detail(self) -> None:
        paths = self.write_inputs(gap=False, noisy_reference=True)

        result, events, diagnostic = self.run_main(self.write_manifest(paths))

        self.assertEqual(result, 1, diagnostic)
        self.assertEqual(
            state_values(events),
            ["preparing", "decoding_audio", "aligning_audio"],
        )
        error = events[-1]
        self.assertIsInstance(error, ErrorEvent)
        self.assertTrue(error.message.startswith("Time alignment is unresolved for 발표자 근처"))
        self.assertIn("manual offset", error.message)
        self.assertNotIn("Traceback", error.message)
        self.assertIn("Unresolved multi-input alignment:", diagnostic)
        self.assertIn('"input_index": 1', diagnostic)
        self.assertIn("Traceback", diagnostic)
        self.assertEqual(self.engine.model_load_count, 0)
        self.assert_no_multi_input_outputs(self.recordings)

        # The reviewed manual offset resolves the same recordings.
        result, events, diagnostic = self.run_main(
            self.write_manifest(paths, manual_offsets={"1": LATER_START_MS})
        )

        self.assertEqual(result, 0, diagnostic)
        alignment = json.loads(Path(events[-1].alignment_file).read_text(encoding="utf-8"))
        self.assertEqual(alignment["inputs"][1]["correction_method"], "manual_offset")
        self.assertEqual(alignment["inputs"][1]["offset_ms"], float(LATER_START_MS))

    def test_unrelated_third_recording_blocks_merge_and_keeps_sources(self) -> None:
        paths = self.write_inputs(gap=False)
        unrelated_path = self.recordings / "다른 회의.wav"
        unrelated_master = speech_like_master(seed=987, duration_seconds=16.0)
        write_source_wav(
            unrelated_path,
            unrelated_master[3 * SAMPLE_RATE : 12 * SAMPLE_RATE],
        )
        paths.append(unrelated_path)
        sources_before = {
            path: multimic.fingerprint_file(path) for path in paths
        }

        manifest_path = self.write_manifest(paths)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["inputs"].append(
            {"path": str(unrelated_path), "label": "다른 회의"}
        )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )

        result, events, diagnostic = self.run_main(manifest_path)

        self.assertEqual(result, 1, diagnostic)
        self.assertEqual(
            state_values(events),
            ["preparing", "decoding_audio", "aligning_audio"],
        )
        error = events[-1]
        self.assertIsInstance(error, ErrorEvent)
        self.assertTrue(
            error.message.startswith(
                "Time alignment is unresolved for 다른 회의"
            ),
            error.message,
        )
        self.assertIn("manual offset", error.message)
        self.assertNotIn("Traceback", error.message)
        self.assertIn("Unresolved multi-input alignment:", diagnostic)
        self.assertIn('"input_index": 2', diagnostic)
        self.assertIn("Traceback", diagnostic)
        self.assertEqual(self.engine.model_load_count, 0)
        self.assert_no_multi_input_outputs(self.recordings)
        self.assertEqual(
            {path: multimic.fingerprint_file(path) for path in paths},
            sources_before,
        )

    def test_undecodable_input_is_a_user_error_with_stderr_detail(self) -> None:
        paths = self.write_inputs()
        paths[1].write_bytes(b"not an audio stream")

        result, events, diagnostic = self.run_main(self.write_manifest(paths))

        self.assertEqual(result, 1, diagnostic)
        self.assertEqual(state_values(events), ["preparing", "decoding_audio"])
        error = events[-1]
        self.assertIsInstance(error, ErrorEvent)
        self.assertTrue(error.message.startswith("Multi-input audio could not be prepared. 발표자 근처: "))
        self.assertNotIn(str(paths[1]), error.message)
        self.assertIn("Multi-input preparation diagnostics:", diagnostic)
        self.assertIn('"input_index": 1', diagnostic)
        self.assert_no_multi_input_outputs(self.recordings)

    def test_override_beyond_reference_is_rejected_before_alignment(self) -> None:
        paths = self.write_inputs()
        manifest_path = self.write_manifest(
            paths,
            overrides=[{"start_sample": 0, "end_sample": 9 * SAMPLE_RATE + 1, "input_index": 1}],
        )

        result, events, diagnostic = self.run_main(manifest_path)

        self.assertEqual(result, 2, diagnostic)
        self.assertEqual(state_values(events), ["preparing", "decoding_audio"])
        self.assertEqual(
            events[-1],
            ErrorEvent(
                "Invalid bridge arguments: multi-input manifest selection.overrides[0] "
                f"ends after the reference timeline ({9 * SAMPLE_RATE} samples)."
            ),
        )

    def test_missing_model_directory_fails_before_preprocessing(self) -> None:
        paths = self.write_inputs()
        self.model_dir.rmdir()

        result, events, diagnostic = self.run_main(self.write_manifest(paths))

        self.assertEqual(result, 3, diagnostic)
        self.assertEqual(state_values(events), ["preparing"])
        self.assertIsInstance(events[-1], ErrorEvent)
        self.assertEqual(self.engine.decoded_paths, [])
        self.assert_no_multi_input_outputs(self.recordings)


class SingleInputRegressionTests(unittest.TestCase):
    def test_single_input_keeps_its_stages_and_completed_contract(self) -> None:
        engine = MergedAudioEngine()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "회의.wav"
            model_dir = root / "model"
            model_dir.mkdir()
            write_source_wav(input_path, speech_like_master(seed=3, duration_seconds=4.0))
            output = StringIO()
            diagnostic = StringIO()

            result = backend_bridge.main(
                ["--input", str(input_path), "--model-dir", str(model_dir), "--device", "CPU"],
                engine_module=engine,
                stdout=output,
                stderr=diagnostic,
            )
            names = sorted(path.name for path in root.iterdir())
            expected = CompletedEvent(
                str((root / "회의.txt").resolve()),
                str((root / "회의.srt").resolve()),
            )
            resolved_input = input_path.resolve()

        self.assertEqual(result, 0, diagnostic.getvalue())
        events = validate_stream(output.getvalue().splitlines(), result)
        self.assertEqual(
            state_values(events),
            ["preparing", "decoding_audio", "loading_model", "transcribing", "saving_result"],
        )
        self.assertEqual(events[-1], expected)
        self.assertFalse(events[-1].is_multi_input)
        self.assertEqual(engine.decoded_paths, [resolved_input])
        self.assertEqual(names, ["model", "회의.srt", "회의.txt", "회의.wav"])


if __name__ == "__main__":
    unittest.main()
