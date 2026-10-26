from __future__ import annotations

from pathlib import Path
import random
import sys
import unittest

import numpy as np


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from overlap_rnd import cases
from overlap_rnd.cases import Candidate, CaseSpec, Placement, PoolUtterance
from overlap_rnd.experiment import ExperimentConfig, method_names, run_case_methods, score_methods
from overlap_rnd.meeting import is_non_lexical, looks_like_hallucination
from overlap_rnd.merge import TimedText, covered_ratio, joined_text, lcs_length, merge_transcripts
from overlap_rnd.scoring import PRIMARY, SECONDARY, ReferenceUtterance, score_case, summarize_group
from overlap_rnd.signals import (
    SAMPLE_RATE,
    active_rms,
    db_to_gain,
    delay_and_sum,
    gcc_phat_delay,
    shift,
    v1_render,
)


def pool_utterance(
    identifier: str,
    core_seconds: float,
    embedding: tuple[float, ...],
    *,
    margin: float = 0.5,
    text: str = "가나다",
) -> PoolUtterance:
    candidate = Candidate(identifier, 10.0, 10.0 + core_seconds + 2 * margin, 10.0 + margin, 10.0 + margin + core_seconds)
    return PoolUtterance(candidate, text, 0, (text, text), 0.0, embedding)


class MergeTests(unittest.TestCase):
    def test_lcs_and_covered_ratio(self) -> None:
        self.assertEqual(lcs_length(list("가나다라"), list("가X다라Y")), 3)
        self.assertEqual(lcs_length([], list("가")), 0)
        segment = TimedText(0.0, 1.0, "회의 시작")
        self.assertEqual(covered_ratio(segment, [TimedText(0.0, 2.0, "이제 회의를 시작합니다")]), 1.0)
        self.assertEqual(covered_ratio(TimedText(0.0, 1.0, "..."), []), 1.0)

    def test_duplicates_are_dropped_and_new_speech_is_added(self) -> None:
        primary = [TimedText(0.0, 2.0, "예산 안건부터 보겠습니다", "v1")]
        other = [
            TimedText(0.1, 2.1, "예산 안건부터 봅시다", "single_1"),
            TimedText(0.5, 1.5, "네 좋습니다", "single_1"),
        ]
        merged, decisions = merge_transcripts(primary, [other])
        self.assertEqual([item.text for item in merged], ["예산 안건부터 보겠습니다", "네 좋습니다"])
        self.assertEqual([decision.added for decision in decisions], [False, True])
        # Only the shared ending "습니다" (3 of 5 characters) is in the nearby text.
        self.assertAlmostEqual(decisions[1].covered_ratio, 0.6)
        merged, _ = merge_transcripts(primary, [other], duplicate_threshold=0.5)
        self.assertEqual(len(merged), 1)

    def test_far_segments_are_not_compared(self) -> None:
        primary = [TimedText(0.0, 1.0, "네 좋습니다")]
        merged, _ = merge_transcripts(primary, [[TimedText(5.0, 6.0, "네 좋습니다")]], tolerance_seconds=1.0)
        self.assertEqual(len(merged), 2)

    def test_segments_of_one_stream_do_not_deduplicate_each_other(self) -> None:
        merged, _ = merge_transcripts([], [[TimedText(0.0, 1.0, "네"), TimedText(0.5, 1.5, "네")]])
        self.assertEqual(len(merged), 2)
        self.assertEqual(joined_text(merged), "네 네")


