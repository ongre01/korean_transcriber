"""Controlled overlap and far-field cases built from real aligned recordings.

Utterances are cut from the aligned recordings of every device, so device
noise, coloration and room reverberation are real.  A case places one or two
utterances on a new timeline and sums them on every channel:

* talker position is simulated by gain only: the device a talker sits next
  to keeps the utterance's level, every other device receives it attenuated
  by ``far_db`` (no extra reverberation or delay is simulated);
* each utterance is scaled so that its level on its near device is
  ``target_level_dbfs``, which removes loudness differences between talkers;
* summing two recordings also sums their background noise.

The reference text of an utterance is the Whisper transcript of its clean,
un-mixed clip (see ``experiment.build_pool``), not a human transcript.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import Mapping, Sequence

import numpy as np

from .scoring import PRIMARY, SECONDARY, ReferenceUtterance
from .signals import SAMPLE_RATE, active_rms, db_to_gain


OVERLAP = "overlap"
FAR_SMALL = "far_small"
SINGLE = "single"

# (condition, far attenuation dB, overlapped share of the shorter utterance, colocated)
OVERLAP_CONDITIONS = (
    ("cross_6db_full", -6.0, 1.0, False),
    ("cross_12db_full", -12.0, 1.0, False),
    ("cross_12db_half", -12.0, 0.5, False),
    ("colocated_full", 0.0, 1.0, True),
)
# (condition, far attenuation dB, placement of the short utterance)
FAR_SMALL_CONDITIONS = (
    ("tail_6db", -6.0, "tail"),
    ("tail_12db", -12.0, "tail"),
    ("after_6db", -6.0, "after"),
    ("after_12db", -12.0, "after"),
)
# (condition, near channel, far attenuation dB)
SINGLE_CONDITIONS = (
    ("alone_near0_6db", 0, -6.0),
    ("alone_near1_6db", 1, -6.0),
)
TAIL_END_GAP_SECONDS = 0.3
AFTER_GAP_SECONDS = 0.4


@dataclass(frozen=True)
class Candidate:
    """An isolated utterance on the reference timeline, with silent margins."""

    candidate_id: str
    clip_start: float
    clip_end: float
    core_start: float
    core_end: float

    @property
    def core_seconds(self) -> float:
        return self.core_end - self.core_start

    @property
    def core_offset(self) -> float:
        return self.core_start - self.clip_start

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "clip_start": self.clip_start,
            "clip_end": self.clip_end,
            "core_start": self.core_start,
            "core_end": self.core_end,
        }


def detect_any_channel_speech(
    channels: Sequence[np.ndarray],
    noise_floor_dbfs: Sequence[float],
    *,
    offset_db: float,
    min_silence_seconds: float,
    padding_seconds: float,
) -> list[tuple[float, float]]:
    """Union of every channel's energy-VAD speech, in seconds.

    Each channel's threshold is its own noise floor plus ``offset_db``,
    because the devices' noise floors differ by more than 15 dB.
    """

    from vad import detect_speech_regions, merge_speech_regions

    regions = []
    for channel, floor in zip(channels, noise_floor_dbfs):
        regions += detect_speech_regions(
            np.asarray(channel, dtype=np.float32),
            SAMPLE_RATE,
            threshold_db=min(-1.0, float(floor) + offset_db),
            min_silence_duration=min_silence_seconds,
            padding_duration=padding_seconds,
        )
    return [
        (region.start_sample / SAMPLE_RATE, region.end_sample / SAMPLE_RATE)
        for region in merge_speech_regions(regions)
    ]


def select_candidates(
    speech: Sequence[tuple[float, float]],
    *,
    timeline_seconds: float,
    margin_seconds: float,
    min_core_seconds: float,
    max_core_seconds: float,
) -> list[Candidate]:
    """Speech regions with at least ``margin_seconds`` of silence on both sides.

    ``speech`` is the sorted union of all devices' speech, so the margins are
    silent on every device, not only on the reference.
    """

    candidates: list[Candidate] = []
    for index, (core_start, core_end) in enumerate(speech):
        if not min_core_seconds <= core_end - core_start <= max_core_seconds:
            continue
        clip_start, clip_end = core_start - margin_seconds, core_end + margin_seconds
        if clip_start < 0.0 or clip_end > timeline_seconds:
            continue
        if index > 0 and speech[index - 1][1] > clip_start:
            continue
        if index + 1 < len(speech) and speech[index + 1][0] < clip_end:
            continue
        candidates.append(Candidate(f"u{index:05d}", clip_start, clip_end, core_start, core_end))
    return candidates


@dataclass(frozen=True)
class PoolUtterance:
    """A candidate whose clean clip transcribed consistently on every device."""

    candidate: Candidate
    reference_text: str
    reference_channel: int
    channel_texts: tuple[str, ...]
    agreement_cer: float
    embedding: tuple[float, ...]

    @property
    def utterance_id(self) -> str:
        return self.candidate.candidate_id

    def similarity(self, other: PoolUtterance) -> float:
        return float(np.dot(self.embedding, other.embedding))

    def to_dict(self) -> dict[str, object]:
        return {
            **self.candidate.to_dict(),
            "reference_text": self.reference_text,
            "reference_channel": self.reference_channel,
            "channel_texts": list(self.channel_texts),
            "agreement_cer": self.agreement_cer,
        }


@dataclass(frozen=True)
class Placement:
    utterance_id: str
    role: str
    offset_seconds: float
    near_channel: int
    gains_db: tuple[float, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "utterance_id": self.utterance_id,
            "role": self.role,
            "offset_seconds": self.offset_seconds,
            "near_channel": self.near_channel,
            "gains_db": list(self.gains_db),
        }


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    category: str
    condition: str
    placements: tuple[Placement, ...]
    duration_seconds: float

    @property
    def group(self) -> str:
        return f"{self.category}/{self.condition}"

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "condition": self.condition,
            "group": self.group,
            "duration_seconds": self.duration_seconds,
            "placements": [placement.to_dict() for placement in self.placements],
        }


def position_gains(channel_count: int, near_channel: int, far_db: float) -> tuple[float, ...]:
    return tuple(0.0 if channel == near_channel else far_db for channel in range(channel_count))


def _clip_seconds(utterance: PoolUtterance) -> float:
    return utterance.candidate.clip_end - utterance.candidate.clip_start


def _layout(
    first: PoolUtterance,
    second: PoolUtterance,
    second_core_start: float,
) -> tuple[float, float, float]:
    """Clip offsets that put the second core at ``second_core_start``.

    Times are relative to the first clip starting at 0; the whole layout is
    shifted right when the second clip would start before 0.
    """

    first_offset = 0.0
    second_offset = second_core_start - second.candidate.core_offset
    shift = max(0.0, -second_offset)
    first_offset += shift
    second_offset += shift
    duration = max(first_offset + _clip_seconds(first), second_offset + _clip_seconds(second))
    return first_offset, second_offset, duration


def overlap_layout(first: PoolUtterance, second: PoolUtterance, overlap_share: float) -> tuple[float, float, float]:
    """Second core ends overlapping the first core's end by ``overlap_share`` of the shorter core."""

    overlap = overlap_share * min(first.candidate.core_seconds, second.candidate.core_seconds)
    first_core_end = first.candidate.core_offset + first.candidate.core_seconds
    return _layout(first, second, first_core_end - overlap)


