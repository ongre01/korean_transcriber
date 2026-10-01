"""Shared JSON Lines contract for the GUI backend bridge.

This module contains no AI/runtime dependencies so the bridge and its tests can
validate protocol data without importing OpenVINO, PyAV, or NumPy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import math
from typing import Iterable, Union


class ProtocolError(ValueError):
    """Raised when a bridge event violates the documented wire contract."""


class StateValue(str, Enum):
    PREPARING = "preparing"
    LOADING_MODEL = "loading_model"
    DECODING_AUDIO = "decoding_audio"
    DETECTING_SPEECH = "detecting_speech"
    TRANSCRIBING = "transcribing"
    DIARIZATION = "diarization"
    SAVING_RESULT = "saving_result"


@dataclass(frozen=True)
class StateEvent:
    value: StateValue


@dataclass(frozen=True)
class ProgressEvent:
    value: int | None
    processed_seconds: float | None = None
    total_seconds: float | None = None


@dataclass(frozen=True)
class SegmentEvent:
    start: float
    end: float
    speaker: int | None
    text: str


@dataclass(frozen=True)
class CompletedEvent:
    text_file: str
    srt_file: str


@dataclass(frozen=True)
class ErrorEvent:
    message: str


@dataclass(frozen=True)
class WarningEvent:
    message: str


BackendEvent = Union[
    StateEvent,
    ProgressEvent,
    SegmentEvent,
    CompletedEvent,
    ErrorEvent,
    WarningEvent,
]


def _required(payload: dict, name: str):
    if name not in payload:
        raise ProtocolError(f"missing required field: {name}")
    return payload[name]


def _reject_non_json_number(value: str):
    raise ProtocolError(f"invalid JSON number: {value}")


def _required_string(payload: dict, name: str, *, non_empty: bool = False) -> str:
    value = _required(payload, name)
    if not isinstance(value, str):
        raise ProtocolError(f"{name} must be a string")
    if non_empty and not value.strip():
        raise ProtocolError(f"{name} must not be empty")
    return value


def _finite_number(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ProtocolError(f"{name} must be finite")
    return result


def _non_negative_number(payload: dict, name: str) -> float:
    value = _finite_number(_required(payload, name), name)
    if value < 0.0:
        raise ProtocolError(f"{name} must be non-negative")
    return value


def parse_event_line(line: str | bytes) -> BackendEvent:
    """Parse and validate one UTF-8 JSON Lines event."""

    if isinstance(line, bytes):
        try:
            line = line.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ProtocolError("event is not valid UTF-8") from exc
    if not isinstance(line, str) or not line.strip():
        raise ProtocolError("event line must not be empty")

    try:
        payload = json.loads(line, parse_constant=_reject_non_json_number)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ProtocolError("event line must be one JSON object") from exc

    if not isinstance(payload, dict):
        raise ProtocolError("event line must be one JSON object")

    event_type = _required_string(payload, "type", non_empty=True)
    if event_type == "state":
        raw_value = _required_string(payload, "value", non_empty=True)
        try:
            return StateEvent(StateValue(raw_value))
        except ValueError as exc:
            raise ProtocolError(f"unknown state value: {raw_value}") from exc

    if event_type == "progress":
        raw_value = _required(payload, "value")
        if raw_value is None:
            progress = None
        elif (isinstance(raw_value, bool)
              or not isinstance(raw_value, (int, float))
              or not math.isfinite(float(raw_value))
              or not float(raw_value).is_integer()):
            raise ProtocolError("progress value must be an integer or null")
        elif not 0 <= raw_value <= 100:
            raise ProtocolError("progress value must be between 0 and 100")
        else:
            progress = int(raw_value)

        has_processed = "processed_seconds" in payload
        has_total = "total_seconds" in payload
        if has_processed != has_total:
            raise ProtocolError("processed_seconds and total_seconds must appear together")
        if progress is None and has_processed:
            raise ProtocolError("indeterminate progress must not contain time fields")
        if not has_processed:
            return ProgressEvent(progress)

        processed = _non_negative_number(payload, "processed_seconds")
        total = _non_negative_number(payload, "total_seconds")
        if processed > total:
            raise ProtocolError("processed_seconds must not exceed total_seconds")
        return ProgressEvent(progress, processed, total)

    if event_type == "segment":
        start = _non_negative_number(payload, "start")
        end = _non_negative_number(payload, "end")
        if end < start:
            raise ProtocolError("segment end must not precede start")

        speaker = _required(payload, "speaker")
        if speaker is not None:
            if (isinstance(speaker, bool)
                    or not isinstance(speaker, (int, float))
                    or not math.isfinite(float(speaker))
                    or not float(speaker).is_integer()
                    or speaker < 1
                    or speaker > 2_147_483_647):
                raise ProtocolError("speaker must be a positive integer or null")
            speaker = int(speaker)
        text = _required_string(payload, "text", non_empty=True)
        return SegmentEvent(start, end, speaker, text)

    if event_type == "completed":
        return CompletedEvent(
            _required_string(payload, "text_file", non_empty=True),
            _required_string(payload, "srt_file", non_empty=True),
        )

    if event_type == "error":
        return ErrorEvent(_required_string(payload, "message", non_empty=True))

    if event_type == "warning":
        return WarningEvent(_required_string(payload, "message", non_empty=True))

    raise ProtocolError(f"unknown event type: {event_type}")


class EventStreamValidator:
    """Validate terminal-event and process-exit semantics for one bridge run."""

    def __init__(self) -> None:
        self._terminal: str | None = None
        self._state: StateValue | None = None

    def accept(self, event: BackendEvent) -> None:
        if self._terminal is not None:
            raise ProtocolError(f"event received after terminal {self._terminal} event")
        if isinstance(event, StateEvent):
            self._state = event.value
        elif (isinstance(event, ProgressEvent)
              and event.value == 100
              and self._state is not StateValue.SAVING_RESULT):
            raise ProtocolError("100% progress must follow the saving_result state")
        elif isinstance(event, CompletedEvent):
            self._terminal = "completed"
        elif isinstance(event, ErrorEvent):
            self._terminal = "error"

    def finish(self, exit_code: int) -> None:
        if self._terminal is None:
            raise ProtocolError("stream ended without completed or error event")
        if self._terminal == "completed" and exit_code != 0:
            raise ProtocolError("completed event requires exit code 0")
        if self._terminal == "error" and exit_code == 0:
            raise ProtocolError("error event requires a non-zero exit code")


def validate_stream(lines: Iterable[str | bytes], exit_code: int) -> list[BackendEvent]:
    validator = EventStreamValidator()
    events = []
    for line in lines:
        event = parse_event_line(line)
        validator.accept(event)
        events.append(event)
    validator.finish(exit_code)
    return events
