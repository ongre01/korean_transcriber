from __future__ import annotations

from pathlib import Path
import sys
import unittest

import numpy as np


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from evaluation.metrics import character_tokens
from evaluation.reference import ReferenceError, overlapping_utterance_ids, parse_reference
from evaluation.scoring import (
    IDENTITY,
    TimelineMapping,
    calibrate_quality_weights,
    compare_conditions,
    diagnose,
    region_disagreement,
    score_condition,
    selection_summary,
    timeline_mapping,
    transcript_disagreement,
)
from multimic.quality import QualityScoringConfig, analyze_aligned_quality
from multimic.select import ChannelSelectionConfig, select_channels
from test_multimic_select import (
    selection_add_tone,
    selection_analysis_signal,
    selection_speech_config,
)


UTTERANCES = [
    {"id": "u1", "start": 0.0, "end": 2.0, "speaker": "A", "text": "오늘 회의를 시작하겠습니다"},
    {"id": "u2", "start": 2.5, "end": 4.0, "speaker": "B", "text": "네 좋습니다"},
    {"id": "u3", "start": 3.5, "end": 6.0, "speaker": "A", "text": "첫 번째 안건은 예산입니다", "tags": ["far_field"]},
    {"id": "u4", "start": 7.0, "end": 8.0, "speaker": "B", "text": "질문 있습니다"},
]
CONDITIONS = {
    "single_0": {"kind": "single", "input_index": 0},
    "single_1": {"kind": "single", "input_index": 1},
    "multi": {"kind": "multi", "input_index": None},
}


def reference(**fields):
    return parse_reference({"version": 1, "utterances": UTTERANCES, **fields})


def segments(*items):
    return [{"start": start, "end": end, "speaker": speaker, "text": text} for start, end, speaker, text in items]


PERFECT = segments(
    (0.0, 2.0, 1, "오늘 회의를 시작하겠습니다."),
    (2.5, 4.0, 2, "네, 좋습니다."),
    (3.5, 6.0, 1, "첫 번째 안건은 예산입니다."),
    (7.0, 8.0, 2, "질문 있습니다."),
)
MISSING_LAST = segments(
    (0.0, 2.0, 1, "오늘 회의를 시작하겠습니다."),
    (2.5, 4.0, 2, "네, 좋습니다."),
    (3.5, 6.0, 1, "첫 번째 안건은 예산 입니다."),
)
WRONG_SPEAKER = segments(
    (0.0, 2.0, 1, "오늘 회의를 시작했습니다."),
    (2.5, 4.0, 1, "네, 좋습니다."),
    (3.5, 6.0, 1, "첫번째 안건은 예선입니다."),
    (7.0, 8.0, 2, "질문 있습니다."),
)


def score(items, transcript=None, mapping=IDENTITY):
    return score_condition(transcript or reference(), items, mapping, missed_threshold=0.5)


class ReferenceTests(unittest.TestCase):
    def test_utterances_are_sorted_and_overlaps_found(self) -> None:
        transcript = parse_reference({"version": 1, "utterances": list(reversed(UTTERANCES))})

        self.assertEqual([item.id for item in transcript.utterances], ["u1", "u2", "u3", "u4"])
        self.assertEqual(transcript.speakers, ("A", "B"))
        self.assertEqual(overlapping_utterance_ids(transcript.utterances), {"u2", "u3"})

    def test_invalid_references_are_rejected(self) -> None:
        cases = {
            "duplicate id": [UTTERANCES[0], dict(UTTERANCES[1], id="u1")],
            "no letters": [dict(UTTERANCES[0], text="...")],
            "end before start": [dict(UTTERANCES[0], end=0.0)],
            "unknown field": [dict(UTTERANCES[0], confidence=1.0)],
        }
        for name, utterances in cases.items():
            with self.subTest(name), self.assertRaises(ReferenceError):
                parse_reference({"version": 1, "utterances": utterances})
        with self.assertRaises(ReferenceError):
            reference(scored_regions=[{"start": 0.0, "end": 5.0}])
        with self.assertRaises(ReferenceError):
            parse_reference({"version": 2, "utterances": UTTERANCES})