def far_small_layout(long: PoolUtterance, short: PoolUtterance, placement: str) -> tuple[float, float, float]:
    long_core_start = long.candidate.core_offset
    long_core_end = long_core_start + long.candidate.core_seconds
    if placement == "tail":
        start = max(long_core_start, long_core_end - TAIL_END_GAP_SECONDS - short.candidate.core_seconds)
    elif placement == "after":
        start = long_core_end + AFTER_GAP_SECONDS
    else:
        raise ValueError(f"unknown far_small placement: {placement}")
    return _layout(long, short, start)


def choose_pairs(
    firsts: Sequence[PoolUtterance],
    seconds: Sequence[PoolUtterance],
    count: int,
    rng: random.Random,
    *,
    max_similarity: float,
) -> list[tuple[PoolUtterance, PoolUtterance]]:
    """Up to ``count`` disjoint pairs of different-sounding talkers.

    Speaker identity comes from embedding cosine similarity, because the
    meeting's diarization labels are not reliable enough to tell talkers apart.
    """

    first_order = list(firsts)
    second_order = list(seconds)
    rng.shuffle(first_order)
    rng.shuffle(second_order)
    used: set[str] = set()
    pairs: list[tuple[PoolUtterance, PoolUtterance]] = []
    for first in first_order:
        if len(pairs) == count:
            break
        if first.utterance_id in used:
            continue
        for second in second_order:
            if second.utterance_id in used or second.utterance_id == first.utterance_id:
                continue
            if first.similarity(second) < max_similarity:
                used.update((first.utterance_id, second.utterance_id))
                pairs.append((first, second))
                break
    return pairs


