from __future__ import annotations

from io import StringIO
import json
import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import backend_bridge
from backend_protocol import ErrorEvent, validate_stream


class BackendBridgeMultiInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.model_dir = self.root / "model"
        self.model_dir.mkdir()
        self.input_paths = [
            self.root / "회의실 마이크 A.wav",
            self.root / "발표자 마이크 B.m4a",
        ]
        for path in self.input_paths:
            path.write_bytes(b"audio")

    def valid_manifest(self) -> dict[str, object]:
        return {
            "version": 1,
            "inputs": [
                {"path": str(self.input_paths[0]), "label": "테이블 중앙"},
                {"path": str(self.input_paths[1]), "label": "발표자 근처"},
            ],
            "reference_index": 0,
            "alignment": {
                "mode": "auto",
                "manual_offsets_ms": {"0": 0, "1": -125},
            },
            "selection": {
                "mode": "automatic",
                "window_seconds": 3.0,
                "hop_seconds": 1.0,
            },
        }

    def write_manifest(
        self,
        payload: object,
        *,
        name: str = "다중 입력 manifest.json",
    ) -> Path:
        path = self.root / name
        path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def parse_manifest(self, payload: object):
        manifest_path = self.write_manifest(payload)
        return backend_bridge.parse_arguments([
            "--multi-input-manifest", str(manifest_path),
            "--model-dir", str(self.model_dir),
        ])

    def assert_manifest_error(self, payload: object) -> ErrorEvent:
        manifest_path = self.write_manifest(payload)
        output = StringIO()
        diagnostic = StringIO()

        result = backend_bridge.main(
            [
                "--multi-input-manifest", str(manifest_path),
                "--model-dir", str(self.model_dir),
            ],
            engine_module=object(),
            stdout=output,
            stderr=diagnostic,
        )

        self.assertEqual(result, 2, diagnostic.getvalue())
        events = validate_stream(output.getvalue().splitlines(), result)
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], ErrorEvent)
        self.assertNotIn("Traceback", events[0].message)
        return events[0]

    def test_valid_manifest_file_is_loaded_and_normalized(self) -> None:
        manifest_path = self.write_manifest(self.valid_manifest())

        args = backend_bridge.parse_arguments([
            "--multi-input-manifest", str(manifest_path),
            "--model-dir", str(self.model_dir),
        ])

        self.assertIsNone(args.input)
        self.assertEqual(args.multi_input_manifest, str(manifest_path))
        self.assertEqual(args.multi_input_manifest_path, manifest_path.resolve())
        self.assertIsInstance(args.multi_input_manifest_data, dict)
        self.assertEqual(args.multi_input_manifest_data["version"], 1)
        self.assertEqual(len(args.multi_input_manifest_data["inputs"]), 2)
        self.assertEqual(
            args.multi_input_manifest_data["alignment"]["manual_offsets_ms"],
            {0: 0, 1: -125},
        )

    def test_valid_manifest_settings_reach_the_multi_input_pipeline(self) -> None:
        payload = self.valid_manifest()
        payload["selection"]["overrides"] = [
            {"start_sample": 0, "end_sample": 16_000, "input_index": 1},
        ]
        args = self.parse_manifest(payload)
        calls = []

        def run_multi_audio_preprocessing(paths, output_dir, **options):
            calls.append((paths, output_dir, options))
            options["on_stage"]("decoding_audio")
            raise RuntimeError("stopped by the test")

        multimic = SimpleNamespace(
            run_multi_audio_preprocessing=run_multi_audio_preprocessing,
            MultiAudioPreparationError=type("PreparationError", (Exception,), {}),
            UnresolvedAlignmentError=type("AlignmentError", (Exception,), {}),
            SelectionOverrideError=type("OverrideError", (Exception,), {}),
        )
        engine = SimpleNamespace(
            read_optional_text=lambda _path: "",
            available_devices=lambda: ["CPU"],
            decode_audio_16k_mono=mock.Mock(
                side_effect=AssertionError("no input may be transcribed on its own")
            ),
        )
        output = StringIO()

        with self.assertRaises(backend_bridge.BridgeFailure) as raised:
            backend_bridge.run_bridge(
                args,
                engine,
                backend_bridge.EventWriter(output),
                multimic_module=multimic,
            )

        self.assertEqual(
            str(raised.exception),
            "Multi-input audio decoding failed: stopped by the test",
        )
        engine.decode_audio_16k_mono.assert_not_called()
        self.assertEqual(len(calls), 1)
        paths, output_dir, options = calls[0]
        self.assertEqual(paths, [str(path.resolve()) for path in self.input_paths])
        self.assertEqual(output_dir, self.input_paths[0].resolve().parent)
        del options["on_stage"]
        self.assertEqual(
            options,
            {
                "reference_index": 0,
                "manual_offsets_ms": {0: 0, 1: -125},
                "window_seconds": 3.0,
                "hop_seconds": 1.0,
                "overrides": [
                    {"start_sample": 0, "end_sample": 16_000, "input_index": 1},
                ],
                "input_labels": ["테이블 중앙", "발표자 근처"],
            },
        )

    def test_manifest_accepts_more_than_recommended_input_count(self) -> None:
        payload = self.valid_manifest()
        inputs = []
        for index in range(9):
            path = self.root / f"microphone_{index}.wav"
            path.write_bytes(b"audio")
            inputs.append({"path": str(path), "label": f"Mic {index}"})
        payload["inputs"] = inputs
        payload["alignment"] = {"mode": "auto", "manual_offsets_ms": {}}

        args = self.parse_manifest(payload)

        self.assertEqual(len(args.multi_input_manifest_data["inputs"]), 9)

    def test_relative_input_paths_are_resolved_from_manifest_directory(self) -> None:
        manifest_directory = self.root / "job with spaces"
        recordings_directory = manifest_directory / "recordings"
        recordings_directory.mkdir(parents=True)
        relative_paths = [Path("recordings/mic-a.wav"), Path("recordings/mic-b.wav")]
        for relative_path in relative_paths:
            (manifest_directory / relative_path).write_bytes(b"audio")
        payload = self.valid_manifest()
        for item, relative_path in zip(payload["inputs"], relative_paths):
            item["path"] = relative_path.as_posix()
        manifest_path = manifest_directory / "manifest.json"
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

        args = backend_bridge.parse_arguments([
            "--multi-input-manifest", str(manifest_path),
            "--model-dir", str(self.model_dir),
        ])

        self.assertEqual(
            [item["path"] for item in args.multi_input_manifest_data["inputs"]],
            [str((manifest_directory / path).resolve()) for path in relative_paths],
        )

    def test_single_input_cli_defaults_remain_unchanged(self) -> None:
        args = backend_bridge.parse_arguments([
            "--input", str(self.input_paths[0]),
            "--model-dir", str(self.model_dir),
        ])

        self.assertEqual(args.input, str(self.input_paths[0]))
        self.assertIsNone(args.multi_input_manifest)
        self.assertIsNone(args.multi_input_manifest_path)
        self.assertIsNone(args.multi_input_manifest_data)
        self.assertEqual(args.device, "AUTO")
        self.assertEqual(args.beams, 1)
        self.assertEqual(args.window_seconds, backend_bridge.DEFAULT_WINDOW_SECONDS)
        self.assertEqual(args.overlap_seconds, backend_bridge.DEFAULT_OVERLAP_SECONDS)

    def test_input_modes_are_required_and_mutually_exclusive(self) -> None:
        manifest_path = self.write_manifest(self.valid_manifest())
        cases = {
            "neither mode": ["--model-dir", str(self.model_dir)],
            "both modes": [
                "--input", str(self.input_paths[0]),
                "--multi-input-manifest", str(manifest_path),
                "--model-dir", str(self.model_dir),
            ],
        }

        for name, arguments in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(backend_bridge.BridgeArgumentError):
                    backend_bridge.parse_arguments(arguments)

    def test_manifest_file_errors_use_argument_error_protocol(self) -> None:
        cases: dict[str, Path] = {
            "missing": self.root / "missing.json",
            "directory": self.root,
        }
        malformed = self.root / "malformed.json"
        malformed.write_text('{"version":', encoding="utf-8")
        cases["malformed JSON"] = malformed
        non_utf8 = self.root / "non-utf8.json"
        non_utf8.write_bytes(b"\xff\xfe\x00")
        cases["non-UTF-8"] = non_utf8

        for name, manifest_path in cases.items():
            with self.subTest(name=name):
                output = StringIO()
                diagnostic = StringIO()
                result = backend_bridge.main(
                    [
                        "--multi-input-manifest", str(manifest_path),
                        "--model-dir", str(self.model_dir),
                    ],
                    engine_module=object(),
                    stdout=output,
                    stderr=diagnostic,
                )

                self.assertEqual(result, 2, diagnostic.getvalue())
                events = validate_stream(output.getvalue().splitlines(), result)
                self.assertEqual(len(events), 1)
                self.assertIsInstance(events[0], ErrorEvent)
                self.assertNotIn("Traceback", events[0].message)

    def test_manifest_json_decoder_edge_cases_use_argument_error_protocol(self) -> None:
        duplicate_field = self.root / "duplicate-field.json"
        valid_document = json.dumps(self.valid_manifest(), ensure_ascii=False)
        duplicate_field.write_text(
            valid_document.replace('"version": 1', '"version": 1, "version": 1', 1),
            encoding="utf-8",
        )
        oversized_integer = self.root / "oversized-integer.json"
        oversized_integer.write_text(
            '{"version": ' + ("9" * 5_000) + '}',
            encoding="utf-8",
        )

        for name, manifest_path in (
            ("duplicate field", duplicate_field),
            ("integer digit limit", oversized_integer),
        ):
            with self.subTest(name=name):
                output = StringIO()
                diagnostic = StringIO()
                result = backend_bridge.main(
                    [
                        "--multi-input-manifest", str(manifest_path),
                        "--model-dir", str(self.model_dir),
                    ],
                    engine_module=object(),
                    stdout=output,
                    stderr=diagnostic,
                )

                self.assertEqual(result, 2, diagnostic.getvalue())
                events = validate_stream(output.getvalue().splitlines(), result)
                self.assertEqual(len(events), 1)
                self.assertIsInstance(events[0], ErrorEvent)

    def test_utf8_bom_manifest_is_accepted(self) -> None:
        manifest_path = self.root / "bom-manifest.json"
        document = json.dumps(self.valid_manifest(), ensure_ascii=False).encode("utf-8")
        manifest_path.write_bytes(b"\xef\xbb\xbf" + document)

        args = backend_bridge.parse_arguments([
            "--multi-input-manifest", str(manifest_path),
            "--model-dir", str(self.model_dir),
        ])

        self.assertEqual(args.multi_input_manifest_data["version"], 1)

    def test_manifest_rejects_invalid_top_level_and_input_fields(self) -> None:
        cases: list[tuple[str, object]] = []

        payload = self.valid_manifest()
        del payload["version"]
        cases.append(("missing version", payload))

        for value in (2, "1", True, 1.0):
            payload = self.valid_manifest()
            payload["version"] = value
            cases.append((f"invalid version {value!r}", payload))

        cases.append(("non-object root", []))

        payload = self.valid_manifest()
        del payload["inputs"]
        cases.append(("missing inputs", payload))

        payload = self.valid_manifest()
        payload["inputs"] = payload["inputs"][:1]
        cases.append(("fewer than two inputs", payload))

        payload = self.valid_manifest()
        payload["inputs"] = "not a list"
        cases.append(("inputs is not a list", payload))

        payload = self.valid_manifest()
        payload["inputs"][0] = "not an object"
        cases.append(("input is not an object", payload))

        for field in ("path", "label"):
            payload = self.valid_manifest()
            del payload["inputs"][0][field]
            cases.append((f"missing input {field}", payload))

            payload = self.valid_manifest()
            payload["inputs"][0][field] = "   "
            cases.append((f"blank input {field}", payload))

            payload = self.valid_manifest()
            payload["inputs"][0][field] = 7
            cases.append((f"non-string input {field}", payload))

        payload = self.valid_manifest()
        payload["inputs"][0]["path"] = str(self.root / "not-found.wav")
        cases.append(("missing input path", payload))

        payload = self.valid_manifest()
        payload["inputs"][0]["path"] = str(self.root)
        cases.append(("input path is a directory", payload))

        payload = self.valid_manifest()
        payload["inputs"][1]["path"] = payload["inputs"][0]["path"]
        cases.append(("duplicate normalized input path", payload))

        payload = self.valid_manifest()
        payload["unexpected"] = True
        cases.append(("unknown root field", payload))

        payload = self.valid_manifest()
        payload["inputs"][0]["enabled"] = True
        cases.append(("unknown input field", payload))

        payload = self.valid_manifest()
        payload["alignment"]["confidence"] = 0.9
        cases.append(("unknown alignment field", payload))

        payload = self.valid_manifest()
        payload["selection"]["strategy"] = "automatic"
        cases.append(("unknown selection field", payload))

        for name, invalid_payload in cases:
            with self.subTest(name=name):
                self.assert_manifest_error(invalid_payload)

    def test_manifest_rejects_invalid_reference_index(self) -> None:
        cases: list[tuple[str, object]] = []

        payload = self.valid_manifest()
        del payload["reference_index"]
        cases.append(("missing", payload))

        for value in (-1, 2, 0.0, "0", True):
            payload = self.valid_manifest()
            payload["reference_index"] = value
            cases.append((repr(value), payload))

        for name, invalid_payload in cases:
            with self.subTest(value=name):
                self.assert_manifest_error(invalid_payload)

    def test_manifest_rejects_invalid_alignment_and_manual_offsets(self) -> None:
        cases: list[tuple[str, object]] = []

        payload = self.valid_manifest()
        del payload["alignment"]
        cases.append(("missing alignment", payload))

        payload = self.valid_manifest()
        payload["alignment"] = "auto"
        cases.append(("alignment is not an object", payload))

        payload = self.valid_manifest()
        payload["alignment"]["mode"] = "manual"
        cases.append(("unknown alignment mode", payload))

        payload = self.valid_manifest()
        del payload["alignment"]["manual_offsets_ms"]
        cases.append(("missing manual offsets", payload))

        payload = self.valid_manifest()
        payload["alignment"]["manual_offsets_ms"] = []
        cases.append(("manual offsets is not an object", payload))

        for key in ("-1", "2", "01", "input-a"):
            payload = self.valid_manifest()
            payload["alignment"]["manual_offsets_ms"] = {key: 0}
            cases.append((f"invalid manual offset index {key!r}", payload))

        for value in (
            True,
            1.5,
            "10",
            math.nan,
            math.inf,
            -math.inf,
            2**63,
            -(2**63) - 1,
        ):
            payload = self.valid_manifest()
            payload["alignment"]["manual_offsets_ms"] = {"0": value}
            cases.append((f"invalid manual offset value {value!r}", payload))

        for name, invalid_payload in cases:
            with self.subTest(name=name):
                self.assert_manifest_error(invalid_payload)

    def test_manifest_rejects_invalid_selection_fields(self) -> None:
        cases: list[tuple[str, object]] = []

        payload = self.valid_manifest()
        del payload["selection"]
        cases.append(("missing selection", payload))

        payload = self.valid_manifest()
        payload["selection"] = "automatic"
        cases.append(("selection is not an object", payload))

        payload = self.valid_manifest()
        payload["selection"]["mode"] = "loudest"
        cases.append(("unknown selection mode", payload))

        for field in ("window_seconds", "hop_seconds"):
            payload = self.valid_manifest()
            del payload["selection"][field]
            cases.append((f"missing {field}", payload))

            for value in (
                0,
                -1.0,
                True,
                "1",
                math.nan,
                math.inf,
                -math.inf,
                10**1000,
            ):
                payload = self.valid_manifest()
                payload["selection"][field] = value
                cases.append((f"invalid {field} {value!r}", payload))

        payload = self.valid_manifest()
        payload["selection"]["hop_seconds"] = 3.5
        cases.append(("hop is greater than window", payload))

        for name, invalid_payload in cases:
            with self.subTest(name=name):
                self.assert_manifest_error(invalid_payload)

    def test_selection_overrides_are_optional_and_normalized_in_time_order(self) -> None:
        args = self.parse_manifest(self.valid_manifest())
        self.assertEqual(args.multi_input_manifest_data["selection"]["overrides"], [])

        payload = self.valid_manifest()
        payload["selection"]["overrides"] = [
            {"input_index": 0, "start_sample": 48_000, "end_sample": 64_000},
            {"start_sample": 0, "end_sample": 16_000, "input_index": 1},
            {"start_sample": 16_000, "end_sample": 32_000, "input_index": 1},
        ]

        args = self.parse_manifest(payload)

        self.assertEqual(
            args.multi_input_manifest_data["selection"]["overrides"],
            [
                {"start_sample": 0, "end_sample": 16_000, "input_index": 1},
                {"start_sample": 16_000, "end_sample": 32_000, "input_index": 1},
                {"start_sample": 48_000, "end_sample": 64_000, "input_index": 0},
            ],
        )

    def test_manifest_rejects_invalid_selection_overrides(self) -> None:
        def with_overrides(overrides: object) -> dict[str, object]:
            payload = self.valid_manifest()
            payload["selection"]["overrides"] = overrides
            return payload

        valid = {"start_sample": 0, "end_sample": 16_000, "input_index": 1}
        cases: list[tuple[str, object]] = [
            ("overrides is not an array", with_overrides({"0": valid})),
            ("override is not an object", with_overrides(["0:16000:1"])),
            ("unknown override field", with_overrides([{**valid, "reason": "x"}])),
            (
                "end before start",
                with_overrides([{**valid, "start_sample": 16_000, "end_sample": 0}]),
            ),
            ("empty interval", with_overrides([{**valid, "end_sample": 0}])),
            ("input index outside inputs", with_overrides([{**valid, "input_index": 2}])),
            ("negative input index", with_overrides([{**valid, "input_index": -1}])),
            (
                "overlapping overrides",
                with_overrides(
                    [
                        valid,
                        {"start_sample": 15_999, "end_sample": 20_000, "input_index": 0},
                    ]
                ),
            ),
        ]
        for field in ("start_sample", "end_sample", "input_index"):
            missing = dict(valid)
            del missing[field]
            cases.append((f"missing {field}", with_overrides([missing])))
            for value in (True, 1.0, "1", None, math.inf):
                cases.append(
                    (
                        f"invalid {field} {value!r}",
                        with_overrides([{**valid, field: value}]),
                    )
                )
        for field in ("start_sample", "end_sample"):
            cases.append(
                (f"negative {field}", with_overrides([{**valid, field: -1}]))
            )
            cases.append(
                (f"oversized {field}", with_overrides([{**valid, field: 2**63}]))
            )

        for name, invalid_payload in cases:
            with self.subTest(name=name):
                self.assert_manifest_error(invalid_payload)


if __name__ == "__main__":
    unittest.main()
