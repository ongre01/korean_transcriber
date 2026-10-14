from __future__ import annotations

from io import StringIO
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import backend_bridge
from backend_protocol import ErrorEvent, validate_stream


class BackendBridgeDependencyTests(unittest.TestCase):
    def run_without_site_packages(self, *arguments: str):
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        return subprocess.run(
            [sys.executable, "-S", str(ENGINE_DIR / "backend_bridge.py"), *arguments],
            cwd=ENGINE_DIR,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )

    def assert_dependency_guidance(self, event: ErrorEvent, module: str) -> None:
        self.assertIsInstance(event, ErrorEvent)
        self.assertIn(f"Required Python module is not installed: {module}", event.message)
        self.assertIn(sys.executable, event.message)
        self.assertIn(str(ENGINE_DIR / "setup.bat"), event.message)
        self.assertIn(str(ENGINE_DIR / ".venv" / "Scripts" / "python.exe"), event.message)
        self.assertNotIn("Traceback", event.message)

    def test_missing_numpy_at_process_start_emits_terminal_protocol_error(self) -> None:
        result = self.run_without_site_packages(
            "--input", "input.wav", "--model-dir", "models", "--skip-silence",
        )

        self.assertEqual(result.returncode, 1, result.stderr)
        events = validate_stream(result.stdout.splitlines(), result.returncode)
        self.assertEqual(len(events), 1)
        self.assert_dependency_guidance(events[0], "numpy")
        self.assertIn("Traceback", result.stderr)
        self.assertIn("ModuleNotFoundError: No module named 'numpy'", result.stderr)

    def test_help_keeps_existing_protocol_behavior_without_runtime_dependencies(self) -> None:
        result = self.run_without_site_packages("--help")

        self.assertEqual(result.returncode, 2, result.stderr)
        events = validate_stream(result.stdout.splitlines(), result.returncode)
        self.assertEqual(events, [ErrorEvent("Invalid bridge arguments: help requested")])
        self.assertIn("usage:", result.stderr)
        self.assertIn("--skip-silence", result.stderr)
        self.assertNotIn("ModuleNotFoundError", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_import_diagnostics_do_not_contaminate_json_output(self) -> None:
        output = StringIO()
        diagnostic = StringIO()

        def failed_import(_name: str):
            print("runtime import diagnostic")
            raise ModuleNotFoundError("No module named 'numpy'", name="numpy")

        with patch.object(backend_bridge.importlib, "import_module", side_effect=failed_import):
            result = backend_bridge.main(
                ["--input", "input.wav", "--model-dir", "models"],
                stdout=output,
                stderr=diagnostic,
            )

        events = validate_stream(output.getvalue().splitlines(), result)
        self.assertEqual(result, 1)
        self.assertEqual(len(events), 1)
        self.assert_dependency_guidance(events[0], "numpy")
        self.assertIn("runtime import diagnostic", diagnostic.getvalue())
        self.assertIn("ModuleNotFoundError", diagnostic.getvalue())

    def test_missing_lazy_decoder_dependency_reports_environment_guidance(self) -> None:
        output = StringIO()
        diagnostic = StringIO()

        def failed_decode(_path: Path):
            raise ModuleNotFoundError("No module named 'av'", name="av")

        engine = SimpleNamespace(
            read_optional_text=lambda _path: "",
            available_devices=lambda: ["CPU"],
            decode_audio_16k_mono=failed_decode,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.wav"
            model_dir = root / "model"
            input_path.write_bytes(b"audio")
            model_dir.mkdir()
            result = backend_bridge.main(
                ["--input", str(input_path), "--model-dir", str(model_dir)],
                engine_module=engine,
                stdout=output,
                stderr=diagnostic,
            )

        events = validate_stream(output.getvalue().splitlines(), result)
        self.assertEqual(result, 1)
        self.assert_dependency_guidance(events[-1], "av")
        self.assertIn("ModuleNotFoundError", diagnostic.getvalue())


if __name__ == "__main__":
    unittest.main()