class ScoringTests(unittest.TestCase):
    def test_order_of_simultaneous_talkers_is_not_an_error(self) -> None:
        references = [
            ReferenceUtterance("a", PRIMARY, "가나다라"),
            ReferenceUtterance("b", SECONDARY, "마바사"),
        ]
        forward = score_case(references, "가나다라 마바사")
        backward = score_case(references, "마바사 가나다라")
        self.assertEqual(forward.counts.errors, 0)
        self.assertEqual(backward.counts.errors, 0)
        self.assertEqual(backward.order, ("b", "a"))

    def test_lost_talker_has_low_recall(self) -> None:
        references = [
            ReferenceUtterance("a", PRIMARY, "가나다라"),
            ReferenceUtterance("b", SECONDARY, "마바사아"),
        ]
        score = score_case(references, "가나다라 마")
        recalls = {item.utterance_id: item.recall for item in score.utterances}
        self.assertEqual(recalls, {"a": 1.0, "b": 0.25})
        self.assertEqual([item.lost for item in score.utterances], [False, True])
        self.assertEqual(score.counts.deletions, 3)

    def test_summary_reports_paired_difference_to_baseline(self) -> None:
        references = [ReferenceUtterance("a", PRIMARY, "가나다라마바사아")]
        rows = [
            {"v1": score_case(references, "가나다라"), "better": score_case(references, "가나다라마바사아")}
            for _ in range(5)
        ]
        summary = summarize_group(rows, ["v1", "better"], baseline="v1", samples=200)
        self.assertAlmostEqual(summary["v1"]["cer"], 0.5)
        self.assertEqual(summary["better"]["cer"], 0.0)
        delta = summary["better"]["cer_delta_vs_baseline"]
        self.assertAlmostEqual(delta["delta"], -0.5)
        self.assertTrue(delta["significant"])
        self.assertNotIn("cer_delta_vs_baseline", summary["v1"])


class SignalTests(unittest.TestCase):
    def test_gcc_phat_recovers_delay_and_shift_undoes_it(self) -> None:
        rng = np.random.default_rng(0)
        reference = rng.standard_normal(8000).astype(np.float32)
        delayed = np.concatenate((np.zeros(37, np.float32), reference[:-37]))
        self.assertEqual(gcc_phat_delay(reference, delayed, 100), 37)
        self.assertEqual(gcc_phat_delay(delayed, reference, 100), -37)
        np.testing.assert_array_equal(shift(delayed, 37)[:-37], reference[:-37])

    def test_delay_and_sum_aligns_and_prefers_the_quieter_device(self) -> None:
        rng = np.random.default_rng(1)
        speech = rng.standard_normal(16000).astype(np.float32) * 0.1
        clean = speech + rng.standard_normal(16000).astype(np.float32) * 0.001
        noisy = np.concatenate((np.zeros(12, np.float32), speech[:-12])) + rng.standard_normal(16000).astype(np.float32) * 0.05
        result = delay_and_sum([clean, noisy], reference_index=0)
        self.assertEqual(result.delays, (0, 12))
        self.assertGreater(result.weights[0], result.weights[1])
        self.assertAlmostEqual(sum(result.weights), 1.0)

    def test_active_rms_ignores_silence(self) -> None:
        frame = int(0.03 * SAMPLE_RATE)
        signal = np.zeros(20 * frame, np.float32)
        signal[: 10 * frame] = 0.1
        self.assertAlmostEqual(active_rms(signal), 0.1, places=5)

    def test_v1_render_partitions_the_case_into_original_channels(self) -> None:
        rng = np.random.default_rng(2)
        length = 6 * SAMPLE_RATE
        envelope = np.zeros(length, np.float32)
        envelope[SAMPLE_RATE : 5 * SAMPLE_RATE] = 1.0
        talker = (rng.standard_normal(length) * envelope * 0.1).astype(np.float32)
        near = talker + rng.standard_normal(length).astype(np.float32) * 0.0005
        far = talker * 0.1 + rng.standard_normal(length).astype(np.float32) * 0.01
        result = v1_render([near, far])
        self.assertEqual(result.samples.size, length)
        covered = sum(end - start for start, end, _ in result.segments)
        self.assertAlmostEqual(covered, length / SAMPLE_RATE, places=6)
        self.assertEqual(sum(result.input_seconds(2)), covered)


