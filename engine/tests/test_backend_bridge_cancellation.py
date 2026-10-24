"""Cancellation and failure cleanup of the bridge (MM-07/MM-09).

A cancelled or failed job must leave the input recordings and any results of
an earlier job exactly as they were, and must not leave a partial or orphaned
result file under a final name.
"""

from __future__ import annotations

import contextlib
from io import BytesIO, StringIO
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import numpy as np


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import backend_bridge
from backend_bridge import CANCELLED_EXIT_CODE, CANCELLED_MESSAGE, CancellationWatcher
from backend_protocol import CompletedEvent, ErrorEvent, parse_event_line, validate_stream
import multimic
from result_staging import OWNER_LOCK_NAME, ResultStaging
import transcribe_npu
from test_backend_bridge_multi_pipeline import (
    MergedAudioEngine,
    MultiInputBridgeTestCase,
    state_values,
)
from test_multimic_align import speech_like_master
from test_multimic_quality_cli import write_source_wav


RESULT_NAMES = (
    multimic.ALIGNMENT_FILE_NAME,
    multimic.CHANNEL_SELECTION_FILE_NAME,
    multimic.MERGED_AUDIO_FILE_NAME,
    "merged_16k_mono.srt",
    "merged_16k_mono.txt",
)
# Qt kills the bridge this long after it requests cancellation.
QT_CANCELLATION_GRACE_SECONDS = 3.0


def interrupt(*_arguments, **_keyword_arguments):
    raise KeyboardInterrupt


def wait_for_process_exit(pid: int, timeout: float) -> None:
    """Wait for an interpreter that a venv ``python.exe`` launcher started."""

    if os.name != "nt":
        return
    import ctypes

    synchronize = 0x00100000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        return
    try:
        kernel32.WaitForSingleObject(handle, int(timeout * 1000))
    finally:
        kernel32.CloseHandle(handle)


