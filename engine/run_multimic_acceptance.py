"""Run the deterministic MM-DEV-016 acceptance matrix and emit a JSON log.

This is intentionally a test runner, not production audio processing.  It
selects the focused unit/integration tests that implement MM-01 through MM-09
and records the two numeric alignment targets with their fixture conditions.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys
import time
import unittest


ENGINE_DIR = Path(__file__).resolve().parent
TESTS_DIR = ENGINE_DIR / "tests"
for directory in (ENGINE_DIR, TESTS_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))


SCENARIO_TESTS: dict[str, tuple[str, ...]] = {
    "MM-01": (
        "test_multimic_align.InitialOffsetTests."
        "test_estimates_both_half_second_placement_offsets",
    ),
    "MM-02": (
        "test_multimic_align.DriftAlignmentTests."
        "test_thirty_minute_linear_drift_is_corrected_and_diagnosed",
        "test_multimic_align.DriftAlignmentTests."
        "test_nonlinear_drift_requires_manual_review_and_is_not_warped",
    ),
    "MM-03": (
        "test_multimic_quality.MultiMicChannelQualityTests."
        "test_local_broadband_noise_lowers_the_affected_channel_score",
        "test_multimic_select.ChannelSelectionTests."
        "test_real_quality_pipeline_avoids_local_broadband_noise",
        "test_multimic_select.ChannelSelectionTests."
        "test_hysteresis_rejects_small_alternating_score_changes",
    ),
    "MM-04": (
        "test_multimic_quality.MultiMicChannelQualityTests."
        "test_hard_clipping_has_an_explicit_penalty_and_lowers_score",
        "test_multimic_select.ChannelSelectionTests."
        "test_local_clipping_selects_the_clean_channel",
    ),
    "MM-05": (
        "test_multimic_quality.MultiMicSpeechAnalysisTests."
        "test_single_input_speech_survives_union",
        "test_multimic_select.ChannelSelectionTests."
        "test_real_vad_pipeline_preserves_single_input_speech",
        "test_multimic_render.MergedOutputEndToEndTests."
        "test_speech_heard_by_one_microphone_survives_rendering",
    ),
    "MM-06": (
        "test_multimic_pipeline.ValidityMaskTests."
        "test_timeline_padding_is_never_marked_as_recorded_silence",
        "test_multimic_quality.MultiMicChannelQualityTests."
        "test_prepared_quality_uses_the_reference_timeline",
        "test_multimic_select.ChannelSelectionTests."
        "test_no_window_override_tracks_partial_channel_validity",
        "test_multimic_render.MergedOutputEndToEndTests."
        "test_offset_recordings_render_reference_timeline_and_keep_sources",
    ),
    "MM-07": (
        "test_multimic_align.InitialOffsetTests."
        "test_unrelated_recording_is_a_file_level_failure",
        "test_backend_bridge_multi_pipeline.BackendBridgeMultiPipelineTests."
        "test_unrelated_third_recording_blocks_merge_and_keeps_sources",
        "test_backend_bridge_multi_pipeline.BackendBridgeMultiPipelineTests."
        "test_unresolved_alignment_is_a_user_error_with_stderr_detail",
    ),
    "MM-08": (
        "test_backend_bridge_multi_input.BackendBridgeMultiInputTests."
        "test_single_input_cli_defaults_remain_unchanged",
        "test_backend_bridge_multi_pipeline.SingleInputRegressionTests."
        "test_single_input_keeps_its_stages_and_completed_contract",
        "test_backend_bridge_protocol.BridgeTerminalEventTests."
        "test_single_input_run_keeps_existing_completed_contract",
        "test_backend_protocol.BackendProtocolFixtureTests.test_success_fixture",
    ),
    "MM-09": (
        "test_backend_bridge_cancellation.MultiInputCancellationTests."
        "test_cancellation_in_each_stage_keeps_inputs_and_earlier_results",
        "test_backend_bridge_cancellation.MultiInputCancellationTests."
        "test_failure_after_rendering_publishes_no_file_of_the_job",
        "test_backend_bridge_cancellation.SingleInputCleanupTests."
        "test_cancelled_transcription_keeps_the_input",
        "test_multimic_render.OutputFileTests."
        "test_failed_write_leaves_no_partial_or_staging_files",
    ),
}


SCENARIO_CONDITIONS: dict[str, dict[str, object]] = {
    "MM-01": {
        "fixture": "deterministic non-periodic speech-like audio",
        "sample_rate_hz": 16_000,
        "timeline_seconds": 9.0,
        "target_offsets_ms": [500.0, -500.0],
        "maximum_error_ms": 20.0,
        "random_seeds": [1234, 100, 101],
    },
    "MM-02": {
        "fixture": "memory-bounded synthetic 30-minute clock drift",
        "sample_rate_hz": 400,
        "duration_seconds": 1805.0,
        "target_offset_ms": 250.0,
        "target_drift_ppm": 200.0,
        "maximum_post_correction_error_ms": 20.0,
        "random_seed": 20261023,
    },
    "MM-03": {
        "sample_rate_hz": 1_000,
        "duration_seconds": 5.0,
        "noise_interval_seconds": [2.0, 3.0],
        "noise_standard_deviation": 0.10,
        "random_seed": 808,
    },
    "MM-04": {
        "sample_rate_hz": 1_000,
        "duration_seconds": 5.0,
        "clipping_interval_seconds": [2.0, 3.0],
        "pre_clip_gain": 5.0,
    },
    "MM-05": {
        "fixture": "speech present on one input only",
        "checks": ["VAD union", "channel selection", "rendered samples"],
    },
    "MM-06": {
        "fixture": "different recording bounds represented by validity masks",
        "checks": ["reference timeline", "non-overlap", "no padding score"],
    },
    "MM-07": {
        "fixture": "independently seeded unrelated third recording",
        "checks": ["low-confidence rejection", "bridge failure", "source hashes"],
    },
    "MM-08": {
        "fixture": "fake transcription engine and shared JSONL fixtures",
        "checks": ["single-input CLI defaults", "states", "completed contract"],
    },
    "MM-09": {
        "fixture": "interrupt/failure injection with temporary result staging",
        "cancelled_stages": [
            "decoding_audio",
            "aligning_audio",
            "detecting_speech",
            "assessing_channels",
            "selecting_channels",
            "rendering_merged_audio",
            "transcribing",
            "diarization",
            "saving_result",
        ],
    },
}


class RecordingResult(unittest.TextTestResult):
    """Text result that also keeps machine-readable per-test outcomes."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.records: dict[str, dict[str, object]] = {}
        self._started: dict[str, float] = {}

    def startTest(self, test) -> None:  # noqa: N802 - unittest API
        self._started[test.id()] = time.perf_counter()
        super().startTest(test)

    def _record(self, test, status: str, detail: str | None = None) -> None:
        test_id = test.id()
        record: dict[str, object] = {
            "id": test_id,
            "status": status,
            "duration_seconds": round(
                time.perf_counter() - self._started.get(test_id, time.perf_counter()),
                6,
            ),
        }
        if detail:
            record["detail"] = detail
        self.records[test_id] = record

    def addSuccess(self, test) -> None:  # noqa: N802 - unittest API
        super().addSuccess(test)
        self._record(test, "passed")

    def addFailure(self, test, err) -> None:  # noqa: N802 - unittest API
        super().addFailure(test, err)
        self._record(test, "failed", self._exc_info_to_string(err, test))

    def addError(self, test, err) -> None:  # noqa: N802 - unittest API
        super().addError(test, err)
        self._record(test, "error", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason) -> None:  # noqa: N802 - unittest API
        super().addSkip(test, reason)
        self._record(test, "skipped", reason)

    def addSubTest(self, test, subtest, err) -> None:  # noqa: N802 - unittest API
        super().addSubTest(test, subtest, err)
        if err is not None:
            status = "failed" if issubclass(err[0], test.failureException) else "error"
            self._record(test, status, self._exc_info_to_string(err, subtest))


