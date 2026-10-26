"""Score evaluation conditions against a reference and diagnose the differences.

Accuracy needs the human reference transcript.  Everything that can be
measured without one (processing time, channel switches, how much two
conditions' transcripts differ) is computed by separate functions and is never
labelled as accuracy: two transcripts that disagree say nothing about which
of them is right.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations, product
import math
from typing import Mapping, Sequence

import numpy as np

from .metrics import (
    CORRECT,
    SUBSTITUTION,
    ErrorCounts,
    align_tokens,
    best_speaker_mapping,
    character_tokens,
    counts_by_owner,
    edit_distance,
    word_tokens,
)
from .reference import ReferenceTranscript, overlapping_utterance_ids


BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 20261026
LISTED_UTTERANCES = 20
REGION_SECONDS = 60.0
SUGGESTED_REGIONS = 5

# Calibration searches these quality weights around the V1 defaults.  The
# invalid/gap penalties describe missing audio rather than a quality trade-off
# and keep the values the run used.
WEIGHT_GRID: dict[str, tuple[float, ...]] = {
    "validity_weight": (0.0, 0.15, 0.3),
    "speech_weight": (0.15, 0.3, 0.45),
    "snr_weight": (0.3, 0.55, 0.8),
    "clipping_penalty_weight": (0.35, 0.65, 1.0),
    "noise_penalty_weight": (0.0, 0.35, 0.7),
}
WEIGHT_NAMES = (
    "validity_weight",
    "speech_weight",
    "snr_weight",
    "clipping_penalty_weight",
    "invalid_penalty_weight",
    "gap_penalty_weight",
    "noise_penalty_weight",
)
CALIBRATION_MIN_LABELED = 30
CALIBRATION_MIN_GAIN = 0.02


@dataclass(frozen=True)
class TimelineMapping:
    """``reference_seconds = scale * condition_seconds + offset_seconds``."""

    scale: float
    offset_seconds: float
    source: str

    def to_reference(self, seconds: float) -> float:
        return self.scale * seconds + self.offset_seconds

    def to_dict(self) -> dict[str, object]:
        return {"scale": self.scale, "offset_seconds": self.offset_seconds, "source": self.source}


IDENTITY = TimelineMapping(1.0, 0.0, "reference_timeline")


def timeline_mapping(
    condition: Mapping[str, object],
    reference_index: int,
    alignment_payload: Mapping[str, object] | None,
    manual_offsets_ms: Mapping[str, int],
) -> TimelineMapping | None:
    """How a condition's timestamps map onto the reference timeline.

    The merged multi-input audio and the reference recording are already on
    it.  Another recording uses the multi-input run's affine alignment
    (``reference = time_scale * input + offset``), or a manual offset from the
    dataset; without either its excerpts cannot be located.
    """

    if condition["kind"] == "multi" or condition["input_index"] == reference_index:
        return IDENTITY
    index = int(condition["input_index"])
    if alignment_payload is not None:
        rate = float(alignment_payload["sample_rate"])
        for item in alignment_payload.get("inputs", []):
            if item.get("input_index") == index and item.get("offset_samples") is not None:
                return TimelineMapping(
                    float(item.get("time_scale") or 1.0),
                    float(item["offset_samples"]) / rate,
                    "multi_alignment",
                )
    if str(index) in manual_offsets_ms:
        return TimelineMapping(1.0, manual_offsets_ms[str(index)] / 1000.0, "manual_offset")
    return None


def _segment_text(segment: Mapping[str, object]) -> str:
    return str(segment.get("text") or "")


def transcript_statistics(
    record: Mapping[str, object],
    audio_seconds: float | None,
) -> dict[str, object]:
    """Reference-free description of one condition's run and transcript."""

    segments = record.get("segments", [])
    stage_seconds: dict[str, float] = defaultdict(float)
    for stage in record.get("stages", []):
        stage_seconds[str(stage["stage"])] += float(stage["seconds"])
    wall = float(record["wall_seconds"])
    return {
        "status": record["status"],
        "exit_code": record["exit_code"],
        "error_message": record.get("error_message"),
        "wall_seconds": wall,
        "audio_seconds": audio_seconds,
        "real_time_factor": wall / audio_seconds if audio_seconds else None,
        "stage_seconds": {name: round(value, 3) for name, value in stage_seconds.items()},
        "warnings": list(record.get("warnings", [])),
        "segment_count": len(segments),
        "characters": sum(len(character_tokens(_segment_text(item))) for item in segments),
        "words": sum(len(word_tokens(_segment_text(item))) for item in segments),
        "segment_seconds": round(
            sum(max(0.0, float(item["end"]) - float(item["start"])) for item in segments), 3
        ),
        "speakers": sorted({item["speaker"] for item in segments if item.get("speaker") is not None}),
    }


