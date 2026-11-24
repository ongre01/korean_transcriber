from __future__ import annotations

import _thread
from contextlib import contextmanager, redirect_stderr
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

import numpy as np


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from nemotron import NemotronConfig, parse_transcript, transcribe_audio, validate_config


class TranscriptTests(unittest.TestCase):
    def test_word_times_preserve_korean_punctuation_and_convert_speakers(self):
        result = parse_transcript({
            "text": "<spk:0> 안녕하세요. <spk:1> 오늘 회의입니다!",
            "words": [
                {"word": "안녕하세요", "start": 0.2, "end": 1.0, "speaker": 1},
                {"word": "오늘", "start": 1.2, "end": 1.8, "speaker": 2},
                {"word": "회의입니다", "start": 1.9, "end": 2.5, "speaker": 2},
            ],
        }, 3.0, diarization=True)
        self.assertEqual([(s.start, s.end, s.text, s.speaker) for s in result], [
            (0.2, 1.0, "안녕하세요.", 0),
            (1.2, 2.5, "오늘 회의입니다!", 1),
        ])

    def test_diarization_off_omits_speaker_ids(self):
        result = parse_transcript({"text": "hello", "words": [
            {"word": "hello", "start": 0.0, "end": 0.5, "speaker": 2},
        ]}, 1.0)
        self.assertIsNone(result[0].speaker)

    def test_silence_has_no_segments_or_warning(self):
        warnings = []
        self.assertEqual(parse_transcript({"text": "", "words": []}, 1.0, warning_callback=warnings.append), [])
        self.assertEqual(warnings, [])

    def test_partial_or_missing_alignment_retains_every_word(self):
        for words in ([], [{"word": "hello", "start": 0.1, "end": 0.5}],
                      [{"word": "world", "start": 0.6, "end": 0.9}]):
            with self.subTest(words=words):
                warnings = []
                result = parse_transcript({"text": "hello world", "words": words}, 1.0,
                                          warning_callback=warnings.append)
                self.assertEqual([(s.start, s.end, s.text, s.speaker) for s in result],
                                 [(0.0, 1.0, "hello world", None)])
                self.assertIn("complete word alignment", warnings[0])

    def test_invalid_schema_or_timestamps_are_rejected(self):
        payloads = [[], {}, {"text": "hello", "words": {}}, {"text": 2, "words": []}]
        for field, value in (("start", float("nan")), ("start", True), ("start", -1),
                             ("end", -1), ("end", 20), ("speaker", 0), ("speaker", "spk:0")):
            word = {"word": "hello", "start": 0.1, "end": 0.5, field: value}
            payloads.append({"text": "hello", "words": [word]})
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_transcript(payload, 1.0)

    def test_missing_speaker_is_reported(self):
        warnings = []
        result = parse_transcript({"text": "hello", "words": [
            {"word": "hello", "start": 0.0, "end": 0.5},
        ]}, 1.0, diarization=True, warning_callback=warnings.append)
        self.assertIsNone(result[0].speaker)
        self.assertIn("unknown speaker", warnings[0])

    def test_overlapping_korean_turns_keep_native_times_speakers_and_punctuation(self):
        # Native CLI text stays in decoder order, but its words are sorted by
        # time (salm_recognizer.cpp::to_asr_result), so speakers interleave.
        warnings = []
        result = parse_transcript({
            "text": "오늘 안건을 설명하겠습니다. 잠깐 질문이 있습니다!",
            "words": [
                {"word": "오늘", "start": 0.2, "end": 0.4, "speaker": 1},
                {"word": "안건을", "start": 0.5, "end": 1.0, "speaker": 1},
                {"word": "잠깐", "start": 0.65, "end": 1.05, "speaker": 2},
                {"word": "설명하겠습니다", "start": 1.1, "end": 2.0, "speaker": 1},
                {"word": "질문이", "start": 1.2, "end": 1.4, "speaker": 2},
                {"word": "있습니다", "start": 1.5, "end": 2.1, "speaker": 2},
            ],
        }, 3.0, diarization=True, warning_callback=warnings.append)
        self.assertEqual([(s.start, s.end, s.text, s.speaker) for s in result], [
            (0.2, 2.0, "오늘 안건을 설명하겠습니다.", 0),
            (0.65, 2.1, "잠깐 질문이 있습니다!", 1),
        ])
        self.assertEqual(warnings, [])

    def test_overlapping_repetitions_within_one_speaker_match_in_order(self):
        words = [
            {"word": "네", "start": 0.1, "end": 0.2, "speaker": 1},
            {"word": "질문", "start": 0.15, "end": 0.3, "speaker": 2},
            {"word": "네", "start": 0.25, "end": 0.35, "speaker": 1},
            {"word": "있습니다", "start": 0.4, "end": 0.8, "speaker": 2},
        ]
        result = parse_transcript({"text": "네 네. 질문 있습니다.", "words": words},
                                  1.0, diarization=True)
        self.assertEqual([(s.start, s.end, s.text, s.speaker) for s in result], [
            (0.1, 0.35, "네 네.", 0), (0.15, 0.8, "질문 있습니다.", 1),
        ])
        warnings = []
        result = parse_transcript({"text": "네 네 네. 질문 있습니다.", "words": words},
                                  1.0, diarization=True, warning_callback=warnings.append)
        self.assertEqual(result[0].text, "네 네 네. 질문 있습니다.")
        self.assertIsNone(result[0].speaker)
        self.assertEqual(len(warnings), 1)

    def test_ambiguous_cross_speaker_repeated_word_retains_full_transcript(self):
        warnings = []
        text = "네 감사합니다. 네 질문입니다."
        result = parse_transcript({"text": text, "words": [
            {"word": "네", "start": 0.1, "end": 0.2, "speaker": 1},
            {"word": "네", "start": 0.15, "end": 0.3, "speaker": 2},
            {"word": "감사합니다", "start": 0.25, "end": 0.35, "speaker": 1},
            {"word": "질문입니다", "start": 0.4, "end": 0.8, "speaker": 2},
        ]}, 1.0, diarization=True, warning_callback=warnings.append)
        self.assertEqual([(s.start, s.end, s.text, s.speaker) for s in result], [(0, 1, text, None)])
        self.assertIn("unambiguously", warnings[0])


