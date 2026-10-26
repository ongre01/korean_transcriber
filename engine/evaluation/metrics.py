"""Korean text normalization, edit-distance alignment and error counting.

CER is measured on Hangul/letter/digit characters with all whitespace
removed, because Korean spacing (띄어쓰기) varies between annotators and is
not a recognition error.  WER is measured on whitespace-separated 어절 and is
therefore spacing-sensitive; it is reported for completeness, CER is the
primary Korean metric.

Alignments of hour-long meetings (tens of thousands of characters on each
side) use Hirschberg's linear-space divide and conquer, with every dynamic
programming row computed by NumPy, so no quadratic table is ever held.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable, Iterable, Mapping, Sequence
import unicodedata

import numpy as np


# Sub-problems at most this large are solved with a full int32 table
# (16 MB) and a direct backtrace.
FULL_TABLE_CELLS = 4_000_000

CORRECT = "C"
SUBSTITUTION = "S"
DELETION = "D"
INSERTION = "I"

AlignmentOp = tuple[str, "int | None", "int | None"]


def normalize_text(text: str) -> str:
    """NFKC, case-folded text with everything but letters/digits as spaces."""

    folded = unicodedata.normalize("NFKC", text).casefold()
    kept = [
        character if unicodedata.category(character)[0] in "LNM" else " "
        for character in folded
    ]
    return " ".join("".join(kept).split())


def character_tokens(text: str) -> list[str]:
    return [character for character in normalize_text(text) if not character.isspace()]


def word_tokens(text: str) -> list[str]:
    return normalize_text(text).split()


def _encode(
    reference: Sequence[Hashable],
    hypothesis: Sequence[Hashable],
) -> tuple[np.ndarray, np.ndarray]:
    vocabulary: dict[Hashable, int] = {}

    def ids(tokens: Sequence[Hashable]) -> np.ndarray:
        return np.fromiter(
            (vocabulary.setdefault(token, len(vocabulary)) for token in tokens),
            dtype=np.int64,
            count=len(tokens),
        )

    return ids(reference), ids(hypothesis)


def _last_row(reference: np.ndarray, hypothesis: np.ndarray) -> np.ndarray:
    """Edit distances of all of ``reference`` to every prefix of ``hypothesis``.

    Row ``i`` follows from row ``i - 1``: substitution and deletion are
    element-wise, and the insertion chain ``D[i][j] = min_k(T[k] + j - k)`` is
    a running minimum of ``T[k] - k``.
    """

    columns = np.arange(len(hypothesis) + 1, dtype=np.int64)
    row = columns.copy()
    work = np.empty_like(columns)
    for index, token in enumerate(reference, start=1):
        work[0] = index
        np.minimum(row[:-1] + (hypothesis != token), row[1:] + 1, out=work[1:])
        row = np.minimum.accumulate(work - columns) + columns
    return row


def _table_alignment(
    reference: np.ndarray,
    hypothesis: np.ndarray,
    reference_start: int,
    hypothesis_start: int,
) -> list[AlignmentOp]:
    rows, columns = len(reference), len(hypothesis)
    offsets = np.arange(columns + 1, dtype=np.int32)
    table = np.empty((rows + 1, columns + 1), dtype=np.int32)
    table[0] = offsets
    work = np.empty(columns + 1, dtype=np.int32)
    for index in range(1, rows + 1):
        previous = table[index - 1]
        work[0] = index
        np.minimum(
            previous[:-1] + (hypothesis != reference[index - 1]),
            previous[1:] + 1,
            out=work[1:],
        )
        table[index] = np.minimum.accumulate(work - offsets) + offsets

    # Backtrace preferring the diagonal, then deletion, then insertion, so the
    # result is deterministic among equally short alignments.
    operations: list[AlignmentOp] = []
    i, j = rows, columns
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            same = reference[i - 1] == hypothesis[j - 1]
            if table[i, j] == table[i - 1, j - 1] + (0 if same else 1):
                operations.append(
                    (
                        CORRECT if same else SUBSTITUTION,
                        reference_start + i - 1,
                        hypothesis_start + j - 1,
                    )
                )
                i -= 1
                j -= 1
                continue
        if i > 0 and table[i, j] == table[i - 1, j] + 1:
            operations.append((DELETION, reference_start + i - 1, None))
            i -= 1
            continue
        operations.append((INSERTION, None, hypothesis_start + j - 1))
        j -= 1
    operations.reverse()
    return operations


def _align(
    reference: np.ndarray,
    hypothesis: np.ndarray,
    reference_start: int,
    hypothesis_start: int,
    operations: list[AlignmentOp],
) -> None:
    rows, columns = len(reference), len(hypothesis)
    if rows == 0:
        operations.extend((INSERTION, None, hypothesis_start + j) for j in range(columns))
        return
    if columns == 0:
        operations.extend((DELETION, reference_start + i, None) for i in range(rows))
        return
    if rows == 1 or (rows + 1) * (columns + 1) <= FULL_TABLE_CELLS:
        operations.extend(_table_alignment(reference, hypothesis, reference_start, hypothesis_start))
        return

    middle = rows // 2
    forward = _last_row(reference[:middle], hypothesis)
    backward = _last_row(reference[middle:][::-1], hypothesis[::-1])[::-1]
    split = int(np.argmin(forward + backward))
    _align(reference[:middle], hypothesis[:split], reference_start, hypothesis_start, operations)
    _align(
        reference[middle:],
        hypothesis[split:],
        reference_start + middle,
        hypothesis_start + split,
        operations,
    )


def align_tokens(
    reference: Sequence[Hashable],
    hypothesis: Sequence[Hashable],
) -> list[AlignmentOp]:
    """Return a minimum edit-distance alignment as ``(op, ref_index, hyp_index)``.

    ``op`` is ``C`` (correct), ``S``, ``D`` (reference token missing) or ``I``
    (extra hypothesis token); the absent side's index is ``None``.
    """

    encoded_reference, encoded_hypothesis = _encode(reference, hypothesis)
    operations: list[AlignmentOp] = []
    _align(encoded_reference, encoded_hypothesis, 0, 0, operations)
    return operations


def edit_distance(reference: Sequence[Hashable], hypothesis: Sequence[Hashable]) -> int:
    encoded_reference, encoded_hypothesis = _encode(reference, hypothesis)
    if len(encoded_reference) < len(encoded_hypothesis):
        encoded_reference, encoded_hypothesis = encoded_hypothesis, encoded_reference
    return int(_last_row(encoded_reference, encoded_hypothesis)[-1])


@dataclass(frozen=True)
class ErrorCounts:
    reference_count: int
    hits: int
    substitutions: int
    deletions: int
    insertions: int

    @property
    def errors(self) -> int:
        return self.substitutions + self.deletions + self.insertions

    @property
    def error_rate(self) -> float | None:
        return self.errors / self.reference_count if self.reference_count else None

    @classmethod
    def from_operations(cls, operations: Iterable[AlignmentOp]) -> ErrorCounts:
        totals = {CORRECT: 0, SUBSTITUTION: 0, DELETION: 0, INSERTION: 0}
        for operation, _, _ in operations:
            totals[operation] += 1
        return cls(
            reference_count=totals[CORRECT] + totals[SUBSTITUTION] + totals[DELETION],
            hits=totals[CORRECT],
            substitutions=totals[SUBSTITUTION],
            deletions=totals[DELETION],
            insertions=totals[INSERTION],
        )

    def __add__(self, other: ErrorCounts) -> ErrorCounts:
        return ErrorCounts(
            self.reference_count + other.reference_count,
            self.hits + other.hits,
            self.substitutions + other.substitutions,
            self.deletions + other.deletions,
            self.insertions + other.insertions,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "reference_count": self.reference_count,
            "hits": self.hits,
            "substitutions": self.substitutions,
            "deletions": self.deletions,
            "insertions": self.insertions,
            "errors": self.errors,
            "error_rate": self.error_rate,
        }


EMPTY_COUNTS = ErrorCounts(0, 0, 0, 0, 0)


def counts_by_owner(
    operations: Sequence[AlignmentOp],
    reference_owners: Sequence[int],
    owner_count: int,
) -> list[ErrorCounts]:
    """Split alignment errors over the reference units that own the tokens.

    An insertion is charged to the owner of the closest preceding reference
    token, or of the following one at the very start.
    """

    totals = [[0, 0, 0, 0] for _ in range(owner_count)]  # hits, S, D, I
    pending_insertions = 0
    current: int | None = None
    for operation, reference_index, _ in operations:
        if operation == INSERTION:
            if current is None:
                pending_insertions += 1
            else:
                totals[current][3] += 1
            continue
        current = reference_owners[reference_index]
        if pending_insertions:
            totals[current][3] += pending_insertions
            pending_insertions = 0
        slot = {CORRECT: 0, SUBSTITUTION: 1, DELETION: 2}[operation]
        totals[current][slot] += 1
    return [
        ErrorCounts(hits + substitutions + deletions, hits, substitutions, deletions, insertions)
        for hits, substitutions, deletions, insertions in totals
    ]


def _hungarian_minimum(cost: list[list[float]]) -> list[int]:
    """Column assigned to every row of a square cost matrix (Kuhn-Munkres)."""

    size = len(cost)
    infinity = float("inf")
    row_potential = [0.0] * (size + 1)
    column_potential = [0.0] * (size + 1)
    column_row = [0] * (size + 1)
    previous_column = [0] * (size + 1)
    for row in range(1, size + 1):
        column_row[0] = row
        column = 0
        slack = [infinity] * (size + 1)
        used = [False] * (size + 1)
        while True:
            used[column] = True
            current_row = column_row[column]
            delta = infinity
            next_column = 0
            for candidate in range(1, size + 1):
                if used[candidate]:
                    continue
                reduced = (
                    cost[current_row - 1][candidate - 1]
                    - row_potential[current_row]
                    - column_potential[candidate]
                )
                if reduced < slack[candidate]:
                    slack[candidate] = reduced
                    previous_column[candidate] = column
                if slack[candidate] < delta:
                    delta = slack[candidate]
                    next_column = candidate
            for candidate in range(size + 1):
                if used[candidate]:
                    row_potential[column_row[candidate]] += delta
                    column_potential[candidate] -= delta
                else:
                    slack[candidate] -= delta
            column = next_column
            if column_row[column] == 0:
                break
        while column:
            prior = previous_column[column]
            column_row[column] = column_row[prior]
            column = prior

    assignment = [-1] * size
    for column in range(1, size + 1):
        if column_row[column]:
            assignment[column_row[column] - 1] = column - 1
    return assignment


def best_speaker_mapping(
    cooccurrence: Mapping[tuple[Hashable, Hashable], int],
) -> dict[Hashable, Hashable]:
    """One-to-one hypothesis→reference speaker map with the most shared tokens.

    Diarization labels are arbitrary cluster numbers, so speaker errors are
    only meaningful after this optimal relabelling.  Hypothesis speakers left
    without a partner (more clusters than people) map to nothing.
    """

    reference_speakers = sorted({key[0] for key in cooccurrence}, key=str)
    hypothesis_speakers = sorted({key[1] for key in cooccurrence}, key=str)
    if not reference_speakers or not hypothesis_speakers:
        return {}
    size = max(len(reference_speakers), len(hypothesis_speakers))
    largest = max(cooccurrence.values())

    def shared(row: int, column: int) -> int:
        # Rows/columns beyond a side's speaker count are padding with no tokens.
        if row >= len(hypothesis_speakers) or column >= len(reference_speakers):
            return 0
        return cooccurrence.get((reference_speakers[column], hypothesis_speakers[row]), 0)

    cost = [[float(largest - shared(row, column)) for column in range(size)] for row in range(size)]
    mapping = {}
    for row, column in enumerate(_hungarian_minimum(cost)):
        if row < len(hypothesis_speakers) and column < len(reference_speakers):
            key = (reference_speakers[column], hypothesis_speakers[row])
            if cooccurrence.get(key, 0) > 0:
                mapping[hypothesis_speakers[row]] = reference_speakers[column]
    return mapping