class CaseTests(unittest.TestCase):
    def test_candidates_need_silent_margins(self) -> None:
        speech = [(1.0, 3.0), (3.2, 5.0), (6.0, 7.0), (8.0, 20.0)]
        found = cases.select_candidates(
            speech, timeline_seconds=30.0, margin_seconds=0.5, min_core_seconds=0.6, max_core_seconds=8.0
        )
        self.assertEqual([(item.core_start, item.core_end) for item in found], [(6.0, 7.0)])
        self.assertEqual((found[0].clip_start, found[0].clip_end), (5.5, 7.5))

    def test_overlap_layout_overlaps_the_shorter_core(self) -> None:
        first = pool_utterance("a", 4.0, (1.0, 0.0))
        second = pool_utterance("b", 2.0, (0.0, 1.0))
        first_offset, second_offset, duration = cases.overlap_layout(first, second, 1.0)
        first_core = (first_offset + 0.5, first_offset + 4.5)
        second_core = (second_offset + 0.5, second_offset + 2.5)
        self.assertAlmostEqual(second_core[1], first_core[1])
        self.assertAlmostEqual(duration, 5.0)
        _, half_offset, _ = cases.overlap_layout(first, second, 0.5)
        self.assertAlmostEqual(half_offset + 0.5, first_core[1] - 1.0)
        # A longer second utterance starts together with the first one.
        long_second = pool_utterance("c", 6.0, (0.0, 1.0))
        first_offset, second_offset, _ = cases.overlap_layout(first, long_second, 1.0)
        self.assertAlmostEqual(first_offset, second_offset)

    def test_far_small_layouts(self) -> None:
        long = pool_utterance("l", 4.0, (1.0, 0.0))
        short = pool_utterance("s", 1.0, (0.0, 1.0))
        _, tail_offset, _ = cases.far_small_layout(long, short, "tail")
        self.assertAlmostEqual(tail_offset + 0.5 + 1.0, 4.5 - cases.TAIL_END_GAP_SECONDS)
        _, after_offset, _ = cases.far_small_layout(long, short, "after")
        self.assertAlmostEqual(after_offset + 0.5, 4.5 + cases.AFTER_GAP_SECONDS)
        with self.assertRaises(ValueError):
            cases.far_small_layout(long, short, "middle")

    def test_synthesize_normalizes_the_near_channel_and_attenuates_the_far_one(self) -> None:
        rng = np.random.default_rng(3)
        clip = rng.standard_normal(SAMPLE_RATE).astype(np.float32) * 0.3
        case = CaseSpec(
            "c", cases.OVERLAP, "x", (Placement("a", PRIMARY, 0.0, 0, (0.0, -12.0)),), 1.0
        )
        near, far = cases.synthesize(case, {"a": [clip, clip]}, target_level_dbfs=-26.0)
        self.assertAlmostEqual(active_rms(near), db_to_gain(-26.0), places=4)
        self.assertAlmostEqual(active_rms(far) / active_rms(near), db_to_gain(-12.0), places=4)

    def test_pairs_use_different_sounding_talkers_once(self) -> None:
        pool = [
            pool_utterance("a", 3.0, (1.0, 0.0)),
            pool_utterance("b", 3.0, (1.0, 0.0)),
            pool_utterance("c", 3.0, (0.0, 1.0)),
            pool_utterance("d", 3.0, (0.0, 1.0)),
        ]
        pairs = cases.choose_pairs(pool, pool, 5, random.Random(0), max_similarity=0.5)
        self.assertEqual(len(pairs), 2)
        for first, second in pairs:
            self.assertLess(first.similarity(second), 0.5)
        self.assertEqual(len({item.utterance_id for pair in pairs for item in pair}), 4)

    def test_build_cases_puts_each_pair_under_every_condition(self) -> None:
        pool = [pool_utterance(f"o{index}", 4.0, (1.0, 0.0) if index % 2 else (0.0, 1.0)) for index in range(4)]
        pool += [pool_utterance(f"s{index}", 1.0, (1.0, 0.0)) for index in range(2)]
        specs = cases.build_cases(
            pool,
            random.Random(0),
            channel_count=2,
            reference_channel=0,
            overlap_pairs=2,
            far_small_pairs=2,
            single_per_position=1,
            max_similarity=0.5,
        )
        groups = [spec.group for spec in specs]
        self.assertEqual(sum(group.startswith("overlap/") for group in groups), 2 * len(cases.OVERLAP_CONDITIONS))
        self.assertEqual(sum(group.startswith("far_small/") for group in groups), 2 * len(cases.FAR_SMALL_CONDITIONS))
        self.assertEqual(sum(group.startswith("single/") for group in groups), len(cases.SINGLE_CONDITIONS))
        far = next(spec for spec in specs if spec.condition == "tail_12db")
        self.assertEqual(far.placements[0].gains_db, (-12.0, 0.0))
        self.assertEqual(far.placements[1].gains_db, (0.0, -12.0))
        colocated = next(spec for spec in specs if spec.condition == "colocated_full")
        self.assertEqual({placement.gains_db for placement in colocated.placements}, {(0.0, 0.0)})