def selection_summary(
    selection_payload: Mapping[str, object],
    alignment_payload: Mapping[str, object] | None,
    labels: Sequence[str],
) -> dict[str, object]:
    """Channel plan of the multi-input run: switches, shares, reasons, alignment."""

    rate = float(selection_payload["sample_rate"])
    input_seconds: Counter[int] = Counter()
    reason_seconds: Counter[str] = Counter()
    for segment in selection_payload["segments"]:
        seconds = (segment["end_sample"] - segment["start_sample"]) / rate
        input_seconds[int(segment["selected_input_index"])] += seconds
        reason_seconds[str(segment["reason"])] += seconds
    total = sum(input_seconds.values()) or 1.0
    merged = selection_payload.get("merged_audio", {})
    summary: dict[str, object] = {
        "switch_count": selection_payload["switch_count"],
        "segment_count": len(selection_payload["segments"]),
        "duration_seconds": selection_payload["duration_seconds"],
        "input_seconds": [
            {
                "input_index": index,
                "label": labels[index] if index < len(labels) else str(index),
                "seconds": round(input_seconds.get(index, 0.0), 3),
                "share": round(input_seconds.get(index, 0.0) / total, 4),
            }
            for index in range(len(labels))
        ],
        "reason_seconds": {
            reason: round(seconds, 3) for reason, seconds in reason_seconds.most_common()
        },
        "warning_counts": dict(Counter(str(item["code"]) for item in selection_payload.get("warnings", []))),
        "transition_count": merged.get("transition_count"),
        "crossfade_count": merged.get("crossfade_count"),
        "padding_seconds": (
            merged["padding_sample_count"] / rate if merged.get("padding_sample_count") is not None else None
        ),
        "quality_config": selection_payload.get("quality_analysis", {}).get("config"),
        "selection_config": selection_payload.get("config"),
    }
    if alignment_payload is not None:
        summary["alignment"] = [
            {
                "input_index": item["input_index"],
                "label": labels[item["input_index"]] if item["input_index"] < len(labels) else None,
                "status": item.get("status"),
                "offset_ms": item.get("offset_ms"),
                "drift_ppm": item.get("drift_ppm"),
                "confidence": item.get("confidence"),
                "correction_method": item.get("correction_method"),
            }
            for item in alignment_payload.get("inputs", [])
        ]
    return summary


def transcript_disagreement(transcripts: Mapping[str, str]) -> list[dict[str, object]]:
    """Character edit distance between every pair of condition transcripts.

    This is agreement, not accuracy: it shows how much the input condition
    changes the output, and where to look, but not which output is right.
    """

    tokens = {name: character_tokens(text) for name, text in transcripts.items()}
    results = []
    for first, second in combinations(sorted(tokens), 2):
        distance = edit_distance(tokens[first], tokens[second])
        results.append(
            {
                "first": first,
                "second": second,
                "edit_distance": distance,
                "first_characters": len(tokens[first]),
                "second_characters": len(tokens[second]),
                "rate_vs_first": distance / len(tokens[first]) if tokens[first] else None,
            }
        )
    return results