def _measure_initial_offsets() -> dict[str, object]:
    from multimic.align import estimate_initial_offsets
    from test_multimic_align import SAMPLE_RATE, cropped_recordings

    report = estimate_initial_offsets(cropped_recordings())
    expected = (500.0, -500.0)
    measurements = []
    for result, target_ms in zip(report.inputs[1:], expected):
        actual_ms = float(result.offset_ms)
        measurements.append(
            {
                "input_index": result.input_index,
                "target_offset_ms": target_ms,
                "measured_offset_ms": actual_ms,
                "absolute_error_ms": abs(actual_ms - target_ms),
                "confidence": float(result.confidence),
            }
        )
    return {
        "sample_rate_hz": SAMPLE_RATE,
        "measurements": measurements,
        "target_met": all(item["absolute_error_ms"] <= 20.0 for item in measurements),
    }


def _measure_drift() -> dict[str, object]:
    from multimic.align import AlignmentConfig, estimate_time_alignments
    from test_multimic_align import long_drift_recordings

    reference, target = long_drift_recordings()
    result = estimate_time_alignments((reference, target)).inputs[1]
    residual_limit = AlignmentConfig().drift_residual_tolerance_ms
    return {
        "sample_rate_hz": reference.sample_rate,
        "duration_seconds": reference.samples.size / reference.sample_rate,
        "measured_offset_ms": float(result.offset_ms),
        "measured_drift_ppm": float(result.drift_ppm),
        "estimated_time_scale": float(result.estimated_time_scale),
        "post_correction_error_ms": float(result.post_correction_error_ms),
        "maximum_post_correction_error_ms": float(
            result.max_post_correction_error_ms
        ),
        "residual_limit_ms": float(residual_limit),
        "anchor_count": len(result.drift_anchors),
        "correction_method": result.correction_method,
        "target_met": (
            result.success
            and not result.requires_manual_review
            and result.post_correction_error_ms <= 20.0
            and result.max_post_correction_error_ms <= residual_limit
        ),
    }