FAKE_RUNTIME = r'''
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import wave

capture, mode = Path(sys.argv[1]), sys.argv[2]
args = sys.argv[3:]
assert args[0] == "transcribe", args
with wave.open(args[1], "rb") as audio:
    info = {"pid": os.getpid(), "arguments": args, "rate": audio.getframerate(), "channels": audio.getnchannels(),
            "width": audio.getsampwidth(), "frames": audio.getnframes()}
    info["pcm_sha256"] = hashlib.sha256(audio.readframes(audio.getnframes())).hexdigest()
capture.write_text(json.dumps(info), encoding="utf-8")
if mode == "sleep":
    while True:
        time.sleep(1)
if mode == "error":
    sys.stderr.write("decoder companion encoder GGUF missing\n")
    sys.exit(3)
if mode == "unsupported_offload":
    sys.stderr.write("nemo-speech transcribe: config 'memory settings.yaml' line 10: unknown key 'asr.salm.n_cpu_moe'\n")
    sys.exit(2)
if mode == "invalid":
    print("not JSON")
else:
    print(json.dumps({"text": "안녕하세요.", "words": [
        {"word": "안녕하세요", "start": 0.1, "end": 0.9, "speaker": 1}]}))
'''


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="nemotron-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.model = self.directory / "모델 with spaces.gguf"
        self.model.write_bytes(b"GGUF")
        self.yaml = self.directory / "memory settings.yaml"
        self.yaml.write_text("asr: {}\n", encoding="utf-8")
        self.config = NemotronConfig(self.model, sys.executable, self.yaml)
        self.fake = self.directory / "fake runtime.py"
        self.fake.write_text(FAKE_RUNTIME, encoding="utf-8")
        self.capture = self.directory / "arguments.json"
        self.captures = []
        self.processes = []
        self.audio = np.zeros(16000, dtype=np.float32)

    @contextmanager
    def fake_runtime(self, mode="success"):
        real_popen = subprocess.Popen

        def spawn(command, **kwargs):
            self.assertFalse(kwargs["shell"])
            # Match a native executable's single-process lifetime. On Windows
            # a venv python.exe can be a launcher with a separate interpreter.
            native_python = getattr(sys, "_base_executable", sys.executable)
            capture = self.capture if not self.captures else self.capture.with_stem(f"arguments-{len(self.captures) + 1}")
            self.captures.append(capture)
            process = real_popen([native_python, str(self.fake), str(capture), mode, *command[1:]], **kwargs)
            self.processes.append(process)
            return process

        with patch("nemotron.subprocess.Popen", side_effect=spawn):
            yield

    def test_real_child_receives_pcm_and_official_arguments(self):
        with self.fake_runtime():
            result = transcribe_audio(self.audio, 1.0, self.config, diarization=True,
                                      hotwords='CUDA, 한글 & $(literal)', initial_prompt="회의 문맥")
        info = json.loads(self.capture.read_text(encoding="utf-8"))
        self.assertEqual((info["rate"], info["channels"], info["width"], info["frames"]),
                         (16000, 1, 2, 16000))
        args = info["arguments"]
        self.assertEqual(args[args.index("--model") + 1], str(self.model))
        self.assertEqual(args[args.index("--config") + 1], str(self.yaml))
        self.assertEqual(args[args.index("--language") + 1], "ko")
        self.assertEqual(args[args.index("--format") + 1], "json")
        self.assertIn("--word-times", args)
        self.assertIn("--diarize", args)
        instruction = args[args.index("--instruction") + 1]
        self.assertIn("Korean", instruction)
        self.assertIn('CUDA, 한글 & $(literal)', instruction)
        self.assertIn("회의 문맥", instruction)
        self.assertIn("<spk:7>", instruction)
        self.assertEqual((result[0].text, result[0].speaker), ("안녕하세요.", 0))
        self.assertFalse(Path(args[1]).exists(), "temporary audio should be removed")

    def test_auto_language_and_empty_context_keep_native_default_prompt(self):
        with self.fake_runtime():
            transcribe_audio(self.audio, 1.0, self.config, language="auto")
        args = json.loads(self.capture.read_text(encoding="utf-8"))["arguments"]
        self.assertNotIn("--language", args)
        self.assertNotIn("--instruction", args)
        self.assertNotIn("--diarize", args)

    def test_bridge_chunks_real_pcm_and_publishes_merged_native_results(self):
        import backend_bridge
        from backend_protocol import CompletedEvent, SegmentEvent, validate_stream

        input_path = self.directory / "긴 회의.wav"
        source_pcm = (np.arange(64001, dtype=np.int64) % 60001 - 30000).astype("<i2")
        with wave.open(str(input_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(source_pcm.tobytes())
        source_bytes = input_path.read_bytes()
        output = self.directory / "results"
        stdout, stderr = io.StringIO(), io.StringIO()
        with self.fake_runtime(), patch.object(backend_bridge, "NEMOTRON_DEFAULT_CHUNK_SECONDS", 2), redirect_stderr(stderr):
            code = backend_bridge.main([
                "--input", str(input_path), "--engine", "nemotron",
                "--nemotron-executable", sys.executable,
                "--nemotron-model", str(self.model), "--output-dir", str(output), "--diarization",
            ], stdout=stdout, stderr=stderr)

        self.assertEqual(code, 0, stderr.getvalue())
        events = validate_stream(stdout.getvalue().splitlines(), code)
        captured = [json.loads(path.read_text(encoding="utf-8")) for path in self.captures]
        self.assertEqual([info["frames"] for info in captured], [21333, 21334, 21334])
        expected_pcm = np.rint(source_pcm.astype(np.float32) / 32768 * 32767).astype("<i2")
        offset = 0
        segments = [event for event in events if isinstance(event, SegmentEvent)]
        self.assertEqual(len(segments), 3)
        for index, (info, segment) in enumerate(zip(captured, segments)):
            end = offset + info["frames"]
            self.assertEqual((info["rate"], info["channels"], info["width"]), (16000, 1, 2))
            self.assertEqual(info["pcm_sha256"], hashlib.sha256(expected_pcm[offset:end].tobytes()).hexdigest())
            self.assertAlmostEqual(segment.start, offset / 16000 + 0.1)
            self.assertAlmostEqual(segment.end, offset / 16000 + 0.9)
            self.assertEqual((segment.speaker, segment.text), (index + 1, "안녕하세요."))
            self.assertFalse(Path(info["arguments"][1]).parent.exists())
            self.assertIsNotNone(self.processes[index].poll())
            offset = end
        self.assertEqual(offset, len(source_pcm))
        self.assertIsInstance(events[-1], CompletedEvent)
        self.assertEqual(Path(events[-1].text_file).read_text(encoding="utf-8-sig").count("안녕하세요."), 3)
        subtitles = Path(events[-1].srt_file).read_text(encoding="utf-8-sig")
        self.assertIn("00:00:01,433 --> 00:00:02,233", subtitles)
        self.assertIn("00:00:02,767 --> 00:00:03,567", subtitles)
        self.assertEqual({path.suffix for path in output.iterdir()}, {".txt", ".srt"})
        self.assertEqual(input_path.read_bytes(), source_bytes)

    def test_missing_local_resources_fail_before_process_launch(self):
        for config, message in (
            (NemotronConfig(self.model, "missing-nemo-executable-for-test"), "executable was not found"),
            (NemotronConfig(self.directory / "missing.gguf", sys.executable), "decoder GGUF"),
            (NemotronConfig(self.model, sys.executable, self.directory / "missing.yaml"), "YAML"),
        ):
            with self.subTest(config=config), self.assertRaisesRegex(ValueError, message):
                validate_config(config)

    def test_native_error_includes_actionable_diagnostic_and_cleans_audio(self):
        diagnostic = io.StringIO()
        with self.fake_runtime("error"), redirect_stderr(diagnostic):
            with self.assertRaisesRegex(RuntimeError, "companion encoder GGUF missing"):
                transcribe_audio(self.audio, 1.0, self.config)
        self.assertIn("companion encoder GGUF missing", diagnostic.getvalue())
        args = json.loads(self.capture.read_text(encoding="utf-8"))["arguments"]
        self.assertFalse(Path(args[1]).exists())

    def test_invalid_native_json_is_rejected(self):
        with self.fake_runtime("invalid"), self.assertRaisesRegex(ValueError, "invalid JSON"):
            transcribe_audio(self.audio, 1.0, self.config)

    def test_unsupported_offload_requests_runtime_upgrade_and_preserves_config(self):
        preset = "asr:\n  salm:\n    n_cpu_moe: 53\n"
        self.yaml.write_text(preset, encoding="utf-8")
        diagnostic = io.StringIO()
        with self.fake_runtime("unsupported_offload"), redirect_stderr(diagnostic):
            with self.assertRaisesRegex(RuntimeError, "CPU 오프로딩") as failure:
                transcribe_audio(self.audio, 1.0, self.config)
        self.assertIn("536b17a", str(failure.exception))
        self.assertIn("unknown key 'asr.salm.n_cpu_moe'", diagnostic.getvalue())
        self.assertEqual(self.yaml.read_text(encoding="utf-8"), preset)
        self.assertEqual(len(self.processes), 1, "must not retry without CPU offloading")
        args = json.loads(self.capture.read_text(encoding="utf-8"))["arguments"]
        self.assertFalse(Path(args[1]).parent.exists())

    def test_keyboard_interrupt_reaps_actual_child_and_removes_temporary_audio(self):
        finished = threading.Event()

        def interrupt_when_started():
            deadline = time.monotonic() + 10
            while not finished.wait(0.01) and time.monotonic() < deadline:
                if self.capture.exists():
                    _thread.interrupt_main()
                    return

        watcher = threading.Thread(target=interrupt_when_started, daemon=True)
        watcher.start()
        try:
            with self.fake_runtime("sleep"), self.assertRaises(KeyboardInterrupt):
                transcribe_audio(self.audio, 1.0, self.config)
        finally:
            finished.set()
            watcher.join(timeout=1)
            for process in self.processes:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=1)
                    self.fail("Nemotron child survived cancellation")
        self.assertEqual(len(self.processes), 1)
        self.assertIsNotNone(self.processes[0].returncode)
        args = json.loads(self.capture.read_text(encoding="utf-8"))["arguments"]
        self.assertFalse(Path(args[1]).parent.exists())

    def test_invalid_audio_never_launches_runtime(self):
        for audio, duration in ((np.zeros(10), 0.000625), (np.zeros((16000, 2)), 1),
                                (np.full(16000, np.nan), 1), (self.audio, 2)):
            with self.subTest(shape=audio.shape, duration=duration), patch("nemotron.subprocess.Popen") as spawn:
                with self.assertRaises(ValueError):
                    transcribe_audio(audio, duration, self.config)
                spawn.assert_not_called()

    def test_bridge_stdin_cancel_stops_native_child_and_preserves_existing_results(self):
        self.assert_bridge_stdin_cancel_stops_child(cancel_chunk=1)

    def test_bridge_stdin_cancel_on_second_chunk_reaps_child_without_publishing_first(self):
        self.assert_bridge_stdin_cancel_stops_child(cancel_chunk=2)

    def assert_bridge_stdin_cancel_stops_child(self, *, cancel_chunk):
        # Python serves as a stand-in native executable: it interprets the
        # positional CLI command, a script named "transcribe" in its cwd.
        # This exercises the real bridge, stdin watcher and adapter end to end,
        # without mocking process creation or the cancellation mechanism.
        from backend_protocol import CompletedEvent, ErrorEvent, SegmentEvent, validate_stream

        input_path = self.directory / "sample.wav"
        with wave.open(str(input_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(np.zeros(16000 if cancel_chunk == 1 else 64001, dtype="<i2").tobytes())
        source_bytes = input_path.read_bytes()
        output = self.directory / "results"
        output.mkdir()
        existing = output / "sample.txt"
        existing.write_text("previous result", encoding="utf-8")
        bootstrap = (
            "from pathlib import Path\nimport sys\n"
            f"capture_dir = Path({str(self.directory)!r})\n"
            "index = len(list(capture_dir.glob('native-*.json'))) + 1\n"
            f"mode = 'sleep' if index == {cancel_chunk} else 'success'\n"
            "sys.argv = [sys.argv[0], str(capture_dir / f'native-{index}.json'), mode, 'transcribe', *sys.argv[1:]]\n"
        )
        (self.directory / "transcribe").write_text(bootstrap + FAKE_RUNTIME, encoding="utf-8")
        # Lower only the request boundary so the real cancellation path can be
        # exercised on a later chunk without allocating an hour of audio.
        bridge_launcher = (
            f"import sys; sys.path.insert(0, {str(ENGINE_DIR)!r}); import backend_bridge; "
            "backend_bridge.NEMOTRON_DEFAULT_CHUNK_SECONDS = 2; "
            "raise SystemExit(backend_bridge.run_as_script())"
        )
        process = subprocess.Popen([
            sys.executable, "-c", bridge_launcher,
            "--input", str(input_path), "--engine", "nemotron",
            "--nemotron-executable", getattr(sys, "_base_executable", sys.executable),
            "--nemotron-model", str(self.model),
            "--output-dir", str(output),
        ], cwd=self.directory, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        captured = None
        capture = self.directory / f"native-{cancel_chunk}.json"
        try:
            deadline = time.monotonic() + 15
            while process.poll() is None and time.monotonic() < deadline:
                try:
                    captured = json.loads(capture.read_text(encoding="utf-8"))
                    break
                except (OSError, json.JSONDecodeError):
                    time.sleep(0.01)
            self.assertIsNotNone(captured, "native runtime did not start")
            process.stdin.write(b"cancel\n")
            process.stdin.flush()
            process.wait(timeout=3)
            stdout, stderr = process.communicate(timeout=2)
            self.assertEqual(process.returncode, 130, stderr.decode("utf-8", "replace"))
            events = validate_stream(stdout.decode("utf-8").splitlines(), process.returncode)
            self.assertEqual(events[-1], ErrorEvent("Transcription was cancelled."))
            self.assertFalse(any(isinstance(event, (SegmentEvent, CompletedEvent)) for event in events))
            self.assertNotIn("job did not stop in time", stderr.decode("utf-8", "replace"))
            captures = list(self.directory.glob("native-*.json"))
            self.assertEqual(len(captures), cancel_chunk)
            for path in captures:
                info = json.loads(path.read_text(encoding="utf-8"))
                self.assertFalse(Path(info["arguments"][1]).parent.exists())
            self.assertEqual(list(output.iterdir()), [existing])
            self.assertEqual(existing.read_text(encoding="utf-8"), "previous result")
            self.assertEqual(input_path.read_bytes(), source_bytes)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
            if captured is not None:
                self.assert_native_process_exited(captured["pid"])

    def assert_native_process_exited(self, pid):
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel32.WaitForSingleObject.restype = wintypes.DWORD
            kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel32.OpenProcess(0x00100001, False, pid)
            if handle:
                try:
                    result = kernel32.WaitForSingleObject(handle, 1000)
                    if result != 0:
                        kernel32.TerminateProcess(handle, 1)
                    self.assertEqual(result, 0, "native runtime survived bridge cancellation")
                finally:
                    kernel32.CloseHandle(handle)
        else:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            os.kill(pid, 9)
            self.fail("native runtime survived bridge cancellation")


if __name__ == "__main__":
    unittest.main()