def region_disagreement(
    segments_by_condition: Mapping[str, Sequence[Mapping[str, object]]],
    mappings: Mapping[str, TimelineMapping | None],
    duration_seconds: float,
    selection_payload: Mapping[str, object] | None = None,
    *,
    region_seconds: float = REGION_SECONDS,
    suggestions: int = SUGGESTED_REGIONS,
) -> dict[str, object]:
    """Per-minute transcript disagreement on the reference timeline.

    Without a reference this locates where the input condition changes the
    transcript most, which is where a human reference is most informative.
    The suggested excerpts are reported in two separate groups: a seeded
    random sample (unbiased level estimate) and the highest-disagreement
    regions (diagnosis only, they overstate any difference).
    """

    names = sorted(name for name in segments_by_condition if mappings.get(name) is not None)
    count = max(1, math.ceil(duration_seconds / region_seconds))
    texts: dict[str, list[list[str]]] = {name: [[] for _ in range(count)] for name in names}
    for name in names:
        mapping = mappings[name]
        for segment in segments_by_condition[name]:
            middle = mapping.to_reference((float(segment["start"]) + float(segment["end"])) / 2.0)
            position = int(middle // region_seconds)
            if 0 <= position < count:
                texts[name][position].append(_segment_text(segment))

    rate = float(selection_payload["sample_rate"]) if selection_payload else None
    regions = []
    for position in range(count):
        start = position * region_seconds
        end = min(duration_seconds, start + region_seconds)
        tokens = {name: character_tokens(" ".join(texts[name][position])) for name in names}
        pairs = []
        for first, second in combinations(names, 2):
            longest = max(len(tokens[first]), len(tokens[second]))
            distance = edit_distance(tokens[first], tokens[second])
            pairs.append(
                {"first": first, "second": second, "edit_distance": distance, "rate": _ratio(distance, longest)}
            )
        selected: Counter[int] = Counter()
        if selection_payload is not None:
            for item in selection_payload["segments"]:
                seconds = _overlap(start, end, item["start_sample"] / rate, item["end_sample"] / rate)
                if seconds > 0.0:
                    selected[int(item["selected_input_index"])] += seconds
        regions.append(
            {
                "start": start,
                "end": end,
                "characters": {name: len(tokens[name]) for name in names},
                "max_pair_rate": max((item["rate"] or 0.0 for item in pairs), default=0.0),
                "pairs": pairs,
                "selected_input_seconds": {str(index): round(value, 3) for index, value in sorted(selected.items())},
            }
        )

    with_speech = [region for region in regions if any(region["characters"].values())]
    highest = sorted(with_speech, key=lambda region: (-region["max_pair_rate"], region["start"]))[:suggestions]
    generator = np.random.default_rng(BOOTSTRAP_SEED)
    sample = sorted(
        generator.choice(len(with_speech), size=min(suggestions, len(with_speech)), replace=False).tolist()
    ) if with_speech else []
    return {
        "region_seconds": region_seconds,
        "conditions": names,
        "regions": regions,
        "suggested_random": [[with_speech[index]["start"], with_speech[index]["end"]] for index in sample],
        "suggested_random_seed": BOOTSTRAP_SEED,
        "suggested_high_disagreement": [[region["start"], region["end"]] for region in highest],
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _speaker_errors(
    reference: ReferenceTranscript,
    operations: Sequence[tuple[str, int | None, int | None]],
    reference_owners: Sequence[int],
    hypothesis_speakers: Sequence[object],
    missed_ids: set[str],
) -> dict[str, object]:
    if not reference.speakers:
        return {"available": False, "reason": "the reference transcript has no speaker labels"}
    if all(speaker is None for speaker in hypothesis_speakers):
        return {"available": False, "reason": "the transcript has no speaker labels (diarization was off or found nobody)"}

    pairs = []
    for operation, reference_index, hypothesis_index in operations:
        if operation not in (CORRECT, SUBSTITUTION):
            continue
        owner = reference_owners[reference_index]
        speaker = reference.utterances[owner].speaker
        if speaker is not None:
            pairs.append((owner, speaker, hypothesis_speakers[hypothesis_index]))
    cooccurrence = Counter((speaker, hypothesis) for _, speaker, hypothesis in pairs if hypothesis is not None)
    mapping = best_speaker_mapping(cooccurrence)
    wrong = sum(1 for _, speaker, hypothesis in pairs if mapping.get(hypothesis) != speaker)

    votes: dict[int, Counter] = defaultdict(Counter)
    for owner, _, hypothesis in pairs:
        votes[owner][mapping.get(hypothesis)] += 1
    judged: dict[str, bool] = {}
    for owner, utterance in enumerate(reference.utterances):
        if utterance.speaker is None or utterance.id in missed_ids or not votes[owner]:
            continue
        judged[utterance.id] = votes[owner].most_common(1)[0][0] == utterance.speaker
    misattributed = [identifier for identifier, correct in judged.items() if not correct]
    return {
        "available": True,
        "reference_speaker_count": len(reference.speakers),
        "hypothesis_speaker_count": len({speaker for speaker in hypothesis_speakers if speaker is not None}),
        "mapping": {str(hypothesis): speaker for hypothesis, speaker in sorted(mapping.items(), key=lambda item: str(item[0]))},
        "aligned_characters": len(pairs),
        "misattributed_characters": wrong,
        "error_rate": _ratio(wrong, len(pairs)),
        "judged_utterance_count": len(judged),
        "misattributed_utterance_count": len(misattributed),
        "misattributed_utterance_ids": misattributed,
        "utterance_speaker_correct": judged,
    }


def score_condition(
    reference: ReferenceTranscript,
    segments: Sequence[Mapping[str, object]],
    mapping: TimelineMapping | None,
    *,
    missed_threshold: float,
) -> dict[str, object]:
    """CER, WER, missed utterances and speaker errors of one condition.

    Segments are kept when their midpoint, on the reference timeline, lies in
    a scored region.  Hypothesis and reference are each concatenated in time
    order and aligned globally, so timestamp errors do not count as
    recognition errors.
    """

    if reference.scored_regions is not None and mapping is None:
        raise ValueError("the condition's timeline cannot be mapped onto the reference timeline")
    scored = [
        segment
        for segment in segments
        if reference.scored_regions is None
        or reference.contains(mapping.to_reference((float(segment["start"]) + float(segment["end"])) / 2.0))
    ]

    utterances = reference.utterances
    reference_characters: list[str] = []
    character_owners: list[int] = []
    reference_words: list[str] = []
    word_owners: list[int] = []
    for owner, utterance in enumerate(utterances):
        characters = character_tokens(utterance.text)
        reference_characters.extend(characters)
        character_owners.extend([owner] * len(characters))
        words = word_tokens(utterance.text)
        reference_words.extend(words)
        word_owners.extend([owner] * len(words))

    hypothesis_characters: list[str] = []
    hypothesis_speakers: list[object] = []
    hypothesis_words: list[str] = []
    for segment in scored:
        characters = character_tokens(_segment_text(segment))
        hypothesis_characters.extend(characters)
        hypothesis_speakers.extend([segment.get("speaker")] * len(characters))
        hypothesis_words.extend(word_tokens(_segment_text(segment)))

    character_operations = align_tokens(reference_characters, hypothesis_characters)
    word_operations = align_tokens(reference_words, hypothesis_words)
    character_counts = counts_by_owner(character_operations, character_owners, len(utterances))
    word_counts = counts_by_owner(word_operations, word_owners, len(utterances))

    missed = [
        utterance.id
        for utterance, counts in zip(utterances, character_counts)
        if counts.hits < missed_threshold * counts.reference_count
    ]
    speaker = _speaker_errors(
        reference,
        character_operations,
        character_owners,
        hypothesis_speakers,
        set(missed),
    )
    speaker_correct = speaker.pop("utterance_speaker_correct", {})
    missed_set = set(missed)
    return {
        "scored_segment_count": len(scored),
        "excluded_segment_count": len(segments) - len(scored),
        "timeline_mapping": None if mapping is None else mapping.to_dict(),
        "cer": ErrorCounts.from_operations(character_operations).to_dict(),
        "wer": ErrorCounts.from_operations(word_operations).to_dict(),
        "missed_utterances": {
            "threshold": missed_threshold,
            "count": len(missed),
            "rate": _ratio(len(missed), len(utterances)),
            "ids": missed,
        },
        "speaker": speaker,
        "utterances": [
            {
                "id": utterance.id,
                "characters": characters.reference_count,
                "character_errors": characters.errors,
                "hits": characters.hits,
                "substitutions": characters.substitutions,
                "deletions": characters.deletions,
                "insertions": characters.insertions,
                "words": words.reference_count,
                "word_errors": words.errors,
                "missed": utterance.id in missed_set,
                "speaker_correct": speaker_correct.get(utterance.id),
            }
            for utterance, characters, words in zip(utterances, character_counts, word_counts)
        ],
    }


def _rate(score: Mapping[str, object], metric: str) -> float | None:
    return score[metric]["error_rate"]


def _bootstrap_delta(
    candidate_errors: np.ndarray,
    baseline_errors: np.ndarray,
    characters: np.ndarray,
) -> dict[str, object]:
    """95% interval of the CER difference by resampling utterances."""

    generator = np.random.default_rng(BOOTSTRAP_SEED)
    count = len(characters)
    picks = generator.integers(0, count, size=(BOOTSTRAP_SAMPLES, count))
    totals = characters[picks].sum(axis=1)
    deltas = (candidate_errors[picks].sum(axis=1) - baseline_errors[picks].sum(axis=1)) / totals
    low, high = np.percentile(deltas, [2.5, 97.5])
    return {
        "method": "utterance bootstrap",
        "samples": BOOTSTRAP_SAMPLES,
        "seed": BOOTSTRAP_SEED,
        "ci95_low": float(low),
        "ci95_high": float(high),
        "excludes_zero": bool(low > 0.0 or high < 0.0),
    }


def _metric_delta(candidate: float | None, baseline: float | None) -> dict[str, object]:
    if candidate is None or baseline is None:
        return {"candidate": candidate, "baseline": baseline, "difference": None, "relative": None}
    return {
        "candidate": candidate,
        "baseline": baseline,
        "difference": candidate - baseline,
        "relative": (candidate - baseline) / baseline if baseline else None,
    }


def _utterance_brief(
    utterance,
    candidate: Mapping[str, object],
    baseline: Mapping[str, object],
    tags: Sequence[str],
) -> dict[str, object]:
    return {
        "id": utterance.id,
        "start": utterance.start,
        "end": utterance.end,
        "speaker": utterance.speaker,
        "characters": candidate["characters"],
        "candidate_errors": candidate["character_errors"],
        "baseline_errors": baseline["character_errors"],
        "difference": candidate["character_errors"] - baseline["character_errors"],
        "tags": list(tags),
    }


def compare_conditions(
    reference: ReferenceTranscript,
    scores: Mapping[str, Mapping[str, object]],
    conditions: Mapping[str, Mapping[str, object]],
    reference_index: int,
    utterance_tags: Mapping[str, Sequence[str]],
) -> dict[str, object]:
    """Multi-input against the best single file and the reference recording.

    "Best single file" is chosen by CER after scoring (an oracle choice the
    user cannot make in advance), so it is the hardest baseline for the
    multi-input condition.
    """

    singles = {
        name: score
        for name, score in scores.items()
        if conditions[name]["kind"] == "single" and _rate(score, "cer") is not None
    }
    multi = scores.get("multi")
    if multi is None or not singles:
        return {"available": False, "reason": "needs a scored multi-input run and at least one scored single-file run"}

    best = min(singles, key=lambda name: (_rate(singles[name], "cer"), name))
    baselines = {"single_best_oracle": best}
    reference_name = f"single_{reference_index}"
    if reference_name in singles:
        baselines["single_reference_input"] = reference_name

    characters = np.array([item["characters"] for item in multi["utterances"]], dtype=np.int64)
    multi_errors = np.array([item["character_errors"] for item in multi["utterances"]], dtype=np.int64)
    comparisons = {}
    for role, name in baselines.items():
        baseline = singles[name]
        baseline_errors = np.array([item["character_errors"] for item in baseline["utterances"]], dtype=np.int64)
        speaker_rate = baseline["speaker"].get("error_rate") if baseline["speaker"].get("available") else None
        multi_speaker_rate = multi["speaker"].get("error_rate") if multi["speaker"].get("available") else None
        cer = _metric_delta(_rate(multi, "cer"), _rate(baseline, "cer"))
        cer["bootstrap"] = _bootstrap_delta(multi_errors, baseline_errors, characters)
        difference = cer["difference"]
        if not cer["bootstrap"]["excludes_zero"]:
            verdict = "no_significant_difference"
        else:
            verdict = "multi_better" if difference < 0 else "multi_worse"

        rows = list(zip(reference.utterances, multi["utterances"], baseline["utterances"]))
        changes = [
            _utterance_brief(utterance, candidate, base, utterance_tags.get(utterance.id, ()))
            for utterance, candidate, base in rows
        ]
        degraded = sorted((item for item in changes if item["difference"] > 0), key=lambda item: -item["difference"])
        improved = sorted((item for item in changes if item["difference"] < 0), key=lambda item: item["difference"])
        comparisons[role] = {
            "baseline": name,
            "cer": cer,
            "wer": _metric_delta(_rate(multi, "wer"), _rate(baseline, "wer")),
            "missed_utterances": _metric_delta(
                multi["missed_utterances"]["count"], baseline["missed_utterances"]["count"]
            ),
            "speaker_error_rate": _metric_delta(multi_speaker_rate, speaker_rate),
            "misattributed_utterances": _metric_delta(
                multi["speaker"].get("misattributed_utterance_count"),
                baseline["speaker"].get("misattributed_utterance_count"),
            ),
            "verdict": verdict,
            "utterances": {
                "improved": len(improved),
                "degraded": len(degraded),
                "unchanged": len(changes) - len(improved) - len(degraded),
                "most_degraded": degraded[:LISTED_UTTERANCES],
                "most_improved": improved[:LISTED_UTTERANCES],
            },
        }
    return {"available": True, "single_best_oracle": best, "baselines": comparisons}


def _overlap(start: float, end: float, other_start: float, other_end: float) -> float:
    return max(0.0, min(end, other_end) - max(start, other_start))


def utterance_selection(
    reference: ReferenceTranscript,
    selection_payload: Mapping[str, object] | None,
) -> dict[str, dict[str, object]]:
    """Which inputs the multi-input plan used during every utterance."""

    if selection_payload is None:
        return {}
    rate = float(selection_payload["sample_rate"])
    segments = [
        (item["start_sample"] / rate, item["end_sample"] / rate, int(item["selected_input_index"]), item["reason_codes"])
        for item in selection_payload["segments"]
    ]
    warnings = [
        (item["start_sample"] / rate, item["end_sample"] / rate, str(item["code"]))
        for item in selection_payload.get("warnings", [])
        if item.get("start_sample") is not None
    ]
    result = {}
    for utterance in reference.utterances:
        seconds: Counter[int] = Counter()
        reasons: set[str] = set()
        inside = [item for item in segments if _overlap(utterance.start, utterance.end, item[0], item[1]) > 0.0]
        for start, end, index, codes in inside:
            seconds[index] += _overlap(utterance.start, utterance.end, start, end)
            reasons.update(codes)
        switches = sum(
            previous[2] != current[2] and utterance.start < current[0] < utterance.end
            for previous, current in zip(inside, inside[1:])
        )
        dominant = seconds.most_common(1)[0][0] if seconds else None
        result[utterance.id] = {
            "dominant_input": dominant,
            "input_seconds": {str(index): round(value, 3) for index, value in sorted(seconds.items())},
            "switches_inside": switches,
            "reasons": sorted(reasons),
            "warnings": sorted(
                {code for start, end, code in warnings if _overlap(utterance.start, utterance.end, start, end) > 0.0}
            ),
        }
    return result


def diagnose(
    reference: ReferenceTranscript,
    scores: Mapping[str, Mapping[str, object]],
    conditions: Mapping[str, Mapping[str, object]],
    selection_payload: Mapping[str, object] | None,
    *,
    oracle_margin: int = 1,
) -> dict[str, object]:
    """Per-utterance evidence and per-tag error rates for failure analysis.

    Tags are the annotators' tags plus ``overlap`` (another speaker talks at
    the same time), ``switch_inside`` (the channel changed mid-utterance) and
    ``selection_warning``.  The utterance-level oracle input is the recording
    whose single-file transcript had the fewest errors there, when it beats
    every other recording by ``oracle_margin`` characters.
    """

    singles = {
        int(conditions[name]["input_index"]): score
        for name, score in scores.items()
        if conditions[name]["kind"] == "single"
    }
    selections = utterance_selection(reference, selection_payload)
    overlaps = overlapping_utterance_ids(reference.utterances)

    rows = []
    tags_by_id: dict[str, list[str]] = {}
    for position, utterance in enumerate(reference.utterances):
        tags = list(utterance.tags)
        if utterance.id in overlaps:
            tags.append("overlap")
        selection = selections.get(utterance.id)
        if selection is not None:
            if selection["switches_inside"]:
                tags.append("switch_inside")
            if selection["warnings"]:
                tags.append("selection_warning")
        tags = list(dict.fromkeys(tags))
        tags_by_id[utterance.id] = tags

        errors_by_input = {index: score["utterances"][position]["character_errors"] for index, score in singles.items()}
        oracle_input = None
        margin = None
        if errors_by_input:
            ranked = sorted(errors_by_input.items(), key=lambda item: (item[1], item[0]))
            margin = ranked[1][1] - ranked[0][1] if len(ranked) > 1 else None
            if margin is None or margin >= oracle_margin:
                oracle_input = ranked[0][0]
        rows.append(
            {
                "id": utterance.id,
                "start": utterance.start,
                "end": utterance.end,
                "speaker": utterance.speaker,
                "characters": len(character_tokens(utterance.text)),
                "tags": tags,
                "character_errors": {
                    name: score["utterances"][position]["character_errors"] for name, score in scores.items()
                },
                "missed": {name: score["utterances"][position]["missed"] for name, score in scores.items()},
                "speaker_correct": {
                    name: score["utterances"][position]["speaker_correct"] for name, score in scores.items()
                },
                "oracle_input": oracle_input,
                "oracle_margin": margin,
                "selection": selection,
            }
        )

    tag_names = sorted({tag for tags in tags_by_id.values() for tag in tags})
    by_tag = {}
    for tag in ["all", *tag_names]:
        members = [row for row in rows if tag == "all" or tag in row["tags"]]
        characters = sum(row["characters"] for row in members)
        by_tag[tag] = {
            "utterances": len(members),
            "characters": characters,
            "cer": {
                name: _ratio(sum(row["character_errors"][name] for row in members), characters) for name in scores
            },
            "missed": {name: sum(row["missed"][name] for row in members) for name in scores},
        }

    judged = [
        row
        for row in rows
        if row["oracle_input"] is not None
        and row["selection"] is not None
        and row["selection"]["dominant_input"] is not None
        and len(singles) > 1
    ]
    agreeing = [row for row in judged if row["selection"]["dominant_input"] == row["oracle_input"]]
    total_characters = sum(row["characters"] for row in rows)
    oracle_errors = sum(
        min(score["utterances"][position]["character_errors"] for score in singles.values())
        for position in range(len(rows))
    ) if singles else None
    return {
        "oracle_margin_characters": oracle_margin,
        "utterance_tags": tags_by_id,
        "by_tag": by_tag,
        "selection_agreement": {
            "judged_utterances": len(judged),
            "agreeing_utterances": len(agreeing),
            "rate": _ratio(len(agreeing), len(judged)),
            "character_weighted_rate": _ratio(
                sum(row["characters"] for row in agreeing), sum(row["characters"] for row in judged)
            ),
        },
        "utterance_oracle_cer": _ratio(oracle_errors, total_characters) if oracle_errors is not None else None,
        "utterances": rows,
    }


def _window_scores(features: Mapping[str, np.ndarray], weights: Mapping[str, float]) -> np.ndarray:
    """``quality.py``'s window score for every window and input at once."""

    positive = weights["validity_weight"] + weights["speech_weight"] + weights["snr_weight"]
    base = (
        weights["validity_weight"] * features["valid_ratio"]
        + weights["speech_weight"] * features["speech_presence_score"]
        + weights["snr_weight"] * features["snr_score"]
    ) / positive
    penalty = (
        weights["clipping_penalty_weight"] * features["clipping_penalty"]
        + weights["invalid_penalty_weight"] * features["invalid_penalty"]
        + weights["gap_penalty_weight"] * features["gap_penalty"]
        + weights["noise_penalty_weight"] * features["noise_penalty"]
    )
    return np.clip(base - penalty, 0.0, 1.0)


def calibrate_quality_weights(
    selection_payload: Mapping[str, object] | None,
    diagnostic_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Search quality weights that pick the utterance-level oracle input.

    The proxy re-scores the stored per-window features with each candidate
    weight set and predicts, per labelled utterance, the input with the
    highest overlap-weighted mean window score.  It ignores hysteresis and
    boundary snapping, and it is fitted and measured on the same utterances,
    so a better candidate is a hypothesis for the next evaluation run, never a
    measured improvement.
    """

    if selection_payload is None:
        return {"available": False, "reason": "no multi-input channel selection file"}
    quality = selection_payload.get("quality_analysis") or {}
    windows = quality.get("windows") or []
    config = quality.get("config") or {}
    if not windows or any(name not in config for name in WEIGHT_NAMES):
        return {"available": False, "reason": "the selection file has no per-window quality features"}
    # Without a second single-file transcript there is no oracle to agree with.
    labeled = [
        row
        for row in diagnostic_rows
        if row.get("oracle_input") is not None and row.get("oracle_margin") is not None
    ]
    input_count = len(windows[0]["inputs"])
    if input_count < 2:
        return {"available": False, "reason": "needs at least two inputs"}

    rate = float(quality["sample_rate"])
    names = (
        "valid_ratio",
        "speech_presence_score",
        "snr_score",
        "clipping_penalty",
        "invalid_penalty",
        "gap_penalty",
        "noise_penalty",
    )
    features = {name: np.zeros((len(windows), input_count)) for name in names}
    scoreable = np.zeros((len(windows), input_count))
    stored = np.zeros((len(windows), input_count))
    starts = np.array([window["start_sample"] / rate for window in windows])
    ends = np.array([window["end_sample"] / rate for window in windows])
    for row, window in enumerate(windows):
        for item in window["inputs"]:
            column = int(item["input_index"])
            for name in names:
                features[name][row, column] = float(item.get(name) or 0.0)
            if item.get("quality_score") is not None:
                scoreable[row, column] = 1.0
                stored[row, column] = float(item["quality_score"])

    used = {name: float(config[name]) for name in WEIGHT_NAMES}
    check = float(np.max(np.abs(_window_scores(features, used) - stored) * scoreable))
    result: dict[str, object] = {
        "available": True,
        "method": "window-score argmax vs utterance-level oracle input (proxy, in-sample)",
        "labeled_utterances": len(labeled),
        "formula_check_max_abs_error": check,
        "run_weights": used,
    }
    if check > 1e-6:
        result.update(available=False, reason="stored window scores do not match the scoring formula")
        return result
    if not labeled:
        result.update(verdict="insufficient_data", candidates=[])
        return result

    overlap = np.array(
        [
            np.clip(np.minimum(ends, row["end"]) - np.maximum(starts, row["start"]), 0.0, None)
            for row in labeled
        ]
    )
    oracle = np.array([int(row["oracle_input"]) for row in labeled])
    weight = np.array([float(row["characters"]) for row in labeled])
    coverage = overlap @ scoreable

    def agreement(weights: Mapping[str, float]) -> tuple[float, float]:
        scores = _window_scores(features, weights)
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = (overlap @ (scores * scoreable)) / coverage
        mean = np.where(coverage > 0.0, mean, -np.inf)
        ordered = np.sort(mean, axis=1)
        decided = np.isfinite(ordered[:, -1]) & (ordered[:, -1] > ordered[:, -2])
        hits = decided & (np.argmax(mean, axis=1) == oracle)
        return float(hits.mean()), float((hits * weight).sum() / weight.sum())

    baseline_rate, baseline_weighted = agreement(used)
    candidates = []
    for values in product(*WEIGHT_GRID.values()):
        weights = dict(used)
        weights.update(zip(WEIGHT_GRID, values))
        if weights["validity_weight"] + weights["speech_weight"] + weights["snr_weight"] <= 0.0:
            continue
        rate_value, weighted = agreement(weights)
        change = sum(abs(weights[name] - used[name]) for name in WEIGHT_GRID)
        candidates.append({"weights": weights, "agreement": rate_value, "character_weighted_agreement": weighted, "change": change})
    candidates.sort(key=lambda item: (-item["agreement"], item["change"]))
    best = candidates[0]
    if len(labeled) < CALIBRATION_MIN_LABELED:
        verdict = "insufficient_data"
    elif best["agreement"] - baseline_rate < CALIBRATION_MIN_GAIN:
        verdict = "keep_run_weights"
    else:
        verdict = "candidate_for_next_run"
    result.update(
        run_weight_agreement=baseline_rate,
        run_weight_character_weighted_agreement=baseline_weighted,
        best_candidate=best,
        gain=best["agreement"] - baseline_rate,
        verdict=verdict,
        minimum_labeled=CALIBRATION_MIN_LABELED,
        minimum_gain=CALIBRATION_MIN_GAIN,
        candidates=candidates[:5],
    )
    return result
