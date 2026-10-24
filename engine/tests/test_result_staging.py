from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from result_staging import (
    OWNER_LOCK_NAME,
    UNLOCKED_STAGING_GRACE_SECONDS,
    ResultStaging,
    discard_active_staging,
    remove_abandoned_staging,
)


def staging_names(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir() if path.name.endswith(".partial"))


class ResultStagingTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.root = Path(temporary_directory.name)
        self.output = self.root / "결과"

    def test_nothing_is_created_before_the_first_result_file(self) -> None:
        with ResultStaging(self.output, prefix="transcribe") as staging:
            self.assertEqual(staging.output_directory, self.output.resolve())
            self.assertEqual(staging.final_path("a.txt"), self.output.resolve() / "a.txt")
        self.assertFalse(self.output.exists())

    def test_published_set_replaces_previous_results_and_staging_is_removed(self) -> None:
        self.output.mkdir()
        (self.output / "result.txt").write_text("이전 결과", encoding="utf-8")

        with ResultStaging(self.output, prefix="transcribe") as staging:
            staging.path("result.srt").write_text("1\n", encoding="utf-8")
            staging.path("result.txt").write_text("새 결과", encoding="utf-8")
            staging_directory = staging.directory
            self.assertTrue(staging_directory.name.startswith(".transcribe_"))
            self.assertTrue((staging_directory / OWNER_LOCK_NAME).is_file())
            # Until publishing, the final names still show the previous run.
            self.assertEqual((self.output / "result.txt").read_text(encoding="utf-8"), "이전 결과")

            published = staging.publish(["result.srt", "result.txt"])

        self.assertEqual(
            published,
            [self.output.resolve() / "result.srt", self.output.resolve() / "result.txt"],
        )
        self.assertEqual((self.output / "result.txt").read_text(encoding="utf-8"), "새 결과")
        self.assertFalse(staging_directory.exists())
        self.assertEqual(sorted(path.name for path in self.output.iterdir()), ["result.srt", "result.txt"])

    def test_failed_publish_restores_every_replaced_file(self) -> None:
        self.output.mkdir()
        (self.output / "a.json").write_text("old a", encoding="utf-8")
        (self.output / "c.txt").write_text("old c", encoding="utf-8")
        real_replace = os.replace
        targets = []

        def fail_on_last_file(source, target):
            targets.append(Path(target).name)
            if Path(source).name == "c.txt" and Path(target).parent == self.output.resolve():
                raise PermissionError("c.txt is open in another program")
            return real_replace(source, target)

        with ResultStaging(self.output, prefix="multimic") as staging:
            for name in ("a.json", "b.wav", "c.txt"):
                staging.path(name).write_text(f"new {name}", encoding="utf-8")
            with mock.patch("result_staging.os.replace", side_effect=fail_on_last_file):
                with self.assertRaises(PermissionError):
                    staging.publish(["a.json", "b.wav", "c.txt"])

        self.assertEqual((self.output / "a.json").read_text(encoding="utf-8"), "old a")
        self.assertEqual((self.output / "c.txt").read_text(encoding="utf-8"), "old c")
        self.assertFalse((self.output / "b.wav").exists())
        self.assertEqual(staging_names(self.output), [])

    def test_publish_rejects_missing_duplicate_and_nested_names(self) -> None:
        with ResultStaging(self.output, prefix="transcribe") as staging:
            staging.path("a.txt").write_text("a", encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                staging.publish(["a.txt", "missing.srt"])
            with self.assertRaises(ValueError):
                staging.publish(["a.txt", "a.txt"])
            for name in ("", "..", "sub/a.txt"):
                with self.assertRaises(ValueError, msg=name):
                    staging.path(name)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_discarded_staging_cannot_be_reused(self) -> None:
        staging = ResultStaging(self.output, prefix="transcribe")
        staging.path("a.txt").write_text("a", encoding="utf-8")
        staging.discard()
        staging.discard()
        with self.assertRaises(RuntimeError):
            staging.path("a.txt")
        self.assertEqual(list(self.output.iterdir()), [])
        with self.assertRaises(ValueError):
            ResultStaging(self.output, prefix="other")

    def test_discard_active_staging_removes_every_open_job(self) -> None:
        first = ResultStaging(self.output, prefix="multimic")
        second = ResultStaging(self.output, prefix="transcribe")
        first.path("merged_16k_mono.wav").write_bytes(b"partial")
        second.path("result.txt").write_text("partial", encoding="utf-8")
        unused = ResultStaging(self.output, prefix="transcribe")

        discard_active_staging()

        self.assertEqual(list(self.output.iterdir()), [])
        with self.assertRaises(RuntimeError):
            first.directory
        unused.discard()

    def test_abandoned_staging_is_removed_and_running_jobs_are_kept(self) -> None:
        self.output.mkdir()
        # A job that was killed: its lock file is no longer held open.
        abandoned = self.output / ".multimic_killed1.partial"
        abandoned.mkdir()
        (abandoned / OWNER_LOCK_NAME).write_bytes(b"")
        (abandoned / "merged_16k_mono.wav").write_bytes(b"partial")
        # A staging directory from before owner locks, long untouched.
        legacy = self.output / ".multimic_legacy1.partial"
        legacy.mkdir()
        old = time.time() - UNLOCKED_STAGING_GRACE_SECONDS - 5.0
        os.utime(legacy, (old, old))
        # Without a lock but brand new: possibly a job that is starting.
        starting = self.output / ".transcribe_new0001.partial"
        starting.mkdir()
        # Names this module never creates are not touched.
        unrelated = [self.output / ".other_x.partial", self.output / "notes.partial"]
        for path in unrelated:
            path.mkdir()
        (self.output / ".transcribe_file.partial").write_text("not a directory", encoding="utf-8")

        output = self.output.resolve()
        running = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys\n"
                f"sys.path.insert(0, {str(ENGINE_DIR)!r})\n"
                "from result_staging import ResultStaging\n"
                f"staging = ResultStaging({str(output)!r}, prefix='transcribe')\n"
                "print(staging.directory, flush=True)\n"
                "sys.stdin.read()\n",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        try:
            running_directory = Path(running.stdout.readline().strip())
            # The other process's staging creation already swept the folder.
            self.assertFalse(abandoned.exists())
            self.assertFalse(legacy.exists())

            removed = remove_abandoned_staging(output)

            self.assertEqual(removed, [])
            self.assertTrue(running_directory.is_dir())
            self.assertTrue(starting.is_dir())
            for path in unrelated:
                self.assertTrue(path.is_dir())
        finally:
            running.stdin.close()
            running.wait(30)
            running.stdout.close()

        # Once that job has ended without cleaning up, its directory is
        # stale.  A virtual environment's python.exe is a launcher, so the
        # interpreter holding the lock can outlive it by a moment.
        self.assertTrue(running_directory.is_dir())
        removed = []
        deadline = time.monotonic() + 10.0
        while not removed and time.monotonic() < deadline:
            removed = remove_abandoned_staging(output)
            if not removed:
                time.sleep(0.05)
        self.assertEqual(removed, [running_directory])
        self.assertTrue(starting.is_dir())

    def test_own_staging_is_never_swept(self) -> None:
        with ResultStaging(self.output, prefix="multimic") as staging:
            staging.path("merged_16k_mono.wav").write_bytes(b"audio")
            with ResultStaging(self.output, prefix="transcribe") as other:
                other.path("result.txt").write_text("x", encoding="utf-8")
                self.assertTrue(staging.path("merged_16k_mono.wav").is_file())
            self.assertEqual(remove_abandoned_staging(self.output), [])
            self.assertTrue(staging.directory.is_dir())

    def test_missing_output_folder_is_ignored_by_the_sweep(self) -> None:
        self.assertEqual(remove_abandoned_staging(self.root / "missing"), [])


if __name__ == "__main__":
    unittest.main()