class JobCleanupTestCase(MultiInputBridgeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.paths = self.write_inputs()
        self.manifest = self.write_manifest(self.paths)
        self.sources = {path: multimic.fingerprint_file(path) for path in self.paths}

    def publish_previous_results(self) -> dict[str, bytes]:
        """Publish an earlier job's results and mark them recognizably."""

        result, _events, diagnostic = self.run_main(self.manifest)
        self.assertEqual(result, 0, diagnostic)
        previous = {}
        for name in RESULT_NAMES:
            path = self.recordings / name
            path.write_bytes(f"earlier job: {name}".encode("utf-8"))
            previous[name] = path.read_bytes()
        return previous

    def assert_job_left_no_trace(self, previous: dict[str, bytes]) -> None:
        names = {path.name for path in self.recordings.iterdir()}
        self.assertFalse([name for name in names if name.endswith(".partial")], names)
        for name in RESULT_NAMES:
            if name in previous:
                self.assertEqual((self.recordings / name).read_bytes(), previous[name], name)
            else:
                self.assertNotIn(name, names)
        self.assertEqual(
            {path: multimic.fingerprint_file(path) for path in self.paths},
            self.sources,
        )


class MultiInputCancellationTests(JobCleanupTestCase):
    def test_cancellation_in_each_stage_keeps_inputs_and_earlier_results(self) -> None:
        previous = self.publish_previous_results()

        def partial_wav(path, _samples, **_options):
            Path(path).write_bytes(b"RIFF partial")
            raise KeyboardInterrupt

        def partial_save(_input_path, _segments, _duration, _suffix, *, diarized, output_dir):
            del diarized
            (Path(output_dir) / "merged_16k_mono.txt").write_text("부분", encoding="utf-8")
            raise KeyboardInterrupt

        cases = {
            "aligning_audio": (
                mock.patch("multimic.pipeline.align_prepared_inputs", side_effect=interrupt),
                (),
            ),
            "rendering_merged_audio": (
                mock.patch("multimic.pipeline.write_pcm16_wav", side_effect=partial_wav),
                (),
            ),
            "transcribing": (
                mock.patch.object(self.engine, "transcribe_windows", side_effect=interrupt, create=True),
                (),
            ),
            "diarization": (contextlib.nullcontext(), ("--diarization",)),
            "saving_result": (
                mock.patch.object(self.engine, "save_outputs", side_effect=partial_save, create=True),
                (),
            ),
        }
        for stage, (patch, arguments) in cases.items():
            with self.subTest(stage=stage), patch:
                result, events, diagnostic = self.run_main(
                    self.manifest,
                    *arguments,
                    diarize_function=interrupt,
                )

                self.assertEqual(result, CANCELLED_EXIT_CODE, diagnostic)
                self.assertEqual(state_values(events)[-1], stage)
                self.assertEqual(events[-1], ErrorEvent(CANCELLED_MESSAGE))
                self.assertIn("KeyboardInterrupt", diagnostic)
                self.assert_job_left_no_trace(previous)

    def test_failure_after_rendering_publishes_no_file_of_the_job(self) -> None:
        previous = self.publish_previous_results()

        with mock.patch.object(
            self.engine,
            "load_whisper_pipeline",
            side_effect=RuntimeError("model blob is missing"),
        ):
            result, events, diagnostic = self.run_main(self.manifest)

        self.assertEqual(result, 1, diagnostic)
        self.assertIn("rendering_merged_audio", state_values(events))
        self.assertEqual(
            events[-1],
            ErrorEvent("Whisper model could not be loaded: model blob is missing"),
        )
        self.assertIn("Traceback", diagnostic)
        self.assert_job_left_no_trace(previous)

    def test_failure_on_a_fresh_folder_leaves_only_the_recordings(self) -> None:
        with mock.patch.object(
            self.engine,
            "transcribe_windows",
            side_effect=RuntimeError("inference failed"),
            create=True,
        ):
            result, events, diagnostic = self.run_main(self.manifest)

        self.assertEqual(result, 1, diagnostic)
        self.assertEqual(events[-1], ErrorEvent("Transcription failed: inference failed"))
        self.assertEqual(sorted(self.recordings.iterdir()), sorted(self.paths))

    def test_result_file_that_cannot_be_replaced_rolls_back_the_whole_set(self) -> None:
        previous = self.publish_previous_results()
        real_replace = os.replace
        recordings = self.recordings.resolve()

        def text_file_is_locked(source, target):
            if Path(source).name == "merged_16k_mono.txt" and Path(target).parent == recordings:
                raise PermissionError("merged_16k_mono.txt is open in another program")
            return real_replace(source, target)

        with mock.patch("result_staging.os.replace", side_effect=text_file_is_locked):
            result, events, diagnostic = self.run_main(self.manifest)

        self.assertEqual(result, 1, diagnostic)
        self.assertIsInstance(events[-1], ErrorEvent)
        self.assertTrue(events[-1].message.startswith("Result files could not be saved: "))
        self.assert_job_left_no_trace(previous)

    def test_failure_messages_name_the_failed_kind_of_processing(self) -> None:
        """Synchronization, conversion and model failures stay distinguishable.

        Qt maps these documented message prefixes to separate user messages;
        decode and unresolved-alignment failures are covered in
        ``test_backend_bridge_multi_pipeline``.
        """

        failure = RuntimeError("boom")
        merged_decode = mock.Mock(side_effect=[failure])
        cases = [
            ("multimic.pipeline.align_prepared_inputs", "Multi-input time alignment failed: boom"),
            ("multimic.quality.analyze_aligned_speech", "Multi-input speech detection failed: boom"),
            ("multimic.quality.score_aligned_quality", "Channel quality analysis failed: boom"),
            ("multimic.select.select_channels", "Channel selection failed: boom"),
            ("multimic.pipeline.render_merged_audio", "Merged audio could not be created: boom"),
        ]
        for target, message in cases:
            with self.subTest(target=target), mock.patch(target, side_effect=failure):
                result, events, diagnostic = self.run_main(self.manifest)
                self.assertEqual(result, 1, diagnostic)
                self.assertEqual(events[-1], ErrorEvent(message))
                self.assertIn("Traceback", diagnostic)
                self.assert_job_left_no_trace({})

        with mock.patch.object(self.engine, "decode_audio_16k_mono", merged_decode):
            result, events, diagnostic = self.run_main(self.manifest)
        self.assertEqual(result, 1, diagnostic)
        self.assertEqual(events[-1], ErrorEvent("Merged audio could not be decoded: boom"))
        self.assert_job_left_no_trace({})


class SingleInputCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.root = Path(temporary_directory.name)
        self.input_path = self.root / "회의.wav"
        write_source_wav(self.input_path, speech_like_master(seed=3, duration_seconds=4.0))
        self.source = multimic.fingerprint_file(self.input_path)
        self.model_dir = self.root / "model"
        self.model_dir.mkdir()
        self.engine = MergedAudioEngine()

    def run_main(self):
        output = StringIO()
        diagnostic = StringIO()
        result = backend_bridge.main(
            ["--input", str(self.input_path), "--model-dir", str(self.model_dir), "--device", "CPU"],
            engine_module=self.engine,
            stdout=output,
            stderr=diagnostic,
        )
        return result, validate_stream(output.getvalue().splitlines(), result), diagnostic.getvalue()

    def assert_folder(self, expected: dict[str, bytes | None]) -> None:
        names = sorted(path.name for path in self.root.iterdir())
        self.assertEqual(names, sorted(["model", "회의.wav", *expected]))
        for name, contents in expected.items():
            if contents is not None:
                self.assertEqual((self.root / name).read_bytes(), contents, name)
        self.assertEqual(multimic.fingerprint_file(self.input_path), self.source)

    def test_interrupted_save_leaves_no_partial_result(self) -> None:
        (self.root / "회의.txt").write_bytes(b"earlier result")

        def partial_save(_input_path, _segments, _duration, _suffix, *, diarized, output_dir):
            del diarized
            (Path(output_dir) / "회의.txt").write_text("부분 결과", encoding="utf-8")
            raise OSError("disk full")

        self.engine.save_outputs = partial_save
        result, events, diagnostic = self.run_main()

        self.assertEqual(result, 1, diagnostic)
        self.assertEqual(events[-1], ErrorEvent("Result files could not be saved: disk full"))
        self.assert_folder({"회의.txt": b"earlier result"})

    def test_cancelled_transcription_keeps_the_input(self) -> None:
        self.engine.transcribe_windows = interrupt
        result, events, diagnostic = self.run_main()

        self.assertEqual(result, CANCELLED_EXIT_CODE, diagnostic)
        self.assertEqual(events[-1], ErrorEvent(CANCELLED_MESSAGE))
        self.assert_folder({})

    def test_successful_job_publishes_both_files_without_staging(self) -> None:
        result, events, diagnostic = self.run_main()

        self.assertEqual(result, 0, diagnostic)
        self.assertEqual(
            events[-1],
            CompletedEvent(
                str((self.root / "회의.txt").resolve()),
                str((self.root / "회의.srt").resolve()),
            ),
        )
        self.assert_folder({"회의.srt": None, "회의.txt": None})


class CancellationWatcherTests(unittest.TestCase):
    def test_cancel_line_interrupts_the_job_once(self) -> None:
        interrupted = threading.Event()
        terminate = mock.Mock()
        watcher = CancellationWatcher(
            BytesIO(b"progress\ncancel\r\ncancel\n"),
            interrupt=interrupted.set,
            terminate=terminate,
            unwind_seconds=10.0,
        )

        watcher.start()
        self.assertTrue(interrupted.wait(5.0))
        watcher.finish()
        watcher.join(5.0)

        self.assertTrue(watcher.requested)
        terminate.assert_not_called()

    def test_end_of_input_is_not_a_cancellation(self) -> None:
        interrupt_main = mock.Mock()
        watcher = CancellationWatcher(BytesIO(b"other\n"), interrupt=interrupt_main)

        watcher.start()
        watcher.join(5.0)

        self.assertFalse(watcher.requested)
        interrupt_main.assert_not_called()

    def test_cancel_after_the_job_returned_is_ignored(self) -> None:
        interrupt_main = mock.Mock()
        watcher = CancellationWatcher(BytesIO(b"cancel\n"), interrupt=interrupt_main)
        watcher.finish()

        watcher.start()
        watcher.join(5.0)

        interrupt_main.assert_not_called()

    def test_job_that_cannot_unwind_is_cleaned_up_and_ended(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging = ResultStaging(directory, prefix="multimic")
            staging.path(multimic.MERGED_AUDIO_FILE_NAME).write_bytes(b"staged audio")
            exit_codes = []
            diagnostic = StringIO()
            watcher = CancellationWatcher(
                BytesIO(b"cancel\n"),
                interrupt=lambda: None,
                terminate=exit_codes.append,
                unwind_seconds=0.05,
                diagnostic=diagnostic,
            )

            watcher.start()
            watcher.join(5.0)

            self.assertEqual(exit_codes, [CANCELLED_EXIT_CODE])
            self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertIn("did not stop in time", diagnostic.getvalue())


DRIVER = r'''
import os
import sys
import time
from pathlib import Path

engine_directory, marker, mode, *arguments = sys.argv[1:]
sys.path[:0] = [engine_directory, str(Path(engine_directory) / "tests")]

import backend_bridge
from test_backend_bridge_multi_pipeline import MergedAudioEngine


class BlockingEngine(MergedAudioEngine):
    """Blocks in Whisper inference until the job is cancelled or killed."""

    def transcribe_windows(self, *_arguments, **_keyword_arguments):
        Path(marker).write_text(str(os.getpid()), encoding="utf-8")
        while True:
            try:
                time.sleep(0.02)
            except KeyboardInterrupt:
                if mode == "cooperative":
                    raise
                # "stuck" behaves like native inference that never returns.


raise SystemExit(backend_bridge.run_as_script(arguments, engine_module=BlockingEngine()))
'''


class BridgeProcessCancellationTests(JobCleanupTestCase):
    """The real script entry point, cancelled through stdin like Qt does."""

    def start_bridge(self, mode: str) -> tuple[subprocess.Popen, int]:
        driver = self.root / "bridge_driver.py"
        driver.write_text(DRIVER, encoding="utf-8")
        marker = self.root / "transcribing.pid"
        process = subprocess.Popen(
            [
                sys.executable,
                str(driver),
                str(ENGINE_DIR),
                str(marker),
                mode,
                "--multi-input-manifest", str(self.manifest),
                "--model-dir", str(self.model_dir),
                "--device", "CPU",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.root,
        )
        self.addCleanup(self.stop, process)
        deadline = time.monotonic() + 120.0
        while not marker.is_file() or not marker.read_text(encoding="utf-8"):
            if process.poll() is not None or time.monotonic() > deadline:
                _output, errors = process.communicate()
                self.fail(errors.decode("utf-8", "replace"))
            time.sleep(0.05)
        return process, int(marker.read_text(encoding="utf-8"))

    @staticmethod
    def stop(process: subprocess.Popen) -> None:
        if process.poll() is None:
            process.kill()
        process.communicate()

    def cancel(self, process: subprocess.Popen) -> tuple[list[str], str, float]:
        started = time.monotonic()
        process.stdin.write(b"cancel\n")
        process.stdin.flush()
        output, errors = process.communicate(timeout=30.0)
        elapsed = time.monotonic() - started
        lines = output.decode("utf-8").splitlines()
        return lines, errors.decode("utf-8", "replace"), elapsed

    def assert_cancelled_while_transcribing(self, lines: list[str]) -> None:
        # Transcription starts only once the merged WAV has been staged.
        self.assertEqual(state_values_from_lines(lines)[-1], "transcribing")

    def test_cancel_request_unwinds_the_job_before_qt_would_kill_it(self) -> None:
        process, _pid = self.start_bridge("cooperative")

        lines, errors, elapsed = self.cancel(process)

        self.assertEqual(process.returncode, CANCELLED_EXIT_CODE, errors)
        self.assertLess(elapsed, QT_CANCELLATION_GRACE_SECONDS, errors)
        events = validate_stream(lines, process.returncode)
        self.assertEqual(events[-1], ErrorEvent(CANCELLED_MESSAGE))
        self.assert_cancelled_while_transcribing(lines)
        self.assertIn("KeyboardInterrupt", errors)
        self.assert_job_left_no_trace({})

    def test_job_stuck_in_native_code_is_cleaned_up_and_ended(self) -> None:
        process, _pid = self.start_bridge("stuck")

        lines, errors, elapsed = self.cancel(process)

        self.assertEqual(process.returncode, CANCELLED_EXIT_CODE, errors)
        self.assertLess(elapsed, QT_CANCELLATION_GRACE_SECONDS, errors)
        self.assertIn("did not stop in time", errors)
        events = [parse_event_line(line.encode("utf-8")) for line in lines]
        self.assertFalse([event for event in events if isinstance(event, CompletedEvent)])
        self.assert_cancelled_while_transcribing(lines)
        self.assert_job_left_no_trace({})

    def test_killed_job_leaves_staging_that_the_next_job_removes(self) -> None:
        process, pid = self.start_bridge("cooperative")

        process.kill()
        process.communicate(timeout=30.0)
        wait_for_process_exit(pid, 30.0)

        leftovers = [path for path in self.recordings.iterdir() if path.name.endswith(".partial")]
        self.assertEqual(len(leftovers), 1)
        self.assertEqual(
            sorted(path.name for path in leftovers[0].iterdir()),
            sorted([OWNER_LOCK_NAME, *RESULT_NAMES[:3]]),
        )
        self.assertEqual(
            sorted(self.recordings.iterdir()),
            sorted([*self.paths, leftovers[0]]),
        )

        result, events, diagnostic = self.run_main(self.manifest)

        self.assertEqual(result, 0, diagnostic)
        self.assertIsInstance(events[-1], CompletedEvent)
        self.assertFalse(leftovers[0].exists())
        self.assertEqual(
            sorted(path.name for path in self.recordings.iterdir()),
            sorted([*(path.name for path in self.paths), *RESULT_NAMES]),
        )


def state_values_from_lines(lines: list[str]) -> list[str]:
    return state_values(parse_event_line(line.encode("utf-8")) for line in lines)


if __name__ == "__main__":
    unittest.main()