class ConditionScoreTests(unittest.TestCase):
    def test_perfect_transcript_has_no_errors(self) -> None:
        result = score(PERFECT)

        self.assertEqual(result["cer"]["errors"], 0)
        self.assertEqual(result["wer"]["errors"], 0)
        self.assertEqual(result["missed_utterances"]["count"], 0)
        self.assertEqual(result["speaker"]["mapping"], {"1": "A", "2": "B"})
        self.assertEqual(result["speaker"]["misattributed_characters"], 0)

    def test_missing_utterance_and_spacing(self) -> None:
        result = score(MISSING_LAST)

        missing = len(character_tokens(UTTERANCES[3]["text"]))
        self.assertEqual(result["missed_utterances"]["ids"], ["u4"])
        self.assertEqual(result["cer"]["deletions"], missing)
        self.assertEqual(result["cer"]["errors"], missing)
        # "예산 입니다" splits one 어절, which WER counts and CER does not.
        self.assertGreater(result["wer"]["errors"], 2)

    def test_speaker_errors_use_the_best_relabelling(self) -> None:
        result = score(WRONG_SPEAKER)

        speaker = result["speaker"]
        self.assertEqual(speaker["mapping"], {"1": "A", "2": "B"})
        self.assertEqual(speaker["misattributed_utterance_ids"], ["u2"])
        self.assertGreater(speaker["error_rate"], 0.0)
        self.assertEqual(result["cer"]["substitutions"] + result["cer"]["insertions"] + result["cer"]["deletions"], result["cer"]["errors"])

    def test_without_diarization_speaker_error_is_not_reported(self) -> None:
        result = score([dict(item, speaker=None) for item in PERFECT])

        self.assertFalse(result["speaker"]["available"])

    def test_scored_regions_use_the_condition_timeline_mapping(self) -> None:
        excerpt = parse_reference(
            {
                "version": 1,
                "scored_regions": [{"start": 0.0, "end": 2.2}, {"start": 6.8, "end": 8.5}],
                "utterances": [UTTERANCES[0], UTTERANCES[3]],
            }
        )
        # A recording that started 5 s after the reference input.
        shifted = [dict(item, start=item["start"] - 5.0, end=item["end"] - 5.0) for item in PERFECT[2:]]
        shifted_mapping = TimelineMapping(1.0, 5.0, "multi_alignment")

        self.assertEqual(score(PERFECT, excerpt)["cer"]["errors"], 0)
        self.assertEqual(score(PERFECT, excerpt)["excluded_segment_count"], 2)
        result = score(shifted, excerpt, shifted_mapping)
        self.assertEqual(result["scored_segment_count"], 1)
        self.assertEqual(result["missed_utterances"]["ids"], ["u1"])
        with self.assertRaises(ValueError):
            score(shifted, excerpt, None)

    def test_timeline_mapping_sources(self) -> None:
        alignment = {
            "sample_rate": 16_000,
            "inputs": [
                {"input_index": 0, "offset_samples": 0, "time_scale": 1.0},
                {"input_index": 1, "offset_samples": 80_000, "time_scale": 0.99997},
                {"input_index": 2, "offset_samples": None, "time_scale": None},
            ],
        }
        single = lambda index: {"kind": "single", "input_index": index}  # noqa: E731

        self.assertEqual(timeline_mapping(CONDITIONS["multi"], 0, alignment, {}), IDENTITY)
        self.assertEqual(timeline_mapping(single(0), 0, alignment, {}), IDENTITY)
        mapped = timeline_mapping(single(1), 0, alignment, {})
        self.assertEqual((mapped.scale, mapped.offset_seconds, mapped.source), (0.99997, 5.0, "multi_alignment"))
        manual = timeline_mapping(single(2), 0, alignment, {"2": -1500})
        self.assertEqual((manual.offset_seconds, manual.source), (-1.5, "manual_offset"))
        self.assertIsNone(timeline_mapping(single(2), 0, alignment, {}))


SELECTION = {
    "sample_rate": 16_000,
    "duration_seconds": 8.0,
    "switch_count": 1,
    "segments": [
        {"start_sample": 0, "end_sample": 48_000, "selected_input_index": 0, "reason": "best_quality", "reason_codes": ["best_quality"]},
        {"start_sample": 48_000, "end_sample": 128_000, "selected_input_index": 1, "reason": "better_quality", "reason_codes": ["better_quality"]},
    ],
    "warnings": [{"code": "low_selection_confidence", "start_sample": 112_000, "end_sample": 128_000}],
    "merged_audio": {"transition_count": 1, "crossfade_count": 0, "padding_sample_count": 16_000},
}


class ComparisonAndDiagnosisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reference = reference()
        self.scores = {
            "single_0": score(MISSING_LAST),
            "single_1": score(WRONG_SPEAKER),
            "multi": score(PERFECT),
        }

    def test_multi_is_compared_with_the_oracle_best_single_file(self) -> None:
        diagnostics = diagnose(self.reference, self.scores, CONDITIONS, SELECTION)
        comparison = compare_conditions(self.reference, self.scores, CONDITIONS, 0, diagnostics["utterance_tags"])

        best = comparison["single_best_oracle"]
        self.assertEqual(best, "single_1")
        self.assertEqual(set(comparison["baselines"]), {"single_best_oracle", "single_reference_input"})
        oracle = comparison["baselines"]["single_best_oracle"]
        self.assertLess(oracle["cer"]["difference"], 0.0)
        self.assertEqual(oracle["utterances"]["degraded"], 0)
        self.assertGreater(oracle["utterances"]["improved"], 0)
        self.assertIn(oracle["verdict"], {"multi_better", "no_significant_difference"})
        again = compare_conditions(self.reference, self.scores, CONDITIONS, 0, diagnostics["utterance_tags"])
        self.assertEqual(again["baselines"]["single_best_oracle"]["cer"]["bootstrap"], oracle["cer"]["bootstrap"])

    def test_degraded_utterances_are_listed(self) -> None:
        scores = dict(self.scores, multi=score(MISSING_LAST))
        diagnostics = diagnose(self.reference, scores, CONDITIONS, SELECTION)
        comparison = compare_conditions(self.reference, scores, CONDITIONS, 0, diagnostics["utterance_tags"])

        degraded = comparison["baselines"]["single_best_oracle"]["utterances"]["most_degraded"]
        self.assertEqual(degraded[0]["id"], "u4")
        self.assertGreater(degraded[0]["difference"], 0)

    def test_diagnosis_tags_and_oracle_inputs(self) -> None:
        diagnostics = diagnose(self.reference, self.scores, CONDITIONS, SELECTION)

        rows = {row["id"]: row for row in diagnostics["utterances"]}
        self.assertEqual(rows["u2"]["tags"], ["overlap", "switch_inside"])
        self.assertEqual(rows["u3"]["tags"], ["far_field", "overlap"])
        self.assertEqual(rows["u4"]["tags"], ["selection_warning"])
        self.assertEqual(rows["u3"]["selection"]["dominant_input"], 1)
        self.assertEqual(rows["u4"]["oracle_input"], 1)
        self.assertIsNone(rows["u2"]["oracle_input"])  # both single files tie
        self.assertEqual(diagnostics["by_tag"]["all"]["cer"]["multi"], 0.0)
        self.assertEqual(diagnostics["by_tag"]["overlap"]["utterances"], 2)
        self.assertEqual(diagnostics["selection_agreement"]["judged_utterances"], 3)

    def test_reference_free_summaries(self) -> None:
        summary = selection_summary(SELECTION, None, ["mic A", "mic B"])
        self.assertEqual([item["seconds"] for item in summary["input_seconds"]], [3.0, 5.0])
        self.assertEqual(summary["reason_seconds"], {"better_quality": 5.0, "best_quality": 3.0})
        self.assertEqual(summary["padding_seconds"], 1.0)

        disagreement = transcript_disagreement({"a": "가나다", "b": "가나다", "c": "가다"})
        self.assertEqual([item["edit_distance"] for item in disagreement], [0, 1, 1])

    def test_region_disagreement_maps_each_condition_onto_the_reference(self) -> None:
        first = segments((10.0, 12.0, None, "가나다라"), (70.0, 72.0, None, "마바사아"))
        # Starts 5 s later: the same speech is 5 s earlier on its own clock.
        second = segments((5.0, 7.0, None, "가나다라"), (65.0, 67.0, None, "마바자차"))
        unmapped = segments((10.0, 12.0, None, "완전히 다른 내용"))

        result = region_disagreement(
            {"single_0": first, "single_1": second, "single_2": unmapped},
            {"single_0": IDENTITY, "single_1": TimelineMapping(1.0, 5.0, "multi_alignment"), "single_2": None},
            150.0,
            SELECTION,
            region_seconds=60.0,
            suggestions=2,
        )

        self.assertEqual(result["conditions"], ["single_0", "single_1"])
        self.assertEqual([region["max_pair_rate"] for region in result["regions"]], [0.0, 0.5, 0.0])
        self.assertEqual(result["regions"][0]["selected_input_seconds"], {"0": 3.0, "1": 5.0})
        self.assertEqual(result["suggested_high_disagreement"][0], [60.0, 120.0])
        self.assertEqual(sorted(result["suggested_random"]), [[0.0, 60.0], [60.0, 120.0]])


