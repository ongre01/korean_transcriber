from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import download_nemotron as downloader


class DownloadNemotronTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.payload = b"complete model bytes"
        self.checks = {"model.safetensors": (
            len(self.payload), hashlib.sha256(self.payload).hexdigest(),
        )}
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(patch.stopall)
        patch.object(downloader, "FILE_CHECKS", self.checks).start()
        patch.object(downloader, "REQUIRED_FILES", ("config.json",)).start()

    def populate(self):
        (self.directory / "model.safetensors").write_bytes(self.payload)
        (self.directory / "config.json").write_text("{}", encoding="utf-8")

    def run_main(self, *arguments):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return downloader.main(["--output-dir", str(self.directory), *arguments])

    def test_verify_only_is_offline_and_accepts_matching_files(self):
        self.populate()
        with patch.dict(sys.modules, {"huggingface_hub": None}):
            self.assertEqual(self.run_main("--verify-only"), 0)

    def test_same_size_corruption_is_rejected(self):
        self.populate()
        (self.directory / "model.safetensors").write_bytes(b"x" * len(self.payload))
        self.assertEqual(self.run_main("--verify-only"), 1)

    def test_incomplete_download_is_rejected(self):
        self.populate()
        (self.directory / "model.safetensors").write_bytes(self.payload[:-1])
        self.assertEqual(self.run_main("--verify-only"), 1)

    def test_missing_configuration_is_rejected(self):
        self.populate()
        (self.directory / "config.json").unlink()
        self.assertEqual(self.run_main("--verify-only"), 1)

    def test_retry_then_verify_downloaded_files(self):
        def succeed(**kwargs):
            self.assertEqual(kwargs["repo_id"], downloader.REPO_ID)
            self.assertEqual(kwargs["revision"], downloader.REVISION)
            self.populate()
        download = Mock(side_effect=[OSError("temporary network failure"), None])
        def attempt(**kwargs):
            result = download(**kwargs)
            succeed(**kwargs)
            return result
        fake_hub = types.SimpleNamespace(snapshot_download=attempt)
        with patch.dict(sys.modules, {"huggingface_hub": fake_hub}), patch.object(downloader.time, "sleep"):
            self.assertEqual(self.run_main(), 0)
        self.assertEqual(download.call_count, 2)

    def test_keyboard_interrupt_does_not_retry_or_delete_files(self):
        self.populate()
        download = Mock(side_effect=KeyboardInterrupt)
        with patch.dict(sys.modules, {"huggingface_hub": types.SimpleNamespace(snapshot_download=download)}):
            self.assertEqual(self.run_main(), 130)
        download.assert_called_once()
        self.assertEqual((self.directory / "model.safetensors").read_bytes(), self.payload)

    def test_no_network_transfer_when_disk_is_full(self):
        download = Mock()
        with patch.dict(sys.modules, {"huggingface_hub": types.SimpleNamespace(snapshot_download=download)}), \
                patch.object(downloader.shutil, "disk_usage", return_value=types.SimpleNamespace(free=0)):
            self.assertEqual(self.run_main(), 1)
        download.assert_not_called()

    def test_dry_run_does_not_create_destination_or_import_sdk(self):
        target = self.directory / "not-created"
        with patch.dict(sys.modules, {"huggingface_hub": None}), redirect_stdout(io.StringIO()):
            self.assertEqual(downloader.main(["--output-dir", str(target), "--dry-run"]), 0)
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