class FakeTranscriber:
    """Names the louder channel half of a two-channel case by its energy."""

    def __init__(self) -> None:
        self.sources: list[str] = []

    def transcribe(self, samples: np.ndarray, source: str) -> tuple[list[TimedText], float]:
        self.sources.append(source)
        level = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
        text = "가나다" if level > 0.05 else "라마바"
        return [TimedText(0.0, 1.0, text, source)], 0.5


class FakeSeparator:
    name = "fake"

    def separate(self, mixture: np.ndarray) -> list[np.ndarray]:
        return [mixture, mixture * 0.01]


class MethodTests(unittest.TestCase):
    def test_every_method_is_run_and_charged(self) -> None:
        rng = np.random.default_rng(4)
        length = 4 * SAMPLE_RATE
        loud = (rng.standard_normal(length) * 0.2).astype(np.float32)
        quiet = (rng.standard_normal(length) * 0.01).astype(np.float32)
        transcriber = FakeTranscriber()
        outputs, costs, details = run_case_methods(
            [loud, quiet],
            reference_channel=0,
            transcriber=transcriber,
            separators=[FakeSeparator()],
            config=ExperimentConfig(),
            selection_window_seconds=3.0,
            selection_hop_seconds=1.0,
        )
        names = method_names(2, [FakeSeparator()])
        self.assertEqual(set(outputs) | {"oracle_single"}, set(names))
        self.assertEqual(joined_text(outputs["merge_channels"]).count("라마바"), 1)
        self.assertEqual(costs["merge_channels"]["asr_streams"], 3)
        self.assertEqual(costs["merge_channels"]["asr_seconds"], 1.5)
        self.assertIn("switch_count", details["v1"])
        references = [ReferenceUtterance("a", PRIMARY, "가나다"), ReferenceUtterance("b", SECONDARY, "라마바")]
        scores, oracle = score_methods(references, outputs, 2)
        self.assertEqual(scores["merge_channels"].counts.errors, 0)
        self.assertIs(scores["oracle_single"], scores[oracle])


class MeetingProxyTests(unittest.TestCase):
    def test_labels_for_the_review_list(self) -> None:
        self.assertTrue(is_non_lexical("ㅋㅋㅋㅋ?ㅋㅋ"))
        self.assertFalse(is_non_lexical("ㅋㅋ 좋네요"))
        self.assertFalse(is_non_lexical("..."))
        self.assertTrue(looks_like_hallucination("감사합니다."))
        self.assertFalse(looks_like_hallucination("정말 감사합니다 여러분"))


if __name__ == "__main__":
    unittest.main()
