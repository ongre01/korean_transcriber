from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave

import numpy as np


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import backend_bridge
import multimic
import multimic_quality
from test_multimic_align import speech_like_master


SAMPLE_RATE = 16_000
LATER_START_MS = 500


def write_source_wav(path: Path, samples: np.ndarray) -> None:
    pcm = np.rint(np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(pcm.tobytes())


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as source:
        frames = source.readframes(source.getnframes())
    return np.frombuffer(frames, dtype="<i2")


def single_strict_object(text: str) -> dict[str, object]:
    lines = text.splitlines()
    if len(lines) != 1:
        raise AssertionError(f"expected one stdout line, got {len(lines)}")

    def reject(value: str) -> object:
        raise ValueError(f"non-JSON number: {value}")

    payload = json.loads(lines[0], parse_constant=reject)
    if not isinstance(payload, dict):
        raise AssertionError("stdout is not a JSON object")
    return payload


class MultiMicQualityCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

        master = speech_like_master(seed=77, duration_seconds=16.0)
        start = 3 * SAMPLE_RATE
        length = 9 * SAMPLE_RATE
        delay = LATER_START_MS * SAMPLE_RATE // 1_000
        self.reference = master[start : start + length].copy()
        self.later = (
            0.65 * master[start + delay : start + delay + length]
            + np.random.default_rng(5).normal(0.0, 0.003, length)
        ).astype(np.float32)

    def write_inputs(self, *, reference: np.ndarray | None = None) -> list[Path]:
        paths = [self.root / "테이블 중앙.wav", self.root / "발표자 근처.wav"]
        write_source_wav(
            paths[0],
            self.reference if reference is None else reference,
        )
        write_source_wav(paths[1], self.later)
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
            "alignment": {
                "mode": "auto",
                "manual_offsets_ms": manual_offsets or {},
            },
            "selection": {
                "mode": "automatic",
                "window_seconds": 3.0,
                "hop_seconds": 1.0,
            },
        }
        if overrides is not None:
            payload["selection"]["overrides"] = overrides  # type: ignore[index]
        path = self.root / "quality manifest.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def run_cli(self, manifest_path: Path) -> tuple[int, dict[str, object], str]:
        output = StringIO()
        diagnostic = StringIO()
        exit_code = multimic_quality.main(
            ["--multi-input-manifest", str(manifest_path)],
            stdout=output,
            stderr=diagnostic,
        )
        return exit_code, single_strict_object(output.getvalue()), diagnostic.getvalue()

    def assert_partition(self, selection: dict[str, object]) -> None:
        for key in ("segments", "rows"):
            intervals = selection[key]
            self.assertTrue(intervals)
            self.assertEqual(intervals[0]["start_sample"], 0)
            self.assertEqual(
                intervals[-1]["end_sample"],
                selection["timeline_sample_count"],
            )
            for previous, current in zip(intervals, intervals[1:]):
                self.assertEqual(previous["end_sample"], current["start_sample"])

        step = int(multimic_quality.REVIEW_ROW_SECONDS * SAMPLE_RATE)
        margin = int(multimic_quality.MINIMUM_REVIEW_ROW_SECONDS * SAMPLE_RATE)
        segments = selection["segments"]
        for row in selection["rows"]:
            segment = segments[row["segment_index"]]
            self.assertLessEqual(segment["start_sample"], row["start_sample"])
            self.assertLessEqual(row["end_sample"], segment["end_sample"])
            self.assertLessEqual(row["end_sample"] - row["start_sample"], step + margin)
            for key in ("selected_input_index", "automatic_input_index", "selection_source"):
                self.assertEqual(row[key], segment[key])
            # Cuts inside a segment lie on the fixed review grid.
            if row["start_sample"] != segment["start_sample"]:
                self.assertEqual(row["start_sample"] % step, 0)
                self.assertGreaterEqual(
                    row["start_sample"] - segment["start_sample"], margin
                )
            automatic = row["automatic"]
            self.assertLessEqual(automatic["segment_start_sample"], row["start_sample"])
            self.assertLessEqual(row["end_sample"], automatic["segment_end_sample"])
            self.assertEqual(automatic["input_index"], row["automatic_input_index"])
            self.assertEqual(
                automatic["quality_score"],
                row["inputs"][row["automatic_input_index"]]["quality_score"],
            )
            self.assertEqual(
                [item["input_index"] for item in row["inputs"]],
                list(range(len(row["inputs"]))),
            )
            for item in row["inputs"]:
                self.assertGreaterEqual(item["valid_ratio"], 0.0)
                self.assertLessEqual(item["valid_ratio"], 1.0)
            for reason in automatic["reason_codes"]:
                self.assertIn(reason, selection["reason_descriptions"])

    def test_automatic_plan_covers_timeline_with_per_input_evidence(self) -> None:
        paths = self.write_inputs()

        exit_code, payload, diagnostic = self.run_cli(self.write_manifest(paths))

        self.assertEqual(exit_code, 0, diagnostic)
        self.assertIs(payload["ok"], True)
        self.assertEqual(payload["version"], 1)
        self.assertEqual(
            [item["label"] for item in payload["sources"]],
            ["테이블 중앙", "발표자 근처"],
        )
        alignment = payload["alignment"]
        self.assertEqual(alignment["reference_index"], 0)
        self.assertEqual(
            [item["source"] for item in alignment["inputs"]],
            ["reference", "automatic"],
        )
        self.assertEqual(alignment["inputs"][0]["offset_ms"], 0.0)
        self.assertLessEqual(
            abs(alignment["inputs"][1]["offset_ms"] - LATER_START_MS),
            20.0,
        )

        selection = payload["selection"]
        self.assertEqual(selection["sample_rate"], SAMPLE_RATE)
        self.assertEqual(selection["timeline_sample_count"], self.reference.size)
        self.assertEqual(selection["manual_override_count"], 0)
        self.assert_partition(selection)
        # Nine seconds always yield more than one review row, whatever the
        # automatic plan coalesced.
        self.assertGreaterEqual(len(selection["rows"]), 2)
        for row in selection["rows"]:
            self.assertEqual(row["selection_source"], "automatic")
            self.assertEqual(
                row["automatic"]["input_index"],
                row["selected_input_index"],
            )
            self.assertEqual(
                row["automatic"]["reason_codes"],
                selection["segments"][row["segment_index"]]["reason_codes"],
            )
        self.assertEqual(
            [item["input_index"] for item in payload["quality"]["inputs"]],
            [0, 1],
        )

    def test_unaligned_input_needs_a_manual_offset_which_is_then_applied(self) -> None:
        # Broadband noise on the reference defeats automatic alignment, as a
        # warning/failed MM-DEV-006 result that the user corrects manually.
        noisy_reference = self.reference.copy()
        noisy_reference[3 * SAMPLE_RATE : 5 * SAMPLE_RATE] += (
            np.random.default_rng(9).normal(0.0, 0.08, 2 * SAMPLE_RATE)
        ).astype(np.float32)
        paths = self.write_inputs(reference=noisy_reference)

        exit_code, payload, diagnostic = self.run_cli(self.write_manifest(paths))

        self.assertEqual(exit_code, 1, diagnostic)
        self.assertIs(payload["ok"], False)
        self.assertEqual(payload["kind"], "alignment")
        self.assertEqual(payload["code"], "alignment_unresolved")
        self.assertEqual(
            [item["input_index"] for item in payload["errors"]],
            [1],
        )
        self.assertTrue(payload["errors"][0]["message"])

        exit_code, payload, diagnostic = self.run_cli(
            self.write_manifest(paths, manual_offsets={"1": LATER_START_MS})
        )

        self.assertEqual(exit_code, 0, diagnostic)
        manual = payload["alignment"]["inputs"][1]
        self.assertEqual(manual["source"], "manual")
        self.assertEqual(manual["status"], "aligned")
        self.assertEqual(manual["offset_ms"], float(LATER_START_MS))
        self.assertEqual(manual["correction_method"], "manual_offset")
        self.assert_partition(payload["selection"])

    def test_override_changes_only_its_interval_and_reports_missing_audio(self) -> None:
        paths = self.write_inputs()
        override_end = 2 * SAMPLE_RATE

        exit_code, automatic, diagnostic = self.run_cli(self.write_manifest(paths))
        self.assertEqual(exit_code, 0, diagnostic)
        exit_code, payload, diagnostic = self.run_cli(
            self.write_manifest(
                paths,
                overrides=[
                    {"start_sample": 0, "end_sample": override_end, "input_index": 1}
                ],
            )
        )

        self.assertEqual(exit_code, 0, diagnostic)
        selection = payload["selection"]
        self.assert_partition(selection)
        self.assertEqual(selection["manual_override_count"], 1)
        manual_segments = [
            item
            for item in selection["segments"]
            if item["selection_source"] == "manual_override"
        ]
        self.assertEqual(len(manual_segments), 1)
        self.assertEqual(
            (manual_segments[0]["start_sample"], manual_segments[0]["end_sample"]),
            (0, override_end),
        )
        self.assertEqual(manual_segments[0]["selected_input_index"], 1)
        self.assertEqual(manual_segments[0]["reason_codes"], ["manual_override"])

        manual = [
            item
            for item in selection["rows"]
            if item["selection_source"] == "manual_override"
        ]
        self.assertEqual(len(manual), 1)
        self.assertEqual(
            (manual[0]["start_sample"], manual[0]["end_sample"]),
            (0, override_end),
        )
        self.assertEqual(manual[0]["selected_input_index"], 1)
        # The automatic evidence of the overridden interval is still visible.
        automatic_input = manual[0]["automatic_input_index"]
        self.assertEqual(manual[0]["automatic"]["input_index"], automatic_input)
        self.assertNotIn("manual_override", manual[0]["automatic"]["reason_codes"])

        # Outside the override the automatic choice and the review rows are
        # unchanged, so the next analysis still matches rows edited earlier.
        def owner(document: dict[str, object], sample: int) -> int:
            return next(
                item["selected_input_index"]
                for item in document["selection"]["rows"]
                if item["start_sample"] <= sample < item["end_sample"]
            )

        def bounds_after(document: dict[str, object], sample: int) -> list:
            return [
                (item["start_sample"], item["end_sample"])
                for item in document["selection"]["rows"]
                if item["start_sample"] >= sample
            ]

        for sample in range(override_end, self.reference.size, SAMPLE_RATE // 2):
            self.assertEqual(owner(payload, sample), owner(automatic, sample))
        self.assertEqual(
            bounds_after(payload, 5 * SAMPLE_RATE),
            bounds_after(automatic, 5 * SAMPLE_RATE),
        )

        # The later microphone has no audio before it started recording.
        self.assertLess(manual[0]["inputs"][1]["valid_ratio"], 1.0)
        warnings = [
            item
            for item in selection["warnings"]
            if item["code"] == "manual_override_unavailable"
        ]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["input_index"], 1)
        self.assertEqual(warnings[0]["start_sample"], 0)
        self.assertAlmostEqual(
            warnings[0]["end"],
            LATER_START_MS / 1_000.0,
            delta=0.02,
        )

    def test_reviewed_plan_is_what_the_merged_audio_renders(self) -> None:
        paths = self.write_inputs()
        override = {
            "start_sample": SAMPLE_RATE,
            "end_sample": 3 * SAMPLE_RATE,
            "input_index": 1,
        }
        manifest = backend_bridge.load_multi_input_manifest(
            self.write_manifest(paths, overrides=[override])
        )

        plan = multimic_quality.plan_channel_selection(manifest)
        outputs = multimic.write_multi_audio_outputs(
            self.root / "result",
            plan.prepared,
            plan.alignment,
            plan.selection,
        )

        aligned = multimic.warp_prepared_inputs(plan.prepared, plan.alignment)
        merged = read_wav(outputs.merged_audio_file)
        interval = slice(override["start_sample"], override["end_sample"])
        expected = np.where(
            aligned[1].valid_mask[interval],
            aligned[1].samples[interval],
            0.0,
        )
        np.testing.assert_array_equal(
            merged[interval],
            np.rint(np.clip(expected, -1.0, 1.0) * 32767.0).astype("<i2"),
        )
        history = json.loads(outputs.selection_file.read_text(encoding="utf-8"))
        manual = [
            item for item in history["segments"] if item["manual_override"]
        ]
        self.assertEqual(
            [
                (item["start_sample"], item["end_sample"], item["selected_input_index"])
                for item in manual
            ],
            [(override["start_sample"], override["end_sample"], 1)],
        )

    def test_override_beyond_reference_timeline_is_a_contract_error(self) -> None:
        paths = self.write_inputs()
        manifest_path = self.write_manifest(
            paths,
            overrides=[
                {
                    "start_sample": 0,
                    "end_sample": self.reference.size + 1,
                    "input_index": 1,
                }
            ],
        )

        exit_code, payload, _ = self.run_cli(manifest_path)

        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["kind"], "contract")
        self.assertEqual(payload["code"], "invalid_override")

    def test_undecodable_input_reports_per_file_preparation_error(self) -> None:
        paths = self.write_inputs()
        paths[1].write_bytes(b"not an audio stream")

        exit_code, payload, _ = self.run_cli(self.write_manifest(paths))

        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["kind"], "preparation")
        self.assertEqual(
            [item["input_index"] for item in payload["errors"]],
            [1],
        )

    def test_invalid_manifest_and_arguments_are_contract_errors(self) -> None:
        output = StringIO()
        exit_code = multimic_quality.main(
            ["--multi-input-manifest", str(self.root / "missing.json")],
            stdout=output,
            stderr=StringIO(),
        )
        payload = single_strict_object(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["code"], "invalid_manifest")

        output = StringIO()
        exit_code = multimic_quality.main([], stdout=output, stderr=StringIO())
        payload = single_strict_object(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["code"], "invalid_arguments")


if __name__ == "__main__":
    unittest.main()
