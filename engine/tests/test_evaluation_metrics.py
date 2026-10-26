from __future__ import annotations

from pathlib import Path
import random
import sys
import unittest
from unittest import mock


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from evaluation import metrics
from evaluation.metrics import (
    ErrorCounts,
    align_tokens,
    best_speaker_mapping,
    character_tokens,
    counts_by_owner,
    edit_distance,
    normalize_text,
    word_tokens,
)


def reference_distance(first: list[str], second: list[str]) -> int:
    previous = list(range(len(second) + 1))
    for row, token in enumerate(first, start=1):
        current = [row] + [0] * len(second)
        for column, other in enumerate(second, start=1):
            current[column] = min(
                previous[column] + 1,
                current[column - 1] + 1,
                previous[column - 1] + (token != other),
            )
        previous = current
    return previous[-1]


class NormalizationTests(unittest.TestCase):
    def test_punctuation_spacing_width_and_case_are_not_errors(self) -> None:
        self.assertEqual(normalize_text("  안녕하세요,  여러분!\n（ＡＢＣ）"), "안녕하세요 여러분 abc")
        self.assertEqual(character_tokens("회의 시작 - 3시"), list("회의시작3시"))
        self.assertEqual(word_tokens("회의 시작 - 3시"), ["회의", "시작", "3시"])

    def test_spacing_changes_cer_tokens_but_not_characters(self) -> None:
        self.assertEqual(character_tokens("회의를 시작합니다"), character_tokens("회의를시작 합니다"))
        self.assertNotEqual(word_tokens("회의를 시작합니다"), word_tokens("회의를시작 합니다"))


class AlignmentTests(unittest.TestCase):
    def test_known_korean_cer_and_wer(self) -> None:
        reference = "안녕하세요 반갑습니다"
        hypothesis = "안녕하세요 반갑니다"

        cer = ErrorCounts.from_operations(
            align_tokens(character_tokens(reference), character_tokens(hypothesis))
        )
        wer = ErrorCounts.from_operations(align_tokens(word_tokens(reference), word_tokens(hypothesis)))

        self.assertEqual((cer.reference_count, cer.deletions, cer.errors), (10, 1, 1))
        self.assertAlmostEqual(cer.error_rate, 0.1)
        self.assertEqual((wer.reference_count, wer.substitutions), (2, 1))
        self.assertAlmostEqual(wer.error_rate, 0.5)

    def test_empty_sides(self) -> None:
        self.assertEqual(align_tokens([], ["가"]), [("I", None, 0)])
        self.assertEqual(align_tokens(["가"], []), [("D", 0, None)])
        self.assertIsNone(ErrorCounts.from_operations([]).error_rate)

    def test_divide_and_conquer_alignment_is_minimal_and_complete(self) -> None:
        generator = random.Random(20261026)
        # A tiny table limit forces the linear-space split on every case.
        with mock.patch.object(metrics, "FULL_TABLE_CELLS", 12):
            for _ in range(300):
                first = [generator.choice("가나다라") for _ in range(generator.randint(0, 40))]
                second = [generator.choice("가나다라") for _ in range(generator.randint(0, 40))]
                operations = align_tokens(first, second)
                expected = reference_distance(first, second)

                self.assertEqual(ErrorCounts.from_operations(operations).errors, expected)
                self.assertEqual(edit_distance(first, second), expected)
                self.assertEqual(
                    [index for _, index, _ in operations if index is not None], list(range(len(first)))
                )
                self.assertEqual(
                    [index for _, _, index in operations if index is not None], list(range(len(second)))
                )
                for operation, reference_index, hypothesis_index in operations:
                    if operation == "C":
                        self.assertEqual(first[reference_index], second[hypothesis_index])
                    elif operation == "S":
                        self.assertNotEqual(first[reference_index], second[hypothesis_index])

    def test_insertions_are_charged_to_the_neighbouring_reference_unit(self) -> None:
        reference = list("가나다라")
        owners = [0, 0, 1, 1]
        operations = align_tokens(reference, list("X가나Y다라Z"))

        counts = counts_by_owner(operations, owners, 2)

        self.assertEqual([item.insertions for item in counts], [2, 1])
        self.assertEqual(sum(counts, metrics.EMPTY_COUNTS), ErrorCounts.from_operations(operations))


class SpeakerMappingTests(unittest.TestCase):
    def test_mapping_maximizes_shared_tokens_instead_of_greedy_choice(self) -> None:
        mapping = best_speaker_mapping({("A", 1): 10, ("A", 2): 9, ("B", 1): 9, ("B", 2): 0})

        self.assertEqual(mapping, {2: "A", 1: "B"})

    def test_extra_hypothesis_speakers_stay_unmapped(self) -> None:
        mapping = best_speaker_mapping({("A", 1): 30, ("A", 2): 5, ("A", 3): 4, ("B", 2): 20})

        self.assertEqual(mapping, {1: "A", 2: "B"})


if __name__ == "__main__":
    unittest.main()
