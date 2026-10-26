"""Scoring of overlapped and far-field cases.

A case holds one or two reference utterances that may overlap in time.  The
hypothesis is compared with the references concatenated in every order and
the order with the fewest errors is kept (concatenated minimum-permutation
CER, the character version of CHiME-6 cpWER), so the order in which a
recognizer writes two simultaneous talkers is not counted as an error.
Per-utterance recall (matched reference characters / reference characters)
shows which talker was lost.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
from typing import Mapping, Sequence

import numpy as np

from evaluation.metrics import EMPTY_COUNTS, ErrorCounts, align_tokens, character_tokens, counts_by_owner


LOST_RECALL_THRESHOLD = 0.5
BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 20261027
PRIMARY = "primary"
SECONDARY = "secondary"


@dataclass(frozen=True)
class ReferenceUtterance:
    utterance_id: str
    role: str
    text: str


@dataclass(frozen=True)
class UtteranceScore:
    utterance_id: str
    role: str
    counts: ErrorCounts

    @property
    def recall(self) -> float | None:
        if not self.counts.reference_count:
            return None
        return self.counts.hits / self.counts.reference_count

    @property
    def lost(self) -> bool:
        recall = self.recall
        return recall is not None and recall < LOST_RECALL_THRESHOLD

    def to_dict(self) -> dict[str, object]:
        return {
            "utterance_id": self.utterance_id,
            "role": self.role,
            "reference_count": self.counts.reference_count,
            "hits": self.counts.hits,
            "recall": self.recall,
            "lost": self.lost,
        }


@dataclass(frozen=True)
class CaseScore:
    counts: ErrorCounts
    utterances: tuple[UtteranceScore, ...]
    order: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "counts": self.counts.to_dict(),
            "order": list(self.order),
            "utterances": [utterance.to_dict() for utterance in self.utterances],
        }


def score_case(references: Sequence[ReferenceUtterance], hypothesis_text: str) -> CaseScore:
    if not references:
        raise ValueError("a case needs at least one reference utterance")
    hypothesis = character_tokens(hypothesis_text)
    best: tuple[ErrorCounts, list, list[int], tuple[int, ...]] | None = None
    for order in permutations(range(len(references))):
        tokens: list[str] = []
        owners: list[int] = []
        for index in order:
            characters = character_tokens(references[index].text)
            tokens.extend(characters)
            owners.extend([index] * len(characters))
        operations = align_tokens(tokens, hypothesis)
        counts = ErrorCounts.from_operations(operations)
        if best is None or counts.errors < best[0].errors:
            best = (counts, operations, owners, order)
    assert best is not None
    counts, operations, owners, order = best
    per_owner = counts_by_owner(operations, owners, len(references))
    return CaseScore(
        counts=counts,
        utterances=tuple(
            UtteranceScore(reference.utterance_id, reference.role, per_owner[index])
            for index, reference in enumerate(references)
        ),
        order=tuple(references[index].utterance_id for index in order),
    )


def _micro_rate(errors: np.ndarray, references: np.ndarray, indices: np.ndarray) -> float:
    total = float(references[indices].sum())
    return float(errors[indices].sum()) / total if total else float("nan")


def summarize_group(
    rows: Sequence[Mapping[str, CaseScore]],
    methods: Sequence[str],
    *,
    baseline: str,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, dict[str, object]]:
    """Aggregate one group of cases for every method.

    CER is micro-averaged over the group's reference characters.  The CER
    difference to ``baseline`` carries a paired bootstrap 95% interval over
    cases, so it reflects case-to-case variation, not only character counts.
    """

    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(rows), size=(samples, len(rows))) if rows else None
    reference = np.array([row[baseline].counts.reference_count for row in rows], dtype=float)
    baseline_errors = np.array([row[baseline].counts.errors for row in rows], dtype=float)
    summary: dict[str, dict[str, object]] = {}
    for method in methods:
        scores = [row[method] for row in rows]
        errors = np.array([score.counts.errors for score in scores], dtype=float)
        utterances = [utterance for score in scores for utterance in score.utterances]
        secondary = [utterance for utterance in utterances if utterance.role == SECONDARY]
        total = sum((score.counts for score in scores), EMPTY_COUNTS)
        item: dict[str, object] = {
            "cases": len(scores),
            "reference_count": total.reference_count,
            "errors": total.errors,
            "cer": total.error_rate,
            "deletion_rate": total.deletions / total.reference_count if total.reference_count else None,
            "insertion_rate": total.insertions / total.reference_count if total.reference_count else None,
            "utterances": len(utterances),
            "lost_utterances": sum(utterance.lost for utterance in utterances),
            "secondary_utterances": len(secondary),
            "secondary_lost": sum(utterance.lost for utterance in secondary),
            "secondary_recall_mean": (
                float(np.mean([utterance.recall for utterance in secondary])) if secondary else None
            ),
        }
        if method != baseline and draws is not None:
            delta = _micro_rate(errors, reference, np.arange(len(rows))) - _micro_rate(
                baseline_errors, reference, np.arange(len(rows))
            )
            boot = np.array(
                [
                    _micro_rate(errors, reference, indices) - _micro_rate(baseline_errors, reference, indices)
                    for indices in draws
                ]
            )
            low, high = np.nanpercentile(boot, [2.5, 97.5])
            item["cer_delta_vs_baseline"] = {
                "baseline": baseline,
                "delta": delta,
                "ci95": [float(low), float(high)],
                "significant": bool(low > 0.0 or high < 0.0),
            }
        summary[method] = item
    return summary
