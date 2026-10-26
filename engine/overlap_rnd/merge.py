"""Text-level merge of transcripts that share one timeline.

Channel-wise STT merging transcribes every recording and keeps a candidate
segment only when its characters are not already in the merged transcript
around the same time.  Duplicate detection uses the share of the candidate's
characters found, in order, in the nearby merged text (longest common
subsequence), so spelling differences between two recognitions of the same
speech do not create a second copy.

This is a union with de-duplication, not ROVER: it recovers content missing
from the primary transcript but does not vote between competing words.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from evaluation.metrics import character_tokens


DEFAULT_TOLERANCE_SECONDS = 1.0
# Two recognitions of the same clean speech on different devices disagreed by
# up to 30% CER in the MM-DEV-018 pool, which motivated 0.7; a lower value
# drops short new remarks that only share a common ending such as "습니다".
# Measured on the 20261027_main run (same data, not validated): 0.5 gave the
# lowest CER, 0.7 recovered slightly more utterances with about twice the
# insertions.  Re-select it on a human-labeled set before relying on it.
DEFAULT_DUPLICATE_THRESHOLD = 0.7


@dataclass(frozen=True)
class TimedText:
    start: float
    end: float
    text: str
    source: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"start": self.start, "end": self.end, "text": self.text, "source": self.source}


@dataclass(frozen=True)
class MergeDecision:
    segment: TimedText
    covered_ratio: float
    added: bool

    def to_dict(self) -> dict[str, object]:
        return {**self.segment.to_dict(), "covered_ratio": self.covered_ratio, "added": self.added}


def _order(segment: TimedText) -> tuple[float, float]:
    return segment.start, segment.end


def lcs_length(first: Sequence[str], second: Sequence[str]) -> int:
    """Length of the longest common subsequence of two token sequences."""

    if not first or not second:
        return 0
    previous = [0] * (len(second) + 1)
    for token in first:
        current = [0]
        for column, other in enumerate(second, start=1):
            if token == other:
                current.append(previous[column - 1] + 1)
            else:
                current.append(max(previous[column], current[column - 1]))
        previous = current
    return previous[-1]


def covered_ratio(segment: TimedText, context: Sequence[TimedText]) -> float:
    """Share of the segment's characters present, in order, in ``context``.

    A segment without scoreable characters counts as fully covered, so
    punctuation-only output is never added.
    """

    tokens = character_tokens(segment.text)
    if not tokens:
        return 1.0
    context_tokens = [
        token for item in sorted(context, key=_order) for token in character_tokens(item.text)
    ]
    return lcs_length(tokens, context_tokens) / len(tokens)


def merge_transcripts(
    primary: Sequence[TimedText],
    candidates: Sequence[Sequence[TimedText]],
    *,
    tolerance_seconds: float = DEFAULT_TOLERANCE_SECONDS,
    duplicate_threshold: float = DEFAULT_DUPLICATE_THRESHOLD,
) -> tuple[list[TimedText], list[MergeDecision]]:
    """Add every candidate segment that the merged transcript does not cover.

    ``candidates`` are merged one stream after another.  A segment is compared
    with the merged segments within ``tolerance_seconds`` of it, as they were
    before its own stream was merged, so segments of one stream never
    de-duplicate each other.
    """

    merged = sorted(primary, key=_order)
    decisions: list[MergeDecision] = []
    for stream in candidates:
        snapshot = list(merged)
        accepted: list[TimedText] = []
        for segment in sorted(stream, key=_order):
            nearby = [
                item
                for item in snapshot
                if item.start - tolerance_seconds < segment.end
                and segment.start < item.end + tolerance_seconds
            ]
            ratio = covered_ratio(segment, nearby)
            added = ratio < duplicate_threshold
            decisions.append(MergeDecision(segment, ratio, added))
            if added:
                accepted.append(segment)
        merged = sorted(merged + accepted, key=_order)
    return merged, decisions


def joined_text(segments: Sequence[TimedText]) -> str:
    return " ".join(segment.text for segment in sorted(segments, key=_order))
