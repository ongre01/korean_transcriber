"""Reference-free check of channel-wise merging on a real MM-DEV-017 run.

The V1 multi-input transcript is the primary transcript; every single-file
transcript, mapped to the reference timeline, is merged into it.  Each added
segment is text that some device's transcript has and the V1 transcript
lacks around that time.  Without a human reference this is an upper bound on
recoverable content, not an accuracy gain: an added segment can be recovered
speech, a duplicate the merge failed to detect, or a Whisper hallucination.

The V1 channel plan separates two causes.  Where V1 already selected the
segment's own device, both transcripts come from the same audio and any
difference is Whisper decoding variance (window, context, VAD); only text from
a device V1 did not select there can be speech that channel selection lost.
Normalizing both by the time V1 selected each device gives an excess
estimate: the other-device addition rate minus the same-device (variance)
rate, times the other-device time.  It assumes decoding variance occurs at
the same rate everywhere.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from evaluation.metrics import character_tokens, normalize_text
from evaluation.runner import load_run
from evaluation.scoring import timeline_mapping

from .merge import DEFAULT_DUPLICATE_THRESHOLD, DEFAULT_TOLERANCE_SECONDS, TimedText, merge_transcripts


# Whisper's well-known Korean outputs for silence or noise.  Matching them is
# a heuristic label for the review list, not a hallucination detector.
HALLUCINATION_PHRASES = ("감사합니다", "시청해주셔서 감사합니다", "구독과 좋아요", "다음 영상에서 만나요")
# Covered-ratio bands of added segments: a high band is more likely another
# recognition of speech the primary transcript already has.
COVERED_BANDS = ((0.0, 0.1), (0.1, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.9))


def _segments(record: Mapping[str, object], mapping, source: str) -> list[TimedText]:
    return [
        TimedText(mapping.to_reference(float(item["start"])), mapping.to_reference(float(item["end"])), str(item["text"]), source)
        for item in record.get("segments", [])
    ]


def looks_like_hallucination(text: str) -> bool:
    normalized = normalize_text(text)
    return any(normalized == normalize_text(phrase) for phrase in HALLUCINATION_PHRASES)


def is_non_lexical(text: str) -> bool:
    """Only isolated Hangul jamo, such as a run of "ㅋ" for laughter.

    NFKC normalization turns compatibility jamo (U+3130 block) into
    conjoining jamo (U+1100 block), so both blocks count.
    """

    tokens = character_tokens(text)
    return bool(tokens) and all(
        "ᄀ" <= token <= "ᇿ" or "㄰" <= token <= "㆏" for token in tokens
    )


def selected_share(segment: TimedText, plan: list[tuple[float, float, int]], input_index: int) -> float:
    """Share of the segment's time for which V1 selected ``input_index``."""

    duration = segment.end - segment.start
    if duration <= 0.0:
        return 0.0
    covered = sum(
        max(0.0, min(end, segment.end) - max(start, segment.start))
        for start, end, index in plan
        if index == input_index
    )
    return covered / duration


