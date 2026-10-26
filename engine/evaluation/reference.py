"""Human reference transcripts for MM-10 accuracy scoring.

Times are seconds on the reference input's timeline (the recording at the
dataset's ``reference_index``), which is also the timeline of the merged
multi-input result.  ``scored_regions`` limits scoring to transcribed
excerpts, so an hour-long meeting can be evaluated from a few carefully
transcribed parts; without it the whole recording is scored.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path

from .metrics import character_tokens


REFERENCE_VERSION = 1
# Utterance times are hand-placed, so a boundary may sit this far outside its
# scored region without being an annotation error.
REGION_TOLERANCE_SECONDS = 0.05


class ReferenceError(ValueError):
    """The reference transcript file is missing or malformed."""


@dataclass(frozen=True)
class ReferenceUtterance:
    id: str
    start: float
    end: float
    speaker: str | None
    text: str
    tags: tuple[str, ...] = ()

    @property
    def midpoint(self) -> float:
        return (self.start + self.end) / 2.0


@dataclass(frozen=True)
class ReferenceTranscript:
    path: Path | None
    utterances: tuple[ReferenceUtterance, ...]
    scored_regions: tuple[tuple[float, float], ...] | None

    @property
    def speakers(self) -> tuple[str, ...]:
        return tuple(sorted({item.speaker for item in self.utterances if item.speaker is not None}))

    @property
    def scored_seconds(self) -> float | None:
        if self.scored_regions is None:
            return None
        return sum(end - start for start, end in self.scored_regions)

    def contains(self, seconds: float) -> bool:
        """Whether a time falls in a scored region (always, without regions)."""

        if self.scored_regions is None:
            return True
        return any(start <= seconds < end for start, end in self.scored_regions)


def _seconds(value: object, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReferenceError(f"{location} must be a number of seconds")
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise ReferenceError(f"{location} must be finite and not negative")
    return result


def _interval(item: object, location: str) -> tuple[float, float]:
    if not isinstance(item, dict):
        raise ReferenceError(f"{location} must be an object")
    start = _seconds(item.get("start"), f"{location}.start")
    end = _seconds(item.get("end"), f"{location}.end")
    if end <= start:
        raise ReferenceError(f"{location}.end must be after start")
    return start, end


def overlapping_utterance_ids(utterances: tuple[ReferenceUtterance, ...]) -> set[str]:
    """Utterances that overlap in time with another speaker's utterance."""

    overlapping: set[str] = set()
    ordered = sorted(utterances, key=lambda item: item.start)
    for position, first in enumerate(ordered):
        for second in ordered[position + 1:]:
            if second.start >= first.end:
                break
            if first.speaker != second.speaker:
                overlapping.update((first.id, second.id))
    return overlapping


def parse_reference(payload: object, *, path: Path | None = None) -> ReferenceTranscript:
    if not isinstance(payload, dict):
        raise ReferenceError("root must be a JSON object")
    unknown = sorted(set(payload) - {"version", "description", "scored_regions", "utterances"})
    if unknown:
        raise ReferenceError(f"root has unknown fields: {', '.join(unknown)}")
    version = payload.get("version")
    if isinstance(version, bool) or version != REFERENCE_VERSION:
        raise ReferenceError(f"version must be the integer {REFERENCE_VERSION}")

    regions = None
    if payload.get("scored_regions") is not None:
        raw_regions = payload["scored_regions"]
        if not isinstance(raw_regions, list) or not raw_regions:
            raise ReferenceError("scored_regions must be a non-empty array or null")
        regions = tuple(
            sorted(_interval(item, f"scored_regions[{index}]") for index, item in enumerate(raw_regions))
        )
        for (_, previous_end), (start, _) in zip(regions, regions[1:]):
            if start < previous_end:
                raise ReferenceError("scored_regions must not overlap")

    raw_utterances = payload.get("utterances")
    if not isinstance(raw_utterances, list) or not raw_utterances:
        raise ReferenceError("utterances must be a non-empty array")
    utterances = []
    identifiers: set[str] = set()
    for index, raw in enumerate(raw_utterances):
        location = f"utterances[{index}]"
        if not isinstance(raw, dict):
            raise ReferenceError(f"{location} must be an object")
        extra = sorted(set(raw) - {"id", "start", "end", "speaker", "text", "tags"})
        if extra:
            raise ReferenceError(f"{location} has unknown fields: {', '.join(extra)}")
        identifier = raw.get("id", f"u{index + 1:04d}")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ReferenceError(f"{location}.id must be a non-empty string")
        if identifier in identifiers:
            raise ReferenceError(f"{location}.id duplicates {identifier!r}")
        identifiers.add(identifier)
        start, end = _interval(raw, location)
        speaker = raw.get("speaker")
        if speaker is not None and (not isinstance(speaker, str) or not speaker.strip()):
            raise ReferenceError(f"{location}.speaker must be a non-empty string or null")
        text = raw.get("text")
        if not isinstance(text, str) or not character_tokens(text):
            raise ReferenceError(f"{location}.text must contain at least one letter or digit")
        tags = raw.get("tags", [])
        if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
            raise ReferenceError(f"{location}.tags must be an array of non-empty strings")
        utterance = ReferenceUtterance(
            id=identifier,
            start=start,
            end=end,
            speaker=None if speaker is None else speaker.strip(),
            text=text,
            tags=tuple(dict.fromkeys(tag.strip() for tag in tags)),
        )
        if regions is not None and not any(
            region_start - REGION_TOLERANCE_SECONDS <= start
            and end <= region_end + REGION_TOLERANCE_SECONDS
            for region_start, region_end in regions
        ):
            raise ReferenceError(f"{location} ({identifier}) is not inside a scored region")
        utterances.append(utterance)

    utterances.sort(key=lambda item: (item.start, item.end, item.id))
    return ReferenceTranscript(path=path, utterances=tuple(utterances), scored_regions=regions)


def load_reference(path: str | Path) -> ReferenceTranscript:
    reference_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(reference_path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ReferenceError(f"reference file could not be read: {reference_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ReferenceError(
            f"reference file is not valid JSON: {reference_path} (line {exc.lineno}, column {exc.colno})"
        ) from exc
    return parse_reference(payload, path=reference_path)
