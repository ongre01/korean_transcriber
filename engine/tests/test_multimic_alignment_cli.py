from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import multimic_alignment


class Serializable:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls = 0

    def to_dict(self) -> dict[str, object]:
        self.calls += 1
        return dict(self.payload)


class FakeReport:
    def __init__(
        self,
        reference_index: int,
        input_results: tuple[Serializable, ...],
        *,
        success: bool,
    ) -> None:
        self.reference_index = reference_index
        self.input_results = input_results
        self.success = success

    def to_dict(self) -> dict[str, object]:
        return {
            "reference_index": self.reference_index,
            "sample_rate": 16_000,
            "success": self.success,
            "reference_windows": [
                {"start_sample": 0, "end_sample": 12_000},
            ],
            "inputs": [item.to_dict() for item in self.input_results],
        }


class FakePreparationError(ValueError):
    def __init__(
        self,
        message: str,
        failures: tuple[Serializable, ...],
        warnings: tuple[Serializable, ...] = (),
    ) -> None:
        super().__init__(message)
        self.failures = failures
        self.warnings = warnings


def normalized_manifest() -> dict[str, object]:
    return {
        "version": 1,
        "inputs": [
            {"path": "C:/audio/mic-a.wav", "label": "테이블"},
            {"path": "C:/audio/mic-b.wav", "label": "발표자"},
        ],
        "reference_index": 0,
        "alignment": {"mode": "auto", "manual_offsets_ms": {}},
        "selection": {
            "mode": "automatic",
            "window_seconds": 3.0,
            "hop_seconds": 1.0,
        },
    }