MEASUREMENT_BUILDERS = {
    "MM-01": _measure_initial_offsets,
    "MM-02": _measure_drift,
}


def _load_suite(scenarios: list[str]) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for scenario in scenarios:
        for test_name in SCENARIO_TESTS[scenario]:
            loaded = loader.loadTestsFromName(test_name)
            if loaded.countTestCases() != 1:
                raise RuntimeError(
                    f"{scenario} test name did not resolve exactly once: {test_name}"
                )
            suite.addTests(loaded)
    if loader.errors:
        raise RuntimeError("\n".join(loader.errors))
    return suite


def _environment() -> dict[str, object]:
    try:
        import numpy

        numpy_version = numpy.__version__
    except ModuleNotFoundError:
        numpy_version = None
    return {
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "python_implementation": platform.python_implementation(),
        "numpy": numpy_version,
        "platform": platform.platform(),
    }


def run_acceptance(scenarios: list[str], verbosity: int) -> tuple[dict[str, object], bool]:
    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    runner = unittest.TextTestRunner(
        stream=sys.stderr,
        verbosity=verbosity,
        resultclass=RecordingResult,
    )
    result = runner.run(_load_suite(scenarios))

    measurements: dict[str, dict[str, object]] = {}
    measurement_errors: dict[str, str] = {}
    for scenario in scenarios:
        builder = MEASUREMENT_BUILDERS.get(scenario)
        if builder is None:
            continue
        try:
            measurements[scenario] = builder()
        except Exception as exc:  # Report diagnostics instead of hiding test output.
            measurement_errors[scenario] = f"{type(exc).__name__}: {exc}"

    scenario_results = []
    overall_success = result.wasSuccessful()
    for scenario in scenarios:
        test_records = []
        for test_name in SCENARIO_TESTS[scenario]:
            record = result.records.get(test_name)
            if record is None:
                record = {
                    "id": test_name,
                    "status": "error",
                    "detail": "test produced no recorded result",
                }
            test_records.append(record)
        statuses = {str(record["status"]) for record in test_records}
        measurement = measurements.get(scenario)
        measurement_ok = measurement is None or bool(measurement.get("target_met", True))
        if scenario in measurement_errors:
            measurement_ok = False
        if statuses == {"passed"} and measurement_ok:
            status = "passed"
        elif "skipped" in statuses and statuses <= {"passed", "skipped"}:
            status = "skipped"
        else:
            status = "failed"
        overall_success = overall_success and status == "passed"
        scenario_result: dict[str, object] = {
            "id": scenario,
            "status": status,
            "conditions": SCENARIO_CONDITIONS[scenario],
            "tests": test_records,
        }
        if measurement is not None:
            scenario_result["measurements"] = measurement
        if scenario in measurement_errors:
            scenario_result["measurement_error"] = measurement_errors[scenario]
        scenario_results.append(scenario_result)

    report: dict[str, object] = {
        "schema_version": 1,
        "ticket": "MM-DEV-016",
        "status": "passed" if overall_success else "failed",
        "started_at_utc": started_at.isoformat(),
        "duration_seconds": round(time.perf_counter() - started, 6),
        "environment": _environment(),
        "scenario_count": len(scenarios),
        "test_count": result.testsRun,
        "scenarios": scenario_results,
    }
    return report, overall_success


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        action="append",
        choices=tuple(SCENARIO_TESTS),
        help="Run only this scenario (repeatable). Defaults to MM-01 through MM-09.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Also write the strict UTF-8 JSON report to this path.",
    )
    parser.add_argument("--verbosity", type=int, choices=(0, 1, 2), default=2)
    parser.add_argument(
        "--list",
        action="store_true",
        help="List the scenario-to-test matrix without running it.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    if arguments.list:
        for scenario, test_names in SCENARIO_TESTS.items():
            print(scenario)
            for test_name in test_names:
                print(f"  {test_name}")
        return 0

    scenarios = list(dict.fromkeys(arguments.scenario or SCENARIO_TESTS))
    try:
        report, success = run_acceptance(scenarios, arguments.verbosity)
    except Exception as exc:
        print(f"MM-DEV-016 runner error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    serialized = json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ) + "\n"
    print(serialized, end="")
    if arguments.report is not None:
        report_path = arguments.report.expanduser().resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(serialized, encoding="utf-8")
        print(f"MM-DEV-016 report: {report_path}", file=sys.stderr)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