def meeting_merge_proxy(
    run_dir: Path,
    *,
    tolerance_seconds: float = DEFAULT_TOLERANCE_SECONDS,
    duplicate_threshold: float = DEFAULT_DUPLICATE_THRESHOLD,
) -> dict[str, object]:
    run, records = load_run(run_dir)
    if "multi" not in records:
        raise ValueError(f"{run_dir} has no multi condition")
    reference_index = int(run["dataset"]["reference_index"])
    alignment_path = run_dir / "conditions" / "multi" / "output" / "alignment.json"
    alignment = json.loads(alignment_path.read_text(encoding="utf-8"))
    manual = {str(key): value for key, value in run["dataset"]["alignment"]["manual_offsets_ms"].items()}
    selection_path = run_dir / "conditions" / "multi" / "output" / "channel_selection.json"
    plan = [
        (float(item["start"]), float(item["end"]), int(item["selected_input_index"]))
        for item in json.loads(selection_path.read_text(encoding="utf-8"))["segments"]
    ]
    timeline_seconds = max(end for _, end, _ in plan)
    selected_seconds: dict[int, float] = {}
    for start, end, index in plan:
        selected_seconds[index] = selected_seconds.get(index, 0.0) + end - start

    primary = _segments(records["multi"], timeline_mapping(records["multi"], reference_index, alignment, manual), "multi")
    singles = sorted(name for name in records if name.startswith("single_"))
    candidates = [
        _segments(records[name], timeline_mapping(records[name], reference_index, alignment, manual), name)
        for name in singles
    ]
    merged, decisions = merge_transcripts(
        primary, candidates, tolerance_seconds=tolerance_seconds, duplicate_threshold=duplicate_threshold
    )

    def characters(segments) -> int:
        return sum(len(character_tokens(segment.text)) for segment in segments)

    added = [decision for decision in decisions if decision.added]
    review = []
    for decision in added:
        segment = decision.segment
        nearby = [
            item for item in primary
            if item.start - tolerance_seconds < segment.end and segment.start < item.end + tolerance_seconds
        ]
        review.append(
            {
                **decision.to_dict(),
                "characters": len(character_tokens(segment.text)),
                "primary_nearby": bool(nearby),
                "primary_text_nearby": " ".join(item.text for item in nearby),
                "hallucination_phrase": looks_like_hallucination(segment.text),
                "non_lexical": is_non_lexical(segment.text),
                "v1_selected_same_input_share": selected_share(
                    segment, plan, int(segment.source.removeprefix("single_"))
                ),
            }
        )

    def lexical(items) -> list[dict[str, object]]:
        return [item for item in items if not item["hallucination_phrase"] and not item["non_lexical"]]

    def by_selection(items, input_index: int | None = None) -> dict[str, object]:
        same = [item for item in items if item["v1_selected_same_input_share"] >= 0.5]
        other = [item for item in items if item["v1_selected_same_input_share"] < 0.5]
        result: dict[str, object] = {
            "v1_selected_same_input": {"segments": len(same), "characters": sum(item["characters"] for item in same)},
            "v1_selected_other_input": {"segments": len(other), "characters": sum(item["characters"] for item in other)},
        }
        if input_index is not None:
            same_seconds = selected_seconds.get(input_index, 0.0)
            other_seconds = timeline_seconds - same_seconds
            same_rate = result["v1_selected_same_input"]["characters"] / same_seconds if same_seconds else None
            other_rate = result["v1_selected_other_input"]["characters"] / other_seconds if other_seconds else None
            result["v1_selected_seconds"] = {"same_input": same_seconds, "other_input": other_seconds}
            result["characters_per_second"] = {"same_input": same_rate, "other_input": other_rate}
            result["excess_characters_estimate"] = (
                (other_rate - same_rate) * other_seconds if same_rate is not None and other_rate is not None else None
            )
        return result

    def bands(items) -> list[dict[str, object]]:
        return [
            {
                "covered_ratio": [low, high],
                "segments": sum(low <= item["covered_ratio"] < high for item in items),
                "characters": sum(item["characters"] for item in items if low <= item["covered_ratio"] < high),
            }
            for low, high in COVERED_BANDS
        ]

    by_source = {}
    for name in singles:
        items = [item for item in review if item["source"] == name]
        by_source[name] = {
            "added_segments": len(items),
            "added_characters": sum(item["characters"] for item in items),
            "added_near_primary_speech": sum(item["primary_nearby"] for item in items),
            "added_hallucination_phrase": sum(item["hallucination_phrase"] for item in items),
            "added_non_lexical_characters": sum(item["characters"] for item in items if item["non_lexical"]),
            "added_lexical_characters": sum(item["characters"] for item in lexical(items)),
            "lexical_covered_bands": bands(lexical(items)),
            "lexical_by_v1_selection": by_selection(lexical(items), int(name.removeprefix("single_"))),
        }
    return {
        "run_dir": str(run_dir),
        "tolerance_seconds": tolerance_seconds,
        "duplicate_threshold": duplicate_threshold,
        "primary_segments": len(primary),
        "primary_characters": characters(primary),
        "candidate_characters": {name: characters(segments) for name, segments in zip(singles, candidates)},
        "merged_characters": characters(merged),
        "added_segments": len(added),
        "added_characters": sum(item["characters"] for item in review),
        "added_hallucination_phrase": sum(item["hallucination_phrase"] for item in review),
        "added_non_lexical_characters": sum(item["characters"] for item in review if item["non_lexical"]),
        "added_lexical_segments": len(lexical(review)),
        "added_lexical_characters": sum(item["characters"] for item in lexical(review)),
        "lexical_covered_bands": bands(lexical(review)),
        "lexical_by_v1_selection": by_selection(lexical(review)),
        "by_source": by_source,
        "review": review,
    }