class MultiMicAlignmentCliTests(unittest.TestCase):
    def test_success_keeps_per_input_alignment_failures_at_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_paths = (root / "mic-a.wav", root / "mic-b.wav")
            for path in input_paths:
                path.write_bytes(b"placeholder")

            manifest = {
                "version": 1,
                "inputs": [
                    {"path": input_paths[0].name, "label": "테이블 중앙"},
                    {"path": input_paths[1].name, "label": "발표자 근처"},
                ],
                "reference_index": 0,
                "alignment": {"mode": "auto", "manual_offsets_ms": {}},
                "selection": {
                    "mode": "automatic",
                    "window_seconds": 3.0,
                    "hop_seconds": 1.0,
                },
            }
            manifest_path = root / "alignment.json"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )

            reference = Serializable(
                {
                    "input_index": 0,
                    "reference_index": 0,
                    "success": True,
                    "offset_samples": 0,
                    "offset_ms": 0.0,
                    "confidence": 1.0,
                    "status": "aligned",
                }
            )
            rejected = Serializable(
                {
                    "input_index": 1,
                    "reference_index": 0,
                    "success": False,
                    "offset_samples": None,
                    "offset_ms": None,
                    "confidence": 0.2,
                    "status": "failed",
                    "failure_code": "low_correlation",
                    "failure_reason": "No reliable shared event was found.",
                }
            )
            report = FakeReport(0, (reference, rejected), success=False)
            preparation_warning = Serializable(
                {"code": "example_warning", "message": "Example warning."}
            )
            prepared = SimpleNamespace(warnings=(preparation_warning,))
            calls: dict[str, object] = {}

            def prepare(paths):
                print("decoder diagnostic")
                calls["paths"] = list(paths)
                return prepared

            def align(value, *, reference_index):
                calls["prepared"] = value
                calls["reference_index"] = reference_index
                return report

            engine = SimpleNamespace(
                MultiAudioPreparationError=FakePreparationError,
                prepare_multi_audio_inputs=prepare,
                align_prepared_inputs=align,
            )
            output = StringIO()
            diagnostic = StringIO()

            exit_code = multimic_alignment.main(
                ["--multi-input-manifest", str(manifest_path)],
                stdout=output,
                stderr=diagnostic,
                multimic_module=engine,
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(output.getvalue().splitlines()), 1)
            self.assertNotIn("decoder diagnostic", output.getvalue())
            self.assertIn("decoder diagnostic", diagnostic.getvalue())
            self.assertIn("테이블 중앙", output.getvalue())
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["ok"])
            self.assertFalse(payload["alignment"]["success"])
            self.assertEqual(
                payload["alignment"]["inputs"][1]["failure_code"],
                "low_correlation",
            )
            self.assertEqual(payload["warnings"], [preparation_warning.payload])
            self.assertEqual(
                calls["paths"],
                [str(path.resolve()) for path in input_paths],
            )
            self.assertIs(calls["prepared"], prepared)
            self.assertEqual(calls["reference_index"], 0)
            self.assertEqual(reference.calls, 1)
            self.assertEqual(rejected.calls, 1)

    def test_preparation_failure_has_per_file_errors_and_nonzero_exit(self) -> None:
        failure = Serializable(
            {
                "input_index": 1,
                "path": "C:/audio/mic-b.wav",
                "code": "decode_failed",
                "message": "The selected audio stream could not be decoded.",
            }
        )
        warning = Serializable(
            {
                "code": "recommended_input_limit_exceeded",
                "message": "Many files were selected.",
            }
        )

        def prepare(_paths):
            raise FakePreparationError(
                "[decode_failed] decode failed",
                (failure,),
                (warning,),
            )

        engine = SimpleNamespace(
            MultiAudioPreparationError=FakePreparationError,
            prepare_multi_audio_inputs=prepare,
            align_prepared_inputs=lambda *_args, **_kwargs: self.fail(
                "alignment must not run after preparation failure"
            ),
        )
        output = StringIO()

        exit_code = multimic_alignment.main(
            ["--multi-input-manifest", "request.json"],
            stdout=output,
            stderr=StringIO(),
            multimic_module=engine,
            manifest_loader=lambda _path: normalized_manifest(),
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["kind"], "preparation")
        self.assertEqual(payload["errors"], [failure.payload])
        self.assertEqual(payload["warnings"], [warning.payload])

    def test_argument_failure_is_one_contract_object(self) -> None:
        output = StringIO()
        diagnostic = StringIO()

        exit_code = multimic_alignment.main(
            [],
            stdout=output,
            stderr=diagnostic,
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["kind"], "contract")
        self.assertEqual(payload["code"], "invalid_arguments")
        self.assertNotIn("usage:", output.getvalue())

    def test_alignment_contract_error_is_nonzero(self) -> None:
        engine = SimpleNamespace(
            MultiAudioPreparationError=FakePreparationError,
            prepare_multi_audio_inputs=lambda _paths: SimpleNamespace(warnings=()),
            align_prepared_inputs=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                ValueError("reference_index is outside the input range")
            ),
        )
        output = StringIO()

        exit_code = multimic_alignment.main(
            ["--multi-input-manifest", "request.json"],
            stdout=output,
            stderr=StringIO(),
            multimic_module=engine,
            manifest_loader=lambda _path: normalized_manifest(),
        )

        self.assertEqual(exit_code, 2)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["kind"], "contract")
        self.assertEqual(payload["code"], "alignment_contract_error")

    def test_non_finite_result_becomes_strict_unexpected_error(self) -> None:
        class NonFiniteReport:
            @staticmethod
            def to_dict() -> dict[str, object]:
                return {"success": True, "confidence": float("nan")}

        engine = SimpleNamespace(
            MultiAudioPreparationError=FakePreparationError,
            prepare_multi_audio_inputs=lambda _paths: SimpleNamespace(warnings=()),
            align_prepared_inputs=lambda *_args, **_kwargs: NonFiniteReport(),
        )
        output = StringIO()
        diagnostic = StringIO()

        exit_code = multimic_alignment.main(
            ["--multi-input-manifest", "request.json"],
            stdout=output,
            stderr=diagnostic,
            multimic_module=engine,
            manifest_loader=lambda _path: normalized_manifest(),
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertNotIn("NaN", output.getvalue())
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["kind"], "unexpected")
        self.assertEqual(payload["code"], "alignment_failed")
        self.assertIn("Out of range float values", diagnostic.getvalue())


if __name__ == "__main__":
    unittest.main()