def build_cases(
    pool: Sequence[PoolUtterance],
    rng: random.Random,
    *,
    channel_count: int,
    reference_channel: int,
    overlap_pairs: int,
    far_small_pairs: int,
    single_per_position: int,
    max_similarity: float,
    overlap_core_seconds: tuple[float, float] = (1.5, 8.0),
    long_min_seconds: float = 3.0,
    short_max_seconds: float = 2.0,
) -> list[CaseSpec]:
    """All cases of the experiment; every pair appears under every condition of its category."""

    if channel_count < 2:
        raise ValueError("cases need at least two channels")
    cases: list[CaseSpec] = []

    overlap_pool = [u for u in pool if overlap_core_seconds[0] <= u.candidate.core_seconds <= overlap_core_seconds[1]]
    for pair_index, (first, second) in enumerate(
        choose_pairs(overlap_pool, overlap_pool, overlap_pairs, rng, max_similarity=max_similarity)
    ):
        for condition, far_db, share, colocated in OVERLAP_CONDITIONS:
            first_offset, second_offset, duration = overlap_layout(first, second, share)
            first_near, second_near = (reference_channel, reference_channel) if colocated else (0, 1)
            cases.append(
                CaseSpec(
                    f"{OVERLAP}-{pair_index:02d}-{condition}",
                    OVERLAP,
                    condition,
                    (
                        Placement(first.utterance_id, PRIMARY, first_offset, first_near,
                                  position_gains(channel_count, first_near, far_db)),
                        Placement(second.utterance_id, SECONDARY, second_offset, second_near,
                                  position_gains(channel_count, second_near, far_db)),
                    ),
                    duration,
                )
            )

    longs = [u for u in pool if u.candidate.core_seconds >= long_min_seconds]
    shorts = [u for u in pool if u.candidate.core_seconds <= short_max_seconds]
    for pair_index, (long, short) in enumerate(
        choose_pairs(longs, shorts, far_small_pairs, rng, max_similarity=max_similarity)
    ):
        for condition, far_db, placement in FAR_SMALL_CONDITIONS:
            long_offset, short_offset, duration = far_small_layout(long, short, placement)
            cases.append(
                CaseSpec(
                    f"{FAR_SMALL}-{pair_index:02d}-{condition}",
                    FAR_SMALL,
                    condition,
                    (
                        Placement(long.utterance_id, PRIMARY, long_offset, 1, position_gains(channel_count, 1, far_db)),
                        Placement(short.utterance_id, SECONDARY, short_offset, 0,
                                  position_gains(channel_count, 0, far_db)),
                    ),
                    duration,
                )
            )

    singles = list(overlap_pool)
    rng.shuffle(singles)
    for condition, near, far_db in SINGLE_CONDITIONS:
        for index, utterance in enumerate(singles[:single_per_position]):
            cases.append(
                CaseSpec(
                    f"{SINGLE}-{index:02d}-{condition}",
                    SINGLE,
                    condition,
                    (Placement(utterance.utterance_id, PRIMARY, 0.0, near,
                               position_gains(channel_count, near, far_db)),),
                    _clip_seconds(utterance),
                )
            )
        singles = singles[single_per_position:] + singles[:single_per_position]
    return cases


def synthesize(
    case: CaseSpec,
    clips: Mapping[str, Sequence[np.ndarray]],
    *,
    target_level_dbfs: float,
) -> list[np.ndarray]:
    """Mix a case on every channel; returns one float32 array per channel."""

    channel_count = len(case.placements[0].gains_db)
    length = int(math.ceil(case.duration_seconds * SAMPLE_RATE))
    output = np.zeros((channel_count, length), dtype=np.float64)
    target = db_to_gain(target_level_dbfs)
    for placement in case.placements:
        clip = clips[placement.utterance_id]
        scale = target / max(active_rms(np.asarray(clip[placement.near_channel])), 1e-9)
        start = int(round(placement.offset_seconds * SAMPLE_RATE))
        for channel in range(channel_count):
            values = np.asarray(clip[channel], dtype=np.float64)
            end = min(length, start + values.size)
            output[channel, start:end] += values[: end - start] * scale * db_to_gain(placement.gains_db[channel])
    return [np.clip(row, -1.0, 1.0).astype(np.float32) for row in output]


def case_references(case: CaseSpec, pool: Mapping[str, PoolUtterance]) -> list[ReferenceUtterance]:
    return [
        ReferenceUtterance(placement.utterance_id, placement.role, pool[placement.utterance_id].reference_text)
        for placement in case.placements
    ]
