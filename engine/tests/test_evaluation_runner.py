from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from evaluation.dataset import DatasetError, SETTING_DEFAULTS, load_dataset, parse_dataset
from evaluation.report import score_run
from evaluation.runner import default_conditions, load_run, parse_condition, run_evaluation


# Stands in for backend_bridge.py.  A "recording" is a JSON list of the
# segments the bridge would emit for it; the multi-input run emits the
# segments stored next to the manifest's first input as merged.json.
FAKE_BRIDGE = textwrap.dedent(
    """
    import json, sys
    from pathlib import Path

    args = sys.argv[1:]
    value = lambda flag: args[args.index(flag) + 1] if flag in args else None
    output = Path(value("--output-dir"))
    encoding = "utf-8"

    def emit(**event):
        line = json.dumps(event, ensure_ascii=False) + "\\n"
        sys.stdout.buffer.write(line.encode(encoding))
        sys.stdout.buffer.flush()

    emit(type="state", value="preparing")
    emit(type="progress", value=None)
    if value("--input"):
        source = Path(value("--input"))
        payload = json.loads(source.read_text(encoding="utf-8"))
        if payload == "fail":
            emit(type="error", message="Whisper model could not be loaded")
            sys.exit(1)
        if isinstance(payload, dict):
            encoding, payload = payload["encoding"], payload["segments"]
        segments, stem, extra = payload, source.stem, {}
    else:
        manifest = json.loads(Path(value("--multi-input-manifest")).read_text(encoding="utf-8"))
        first = Path(manifest["inputs"][0]["path"])
        segments = json.loads((first.parent / "merged.json").read_text(encoding="utf-8"))
        stem = "merged_16k_mono"
        extra = {}
        for name, payload in (
            ("alignment.json", {"sample_rate": 16000, "inputs": [
                {"input_index": 0, "status": "reference", "offset_samples": 0, "offset_ms": 0.0,
                 "time_scale": 1.0, "drift_ppm": 0.0, "confidence": 1.0, "correction_method": "none"},
                {"input_index": 1, "status": "aligned", "offset_samples": 16000, "offset_ms": 1000.0,
                 "time_scale": 1.0, "drift_ppm": 0.0, "confidence": 0.9, "correction_method": "offset"}]}),
            ("channel_selection.json", {"sample_rate": 16000, "duration_seconds": 8.0, "switch_count": 1,
                "segments": [
                    {"start_sample": 0, "end_sample": 64000, "selected_input_index": 0,
                     "reason": "best_quality", "reason_codes": ["best_quality"]},
                    {"start_sample": 64000, "end_sample": 128000, "selected_input_index": 1,
                     "reason": "single_input_speech", "reason_codes": ["single_input_speech"]}],
                "warnings": [], "merged_audio": {"transition_count": 1, "crossfade_count": 0,
                                                 "padding_sample_count": 0}}),
        ):
            path = output / name
            path.write_text(json.dumps(payload), encoding="utf-8")
            extra[name.split(".")[0].replace("channel_", "") + "_file"] = str(path)
        (output / "merged_16k_mono.wav").write_bytes(b"")
        extra["merged_audio_file"] = str(output / "merged_16k_mono.wav")
    emit(type="state", value="transcribing")
    for segment in segments:
        emit(type="segment", **segment)
    (output / (stem + ".txt")).write_text("", encoding="utf-8")
    (output / (stem + ".srt")).write_text("", encoding="utf-8")
    emit(type="state", value="saving_result")
    emit(type="completed", text_file=str(output / (stem + ".txt")),
         srt_file=str(output / (stem + ".srt")), **extra)
    """
)

REFERENCE = {
    "version": 1,
    "utterances": [
        {"id": "u1", "start": 0.5, "end": 3.0, "speaker": "A", "text": "오늘 회의를 시작하겠습니다"},
        {"id": "u2", "start": 5.0, "end": 7.5, "speaker": "B", "text": "저쪽 자리에서만 들리는 질문입니다"},
    ],
}
MIC_A = [
    {"start": 0.5, "end": 3.0, "speaker": 1, "text": "오늘 회의를 시작하겠습니다."},
    {"start": 5.0, "end": 7.5, "speaker": 1, "text": "저쪽 자리에서"},
]
# Mic B started 1 s late, so its timestamps are 1 s earlier.
MIC_B = [
    {"start": -0.5 + 1.0, "end": 2.0, "speaker": 1, "text": "오늘 회의 시작합니다"},
    {"start": 4.0, "end": 6.5, "speaker": 2, "text": "저쪽 자리에서만 들리는 질문입니다."},
]
MERGED = [
    {"start": 0.5, "end": 3.0, "speaker": 1, "text": "오늘 회의를 시작하겠습니다."},
    {"start": 5.0, "end": 7.5, "speaker": 2, "text": "저쪽 자리에서만 들리는 질문입니다."},
]


class EvaluationRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="mm-dev-017-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.directory, ignore_errors=True))
        self.bridge = self.directory / "fake_bridge.py"
        self.bridge.write_text(FAKE_BRIDGE, encoding="utf-8")
        recordings = self.directory / "recordings"
        recordings.mkdir()
        for name, payload in (("mic_a.wav", MIC_A), ("mic_b.m4a", MIC_B), ("merged.json", MERGED)):
            (recordings / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        (self.directory / "reference.json").write_text(json.dumps(REFERENCE, ensure_ascii=False), encoding="utf-8")

    def dataset_payload(self, **overrides) -> dict[str, object]:
        payload = {
            "version": 1,
            "dataset_id": "fake_meeting",
            "authorization": {"approved_by": "QA lead", "basis": "synthetic test data"},
            "inputs": [
                {"path": "recordings/mic_a.wav", "label": "mic A"},
                {"path": "recordings/mic_b.m4a", "label": "mic B"},
            ],
            "reference_transcript": "reference.json",
            "settings": {"model_dir": "models/int8", "diarization": True, "device": "CPU"},
        }
        payload.update(overrides)
        return payload

    def write_dataset(self, **overrides) -> Path:
        path = self.directory / "dataset.json"
        path.write_text(json.dumps(self.dataset_payload(**overrides)), encoding="utf-8")
        return path

    def run_fake(self, dataset_path: Path, run_id: str = "run1") -> Path:
        dataset = load_dataset(dataset_path)
        return run_evaluation(
            dataset,
            self.directory / "artifacts",
            run_id=run_id,
            python=sys.executable,
            bridge_script=self.bridge,
            environment={"note": "test"},
            inputs=[
                {"input_index": index, "label": item.label, "path": str(item.path), "duration_seconds": 8.0,
                 "sample_rate": 16000, "channels": 1, "sha256": "0" * 64}
                for index, item in enumerate(dataset.inputs)
            ],
            log=lambda message: None,
        )

    def test_dataset_defaults_paths_and_validation(self) -> None:
        dataset = load_dataset(self.write_dataset())

        self.assertEqual(dataset.authorization_status, "approved")
        self.assertEqual(dataset.settings["beams"], SETTING_DEFAULTS["beams"])
        self.assertTrue(dataset.settings["skip_silence"])
        self.assertEqual(Path(dataset.settings["model_dir"]), (self.directory / "models" / "int8").resolve())
        self.assertEqual([condition.name for condition in default_conditions(dataset)], ["single_0", "single_1", "multi"])
        self.assertEqual(parse_condition("single_1", dataset).input_index, 1)
        with self.assertRaises(ValueError):
            parse_condition("single_2", dataset)

        unverified = parse_dataset(
            self.dataset_payload(authorization={"approved_by": None, "basis": "unknown"}),
            base_directory=self.directory,
        )
        self.assertEqual(unverified.authorization_status, "unverified")
        for name, overrides in {
            "bridge rejects beams": {"settings": {"model_dir": "m", "beams": 0}},
            "unknown setting": {"settings": {"model_dir": "m", "temperature": 0.2}},
            "missing permission basis": {"authorization": {"approved_by": "x"}},
            "one recording": {"inputs": [{"path": "recordings/mic_a.wav", "label": "a"}]},
            "missing recording": {"inputs": [{"path": "nope.wav", "label": "a"}, {"path": "recordings/mic_b.m4a", "label": "b"}]},
        }.items():
            with self.subTest(name), self.assertRaises(DatasetError):
                parse_dataset(self.dataset_payload(**overrides), base_directory=self.directory)

    def test_every_condition_shares_the_bridge_settings_and_is_scored(self) -> None:
        run_dir = self.run_fake(self.write_dataset())
        run, records = load_run(run_dir)

        self.assertEqual(run["condition_order"], ["single_0", "single_1", "multi"])
        shared = run["shared_bridge_arguments"]
        for record in records.values():
            self.assertEqual(record["status"], "completed")
            command = record["command"]
            self.assertEqual(command[-len(shared):], shared)
        self.assertIn("--input", records["single_0"]["command"])
        self.assertIn("--multi-input-manifest", records["multi"]["command"])
        self.assertEqual([stage["stage"] for stage in records["multi"]["stages"]], ["preparing", "transcribing", "saving_result"])

        report_path, markdown_path, all_completed = score_run(run_dir)
        report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertTrue(all_completed)
        accuracy = report["accuracy"]
        self.assertEqual(accuracy["status"], "measured")
        self.assertEqual(accuracy["scores"]["multi"]["cer"]["errors"], 0)
        # Mic B's segments are mapped with the multi-input alignment (+1 s).
        self.assertEqual(accuracy["scores"]["single_1"]["timeline_mapping"]["source"], "multi_alignment")
        self.assertEqual(accuracy["scores"]["single_0"]["missed_utterances"]["ids"], ["u2"])
        comparison = accuracy["comparison"]
        self.assertTrue(comparison["available"])
        self.assertIn(comparison["single_best_oracle"], {"single_0", "single_1"})
        self.assertLess(comparison["baselines"]["single_best_oracle"]["cer"]["difference"], 0.0)
        self.assertEqual(report["multi_input"]["switch_count"], 1)
        rows = {row["id"]: row for row in accuracy["diagnostics"]["utterances"]}
        self.assertEqual(rows["u2"]["oracle_input"], 1)
        self.assertEqual(rows["u2"]["selection"]["dominant_input"], 1)

        markdown = markdown_path.read_text(encoding="utf-8")
        for heading in ("## 1. 조건별 결과", "## 2. 다중 입력 vs 단일 파일", "## 3. 실패 구간 진단", "## 9. 해석 제한"):
            self.assertIn(heading, markdown)

    def test_without_reference_accuracy_is_reported_as_not_measured(self) -> None:
        run_dir = self.run_fake(self.write_dataset(reference_transcript=None))

        report_path, markdown_path, _ = score_run(run_dir)
        report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(report["accuracy"]["status"], "not_measured")
        markdown = markdown_path.read_text(encoding="utf-8")
        header = next(line for line in markdown.splitlines() if line.startswith("| 조건 | 입력 |"))
        self.assertNotIn("CER", header)
        self.assertIn("정확도 측정: **미측정**", markdown)
        self.assertEqual(len(report["transcript_disagreement"]), 3)

        # A reference added later is scored without running Whisper again.
        report_path, _, _ = score_run(run_dir, reference_path=self.directory / "reference.json")
        self.assertEqual(json.loads(report_path.read_text(encoding="utf-8"))["accuracy"]["status"], "measured")

    def test_failed_condition_is_reported_and_not_scored(self) -> None:
        (self.directory / "recordings" / "mic_b.m4a").write_text('"fail"', encoding="utf-8")
        run_dir = self.run_fake(self.write_dataset())

        report_path, _, all_completed = score_run(run_dir)
        report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertFalse(all_completed)
        failed = next(item for item in report["conditions"] if item["name"] == "single_1")
        self.assertEqual(failed["statistics"]["status"], "failed")
        self.assertEqual(failed["statistics"]["error_message"], "Whisper model could not be loaded")
        self.assertEqual(report["accuracy"]["not_scored"]["single_1"], "the condition did not complete")
        self.assertNotIn("single_1", report["accuracy"]["scores"])

    def test_a_stream_that_is_not_utf8_is_not_scored(self) -> None:
        (self.directory / "recordings" / "mic_b.m4a").write_text(
            json.dumps({"encoding": "cp949", "segments": MIC_B}, ensure_ascii=False), encoding="utf-8"
        )
        run_dir = self.run_fake(self.write_dataset())

        _, records = load_run(run_dir)

        record = records["single_1"]
        self.assertEqual(record["status"], "failed")
        self.assertEqual(len(record["protocol_errors"]), len(MIC_B))
        self.assertIn("not UTF-8", record["error_message"])
        stored = (run_dir / "conditions" / "single_1" / "events.jsonl").read_bytes()
        self.assertIn("질문".encode("cp949"), stored)

    def test_a_run_directory_is_never_reused(self) -> None:
        dataset_path = self.write_dataset()
        self.run_fake(dataset_path)

        with self.assertRaises(FileExistsError):
            self.run_fake(dataset_path)


if __name__ == "__main__":
    unittest.main()