def calibration_window(index: int, values: list[dict[str, float]], config: dict[str, float]) -> dict[str, object]:
    inputs = []
    for input_index, features in enumerate(values):
        positive = config["validity_weight"] + config["speech_weight"] + config["snr_weight"]
        base = (
            config["validity_weight"] * features["valid_ratio"]
            + config["speech_weight"] * features["speech_presence_score"]
            + config["snr_weight"] * features["snr_score"]
        ) / positive
        penalty = config["noise_penalty_weight"] * features["noise_penalty"]
        inputs.append(
            {
                "input_index": input_index,
                "quality_score": min(1.0, max(0.0, base - penalty)),
                "clipping_penalty": 0.0,
                "invalid_penalty": 0.0,
                "gap_penalty": 0.0,
                **features,
            }
        )
    return {"start_sample": index * 16_000, "end_sample": (index + 1) * 16_000, "inputs": inputs}


class CalibrationTests(unittest.TestCase):
    def test_formula_matches_the_quality_module(self) -> None:
        clean = np.zeros(5_000, dtype=np.float32)
        selection_add_tone(clean, 250, 4_750, amplitude=0.24)
        noisy = clean.copy()
        noisy[2_000:3_000] += np.random.default_rng(808).normal(0.0, 0.10, 1_000).astype(np.float32)
        quality = analyze_aligned_quality(
            (selection_analysis_signal(noisy), selection_analysis_signal(clean)),
            speech_config=selection_speech_config(),
        )
        payload = select_channels(
            quality, config=ChannelSelectionConfig(minimum_hold_seconds=0.0, boundary_search_seconds=0.0)
        ).to_dict()
        rows = [{"start": 0.0, "end": 5.0, "characters": 10, "oracle_input": 1, "oracle_margin": 3}]

        result = calibrate_quality_weights(payload, rows)

        self.assertTrue(result["available"])
        self.assertLess(result["formula_check_max_abs_error"], 1e-9)
        self.assertEqual(result["verdict"], "insufficient_data")

    def test_candidate_weights_are_reported_only_as_a_hypothesis(self) -> None:
        config = QualityScoringConfig().to_dict()
        common = {"valid_ratio": 1.0, "speech_presence_score": 1.0}
        noisy_but_loud = dict(common, snr_score=0.9, noise_penalty=0.5)
        quiet_but_clean = dict(common, snr_score=0.5, noise_penalty=0.0)
        windows = [calibration_window(index, [noisy_but_loud, quiet_but_clean], config) for index in range(40)]
        payload = {"quality_analysis": {"sample_rate": 16_000, "config": config, "windows": windows}}
        rows = [
            {"start": float(index), "end": index + 1.0, "characters": 5, "oracle_input": 1, "oracle_margin": 2}
            for index in range(40)
        ]

        result = calibrate_quality_weights(payload, rows)

        self.assertEqual(result["run_weight_agreement"], 0.0)
        self.assertEqual(result["best_candidate"]["agreement"], 1.0)
        self.assertEqual(result["verdict"], "candidate_for_next_run")
        self.assertIn("proxy", result["method"])

    def test_tampered_scores_disable_calibration(self) -> None:
        config = QualityScoringConfig().to_dict()
        window = calibration_window(0, [dict(valid_ratio=1.0, speech_presence_score=1.0, snr_score=0.5, noise_penalty=0.0)] * 2, config)
        window["inputs"][0]["quality_score"] = 0.01
        payload = {"quality_analysis": {"sample_rate": 16_000, "config": config, "windows": [window]}}

        result = calibrate_quality_weights(payload, [])

        self.assertFalse(result["available"])


if __name__ == "__main__":
    unittest.main()
