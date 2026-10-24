#!/usr/bin/env python
"""JSON Lines bridge between the Qt frontend and the transcription engine."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import dataclass
import gc
import importlib
import json
import math
from numbers import Integral
import os
from pathlib import Path
import sys
import traceback
from typing import TYPE_CHECKING, Callable, TextIO

from backend_protocol import EventStreamValidator, parse_event_line

if TYPE_CHECKING:
    from vad import SpeechRegion


APP_DIR = Path(__file__).resolve().parent
DEFAULT_WINDOW_SECONDS = 120.0
DEFAULT_OVERLAP_SECONDS = 4.0
DEFAULT_HOTWORDS_FILE = APP_DIR / "hotwords.txt"
DEFAULT_INITIAL_PROMPT_FILE = APP_DIR / "initial_prompt.txt"
DEFAULT_DIARIZATION_DIR = APP_DIR / "models" / "diarization"
DEFAULT_SEGMENTATION_MODEL = (
    DEFAULT_DIARIZATION_DIR
    / "sherpa-onnx-pyannote-segmentation-3-0"
    / "model.onnx"
)
DEFAULT_EMBEDDING_MODEL = (
    DEFAULT_DIARIZATION_DIR
    / "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
)
MULTI_INPUT_MANIFEST_VERSION = 1
MULTI_INPUT_ALIGNMENT_MODE = "auto"
MULTI_INPUT_SELECTION_MODE = "automatic"
MIN_SIGNED_64_BIT_INTEGER = -(2 ** 63)
MAX_SIGNED_64_BIT_INTEGER = 2 ** 63 - 1
# User-facing prefixes for unexpected failures, keyed by the multi-input
# pre-processing stage that was running.
MULTI_INPUT_STAGE_FAILURES = {
    "decoding_audio": "Multi-input audio decoding failed",
    "aligning_audio": "Multi-input time alignment failed",
    "detecting_speech": "Multi-input speech detection failed",
    "assessing_channels": "Channel quality analysis failed",
    "selecting_channels": "Channel selection failed",
    "rendering_merged_audio": "Merged audio could not be created",
}


class BridgeArgumentError(ValueError):
    pass


class BridgeFailure(RuntimeError):
    """A failure reported to the user as ``message``.

    ``diagnostic`` holds structured detail that only goes to stderr, next to
    the traceback, so the ``error`` event stays short.
    """

    def __init__(
        self,
        message: str,
        exit_code: int = 1,
        *,
        diagnostic: str | None = None,
    ):
        super().__init__(message)
        self.exit_code = exit_code
        self.diagnostic = diagnostic


class BridgeArgumentParser(argparse.ArgumentParser):
    """Turn argparse failures into protocol events instead of process exits."""

    def error(self, message: str) -> None:
        raise BridgeArgumentError(message)

    def print_help(self, file=None) -> None:
        super().print_help(file=sys.stderr if file is None else file)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if message:
            self._print_message(message, sys.stderr)
        raise BridgeArgumentError("help requested" if status == 0 else "invalid arguments")


class EventWriter:
    """Write protocol events after checking them against the wire contract.

    Every line is parsed with the same validator the tests and Qt mirror use
    before it is written, so a malformed event or any event after the single
    terminal ``completed``/``error`` raises here instead of reaching stdout.
    """

    def __init__(self, stream: TextIO):
        self._stream = stream
        self._validator = EventStreamValidator()

    @property
    def terminal(self) -> str | None:
        return self._validator.terminal

    def emit(self, event_type: str, **fields) -> None:
        payload = {"type": event_type, **fields}
        line = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        self._validator.accept(parse_event_line(line.encode("utf-8")))
        self._stream.write(line + "\n")
        self._stream.flush()

    def state(self, value: str) -> None:
        self.emit("state", value=value)

    def warning(self, message: str) -> None:
        self.emit("warning", message=message)

    def completed(
        self,
        text_file: str | Path,
        srt_file: str | Path,
        *,
        merged_audio_file: str | Path | None = None,
        alignment_file: str | Path | None = None,
        selection_file: str | Path | None = None,
    ) -> None:
        fields = {"text_file": str(text_file), "srt_file": str(srt_file)}
        artifacts = {
            "merged_audio_file": merged_audio_file,
            "alignment_file": alignment_file,
            "selection_file": selection_file,
        }
        fields.update(
            (name, str(path)) for name, path in artifacts.items() if path is not None
        )
        self.emit("completed", **fields)

    def error(self, message: str) -> None:
        self.emit("error", message=message if message.strip() else "Backend processing failed.")

    def progress(
        self,
        value: int | None,
        processed_seconds: float | None = None,
        total_seconds: float | None = None,
    ) -> None:
        fields = {"value": value}
        if value is not None and processed_seconds is not None and total_seconds is not None:
            fields["processed_seconds"] = processed_seconds
            fields["total_seconds"] = total_seconds
        self.emit("progress", **fields)


def _speaker_count(value: str) -> int:
    if value.casefold() == "auto":
        return -1
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be auto, 2, 3, 4, or 5") from exc
    if result not in (2, 3, 4, 5):
        raise argparse.ArgumentTypeError("must be auto, 2, 3, 4, or 5")
    return result


def _device(value: str) -> str:
    result = value.upper()
    if result not in ("AUTO", "NPU", "CPU", "GPU"):
        raise argparse.ArgumentTypeError("must be AUTO, NPU, CPU, or GPU")
    return result


def _diarization_device(value: str) -> str:
    result = value.upper()
    if result not in ("NPU", "CPU", "GPU"):
        raise argparse.ArgumentTypeError("must be NPU, CPU, or GPU")
    return result


def _manifest_field(payload: dict, name: str, location: str):
    if name not in payload:
        raise BridgeArgumentError(
            f"multi-input manifest {location}.{name} is required"
        )
    return payload[name]


def _manifest_object(value: object, location: str) -> dict:
    if not isinstance(value, dict):
        raise BridgeArgumentError(
            f"multi-input manifest {location} must be a JSON object"
        )
    return value


def _reject_unknown_manifest_fields(
    payload: dict,
    allowed_fields: set[str],
    location: str,
) -> None:
    unknown_fields = [name for name in payload if name not in allowed_fields]
    if unknown_fields:
        names = ", ".join(repr(name) for name in unknown_fields)
        raise BridgeArgumentError(
            f"multi-input manifest {location} contains unknown field(s): {names}"
        )


def _manifest_string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BridgeArgumentError(
            f"multi-input manifest {location} must be a non-empty string"
        )
    return value.strip()


def _manifest_number(value: object, location: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BridgeArgumentError(
            f"multi-input manifest {location} must be a number"
        )
    try:
        finite_value = math.isfinite(float(value))
    except (OverflowError, ValueError) as exc:
        raise BridgeArgumentError(
            f"multi-input manifest {location} must be finite"
        ) from exc
    if not finite_value:
        raise BridgeArgumentError(
            f"multi-input manifest {location} must be finite"
        )
    return value


def _manifest_milliseconds(value: object, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BridgeArgumentError(
            f"multi-input manifest {location} must be an integer number of milliseconds"
        )
    if not MIN_SIGNED_64_BIT_INTEGER <= value <= MAX_SIGNED_64_BIT_INTEGER:
        raise BridgeArgumentError(
            f"multi-input manifest {location} is outside the signed 64-bit range"
        )
    return value


def _manifest_sample_index(value: object, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BridgeArgumentError(
            f"multi-input manifest {location} must be an integer sample index"
        )
    if not 0 <= value <= MAX_SIGNED_64_BIT_INTEGER:
        raise BridgeArgumentError(
            f"multi-input manifest {location} must be between 0 and the signed 64-bit maximum"
        )
    return value


def _manifest_selection_overrides(
    value: object,
    *,
    input_count: int,
) -> list[dict[str, int]]:
    """Validate user channel overrides on the 16 kHz reference timeline.

    Whether an override ends inside the reference recording is only known
    after decoding, so that bound is checked by the analysis stage.
    """

    if not isinstance(value, list):
        raise BridgeArgumentError(
            "multi-input manifest selection.overrides must be a JSON array"
        )
    overrides: list[dict[str, int]] = []
    for index, raw_override in enumerate(value):
        location = f"selection.overrides[{index}]"
        item = _manifest_object(raw_override, location)
        _reject_unknown_manifest_fields(
            item,
            {"start_sample", "end_sample", "input_index"},
            location,
        )
        start_sample = _manifest_sample_index(
            _manifest_field(item, "start_sample", location),
            f"{location}.start_sample",
        )
        end_sample = _manifest_sample_index(
            _manifest_field(item, "end_sample", location),
            f"{location}.end_sample",
        )
        if end_sample <= start_sample:
            raise BridgeArgumentError(
                f"multi-input manifest {location}.end_sample must be greater than start_sample"
            )
        input_index = _manifest_field(item, "input_index", location)
        if (
            isinstance(input_index, bool)
            or not isinstance(input_index, int)
            or not 0 <= input_index < input_count
        ):
            raise BridgeArgumentError(
                f"multi-input manifest {location}.input_index must identify an input file"
            )
        overrides.append(
            {
                "start_sample": start_sample,
                "end_sample": end_sample,
                "input_index": input_index,
            }
        )

    overrides.sort(key=lambda item: (item["start_sample"], item["end_sample"]))
    for previous, current in zip(overrides, overrides[1:]):
        if current["start_sample"] < previous["end_sample"]:
            raise BridgeArgumentError(
                "multi-input manifest selection.overrides must not overlap"
            )
    return overrides


def _resolve_manifest_input_path(
    value: object,
    *,
    location: str,
    base_directory: Path,
) -> Path:
    path_text = _manifest_string(value, location)
    try:
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            path = base_directory / path
        path = path.resolve()
        is_file = path.is_file()
    except (OSError, RuntimeError, ValueError) as exc:
        raise BridgeArgumentError(
            f"multi-input manifest {location} is invalid: {exc}"
        ) from exc
    if not is_file:
        raise BridgeArgumentError(
            f"multi-input manifest {location} does not identify an existing file: {path}"
        )
    return path


def validate_multi_input_manifest(
    payload: object,
    *,
    base_directory: str | Path,
) -> dict[str, object]:
    """Validate and normalize a version 1 multi-input manifest.

    Input paths are returned as resolved absolute strings. Relative paths are
    interpreted from the directory containing the manifest, not from the
    process working directory.
    """

    manifest = _manifest_object(payload, "root")
    _reject_unknown_manifest_fields(
        manifest,
        {"version", "inputs", "reference_index", "alignment", "selection"},
        "root",
    )

    version = _manifest_field(manifest, "version", "root")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != MULTI_INPUT_MANIFEST_VERSION
    ):
        raise BridgeArgumentError(
            "multi-input manifest version must be the integer 1"
        )

    raw_inputs = _manifest_field(manifest, "inputs", "root")
    if not isinstance(raw_inputs, list):
        raise BridgeArgumentError(
            "multi-input manifest inputs must be a JSON array"
        )
    if len(raw_inputs) < 2:
        raise BridgeArgumentError(
            "multi-input manifest inputs must contain at least two files"
        )

    try:
        manifest_directory = Path(base_directory).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise BridgeArgumentError(
            f"multi-input manifest base directory is invalid: {exc}"
        ) from exc

    normalized_inputs: list[dict[str, str]] = []
    input_path_indexes: dict[str, int] = {}
    for index, raw_input in enumerate(raw_inputs):
        location = f"inputs[{index}]"
        item = _manifest_object(raw_input, location)
        _reject_unknown_manifest_fields(item, {"path", "label"}, location)
        path = _resolve_manifest_input_path(
            _manifest_field(item, "path", location),
            location=f"{location}.path",
            base_directory=manifest_directory,
        )
        label = _manifest_string(
            _manifest_field(item, "label", location),
            f"{location}.label",
        )

        path_key = os.path.normcase(str(path))
        previous_index = input_path_indexes.get(path_key)
        if previous_index is not None:
            raise BridgeArgumentError(
                f"multi-input manifest {location}.path duplicates inputs[{previous_index}].path"
            )
        input_path_indexes[path_key] = index
        normalized_inputs.append({"path": str(path), "label": label})

    reference_index = _manifest_field(manifest, "reference_index", "root")
    if isinstance(reference_index, bool) or not isinstance(reference_index, int):
        raise BridgeArgumentError(
            "multi-input manifest reference_index must be an integer"
        )
    if reference_index < 0 or reference_index >= len(normalized_inputs):
        raise BridgeArgumentError(
            "multi-input manifest reference_index must identify an input file"
        )

    alignment = _manifest_object(
        _manifest_field(manifest, "alignment", "root"),
        "alignment",
    )
    _reject_unknown_manifest_fields(
        alignment,
        {"mode", "manual_offsets_ms"},
        "alignment",
    )
    alignment_mode = _manifest_string(
        _manifest_field(alignment, "mode", "alignment"),
        "alignment.mode",
    )
    if alignment_mode != MULTI_INPUT_ALIGNMENT_MODE:
        raise BridgeArgumentError(
            'multi-input manifest alignment.mode must be "auto"'
        )
    raw_manual_offsets = _manifest_object(
        _manifest_field(alignment, "manual_offsets_ms", "alignment"),
        "alignment.manual_offsets_ms",
    )
    manual_offsets: dict[int, int] = {}
    for raw_index, raw_offset in raw_manual_offsets.items():
        if not isinstance(raw_index, str):
            raise BridgeArgumentError(
                "multi-input manifest alignment.manual_offsets_ms indexes must be strings"
            )
        try:
            input_index = int(raw_index, 10)
        except ValueError as exc:
            raise BridgeArgumentError(
                "multi-input manifest alignment.manual_offsets_ms indexes must be "
                "canonical zero-based integers"
            ) from exc
        if input_index < 0 or raw_index != str(input_index):
            raise BridgeArgumentError(
                "multi-input manifest alignment.manual_offsets_ms indexes must be "
                "canonical zero-based integers"
            )
        if input_index >= len(normalized_inputs):
            raise BridgeArgumentError(
                "multi-input manifest alignment.manual_offsets_ms index "
                f"{raw_index} does not identify an input file"
            )
        manual_offsets[input_index] = _manifest_milliseconds(
            raw_offset,
            f"alignment.manual_offsets_ms.{raw_index}",
        )

    selection = _manifest_object(
        _manifest_field(manifest, "selection", "root"),
        "selection",
    )
    _reject_unknown_manifest_fields(
        selection,
        {"mode", "window_seconds", "hop_seconds", "overrides"},
        "selection",
    )
    selection_mode = _manifest_string(
        _manifest_field(selection, "mode", "selection"),
        "selection.mode",
    )
    if selection_mode != MULTI_INPUT_SELECTION_MODE:
        raise BridgeArgumentError(
            'multi-input manifest selection.mode must be "automatic"'
        )
    window_seconds = _manifest_number(
        _manifest_field(selection, "window_seconds", "selection"),
        "selection.window_seconds",
    )
    hop_seconds = _manifest_number(
        _manifest_field(selection, "hop_seconds", "selection"),
        "selection.hop_seconds",
    )
    if window_seconds <= 0:
        raise BridgeArgumentError(
            "multi-input manifest selection.window_seconds must be greater than 0"
        )
    if hop_seconds <= 0 or hop_seconds > window_seconds:
        raise BridgeArgumentError(
            "multi-input manifest selection.hop_seconds must be greater than 0 "
            "and no greater than selection.window_seconds"
        )
    overrides = _manifest_selection_overrides(
        selection.get("overrides", []),
        input_count=len(normalized_inputs),
    )

    return {
        "version": version,
        "inputs": normalized_inputs,
        "reference_index": reference_index,
        "alignment": {
            "mode": alignment_mode,
            "manual_offsets_ms": manual_offsets,
        },
        "selection": {
            "mode": selection_mode,
            "window_seconds": window_seconds,
            "hop_seconds": hop_seconds,
            "overrides": overrides,
        },
    }


def _reject_non_json_manifest_number(value: str):
    raise BridgeArgumentError(
        f"multi-input manifest contains a non-JSON number: {value}"
    )


def _reject_duplicate_manifest_fields(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise BridgeArgumentError(
                f"multi-input manifest contains a duplicate JSON field: {name!r}"
            )
        result[name] = value
    return result


def _load_multi_input_manifest(
    manifest_path: str | Path,
) -> tuple[Path, dict[str, object]]:
    path_text = str(manifest_path).strip()
    if not path_text:
        raise BridgeArgumentError(
            "--multi-input-manifest must identify a JSON file"
        )
    try:
        path = Path(path_text).expanduser().resolve()
        is_file = path.is_file()
    except (OSError, RuntimeError, ValueError) as exc:
        raise BridgeArgumentError(
            f"multi-input manifest path is invalid: {exc}"
        ) from exc
    if not is_file:
        raise BridgeArgumentError(
            f"multi-input manifest file not found: {path}"
        )

    try:
        document = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise BridgeArgumentError(
            f"multi-input manifest could not be read as UTF-8: {path}: {exc}"
        ) from exc
    try:
        payload = json.loads(
            document,
            parse_constant=_reject_non_json_manifest_number,
            object_pairs_hook=_reject_duplicate_manifest_fields,
        )
    except BridgeArgumentError:
        raise
    except json.JSONDecodeError as exc:
        raise BridgeArgumentError(
            "multi-input manifest is not valid JSON: "
            f"{path} (line {exc.lineno}, column {exc.colno})"
        ) from exc
    except (RecursionError, ValueError) as exc:
        raise BridgeArgumentError(
            f"multi-input manifest could not be decoded as JSON: {path}: {exc}"
        ) from exc

    return path, validate_multi_input_manifest(
        payload,
        base_directory=path.parent,
    )


def load_multi_input_manifest(manifest_path: str | Path) -> dict[str, object]:
    """Load a UTF-8 JSON manifest file and return its normalized schema."""

    return _load_multi_input_manifest(manifest_path)[1]


def build_parser() -> argparse.ArgumentParser:
    parser = BridgeArgumentParser(
        description="Qt JSON Lines bridge for Korean transcription"
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--input")
    input_group.add_argument("--multi-input-manifest", metavar="JSON_PATH")
    parser.add_argument("--device", type=_device, default="AUTO")
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--model-label", default="")
    parser.add_argument("--beams", type=int, default=1)
    parser.add_argument("--language", default="<|ko|>")
    parser.add_argument("--window-seconds", type=float, default=DEFAULT_WINDOW_SECONDS)
    parser.add_argument("--overlap-seconds", type=float, default=DEFAULT_OVERLAP_SECONDS)
    parser.add_argument("--output-dir")
    parser.add_argument("--output-suffix", default="")
    parser.add_argument("--hotwords-file", default=str(DEFAULT_HOTWORDS_FILE))
    parser.add_argument("--initial-prompt-file", default=str(DEFAULT_INITIAL_PROMPT_FILE))
    parser.add_argument(
        "--skip-silence",
        action="store_true",
        help="Detect voiced regions and send only those regions to Whisper.",
    )
    parser.add_argument("--silence-threshold-db", type=float, default=-45.0)
    parser.add_argument("--silence-min-speech-duration", type=float, default=0.3)
    parser.add_argument("--silence-min-duration", type=float, default=0.5)
    parser.add_argument("--silence-padding-duration", type=float, default=0.2)
    parser.add_argument("--diarization", action="store_true")
    parser.add_argument("--num-speakers", type=_speaker_count, default=-1)
    parser.add_argument("--speaker-threshold", type=float, default=0.5)
    parser.add_argument("--diarization-min-duration-on", type=float, default=0.3)
    parser.add_argument("--diarization-min-duration-off", type=float, default=0.5)
    parser.add_argument(
        "--diarization-segmentation-model",
        default=str(DEFAULT_SEGMENTATION_MODEL),
    )
    parser.add_argument(
        "--diarization-embedding-model",
        default=str(DEFAULT_EMBEDDING_MODEL),
    )
    parser.add_argument(
        "--diarization-device",
        type=_diarization_device,
        default="NPU",
    )
    parser.add_argument("--diarization-no-fallback", action="store_true")
    return parser


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    args = build_parser().parse_args(argv)
    args.multi_input_manifest_path = None
    args.multi_input_manifest_data = None
    if args.multi_input_manifest is not None:
        (
            args.multi_input_manifest_path,
            args.multi_input_manifest_data,
        ) = _load_multi_input_manifest(args.multi_input_manifest)
    if args.beams < 1:
        raise BridgeArgumentError("--beams must be at least 1")
    if not math.isfinite(args.window_seconds) or args.window_seconds < 30.0:
        raise BridgeArgumentError("--window-seconds must be a finite number at least 30")
    if (
        not math.isfinite(args.overlap_seconds)
        or args.overlap_seconds < 0.0
        or args.overlap_seconds >= args.window_seconds / 2.0
    ):
        raise BridgeArgumentError(
            "--overlap-seconds must be finite, non-negative, and less than half the window"
        )
    if (
        not math.isfinite(args.speaker_threshold)
        or not 0.0 < args.speaker_threshold <= 1.0
    ):
        raise BridgeArgumentError("--speaker-threshold must be in (0, 1]")
    for option, value in (
        ("--silence-min-speech-duration", args.silence_min_speech_duration),
        ("--silence-min-duration", args.silence_min_duration),
        ("--silence-padding-duration", args.silence_padding_duration),
        ("--diarization-min-duration-on", args.diarization_min_duration_on),
        ("--diarization-min-duration-off", args.diarization_min_duration_off),
    ):
        if not math.isfinite(value) or value < 0.0:
            raise BridgeArgumentError(f"{option} must be a finite number at least 0")
    if (
        not math.isfinite(args.silence_threshold_db)
        or args.silence_threshold_db < -100.0
        or args.silence_threshold_db >= 0.0
    ):
        raise BridgeArgumentError("--silence-threshold-db must be finite, at least -100, and below 0")
    return args


def _failure_message(prefix: str, exc: BaseException) -> str:
    detail = str(exc).strip()
    return f"{prefix}: {detail}" if detail else prefix


def _call(prefix: str, operation: Callable):
    try:
        return operation()
    except (BridgeFailure, ModuleNotFoundError):
        raise
    except Exception as exc:
        raise BridgeFailure(_failure_message(prefix, exc)) from exc


def _matches_device(device: object, requested: str) -> bool:
    value = str(device).upper()
    return value == requested or value.startswith(requested + ".")


def resolve_device(engine, requested: str) -> str:
    if requested != "AUTO":
        engine.ensure_device(requested)
        return requested

    devices = engine.available_devices()
    for candidate in ("NPU", "GPU", "CPU"):
        if any(_matches_device(device, candidate) for device in devices):
            return candidate
    raise RuntimeError(f"No supported OpenVINO device was found. Devices: {devices}")


def npu_context_tokens_need_word_timestamps(
    selected_device: str,
    hotwords: str,
    initial_prompt: str,
) -> bool:
    """Return whether the NPU pipeline needs prompt-token decoder capacity."""

    return bool(hotwords.strip() or initial_prompt.strip()) and _matches_device(selected_device, "NPU")


def transcribe_speech_regions(
    engine,
    pipe,
    config,
    audio,
    regions: list[SpeechRegion],
    window_seconds: float,
    overlap_seconds: float,
    *,
    progress_callback: Callable[[float, float, int, int], None] | None = None,
):
    """Transcribe VAD regions while restoring every segment to source time."""

    sample_rate = engine.TARGET_SAMPLE_RATE
    total_duration = sum(region.sample_count for region in regions) / sample_rate
    completed_duration = 0.0
    segments = []

    for region_index, region in enumerate(regions, start=1):
        region_audio = audio[region.start_sample:region.end_sample]
        region_duration = len(region_audio) / sample_rate
        if region_duration <= 0.0:
            continue

        def region_progress(processed: float, _total: float, index: int, count: int) -> None:
            if progress_callback is None:
                return
            progress_callback(
                min(total_duration, completed_duration + min(region_duration, max(0.0, processed))),
                total_duration,
                index,
                count,
            )

        region_segments = engine.transcribe_windows(
            pipe,
            config,
            region_audio,
            region_duration,
            window_seconds,
            overlap_seconds,
            progress_callback=region_progress,
            console_progress=False,
        )
        offset = region.start_sample / sample_rate
        region_end = region.end_sample / sample_rate
        for segment in region_segments:
            segment.start = offset + float(segment.start)
            segment.end = min(region_end, offset + float(segment.end))
            segments.append(segment)
        completed_duration += region_duration

        if progress_callback is not None:
            progress_callback(completed_duration, total_duration, region_index, len(regions))

    segments.sort(key=lambda segment: (segment.start, segment.end))
    return segments


def _emit_transcription_progress(
    writer: EventWriter,
    processed: float,
    total: float,
) -> bool:
    """Emit reliable transcription progress without claiming job completion.

    A completed transcription still has optional diarization and result-file
    saving ahead of it.  Reserve 100% for the point where both result files
    have been successfully written.
    """
    processed = float(processed)
    total = float(total)
    if not math.isfinite(processed) or not math.isfinite(total):
        raise RuntimeError("transcription progress must be finite")

    if total <= 0.0:
        # A zero/unknown duration cannot yield a meaningful percentage.
        writer.progress(None)
        return False

    processed = min(total, max(0.0, processed))
    value = min(99, int(round(processed / total * 100.0)))
    writer.progress(value, processed, total)
    return processed >= total


def _load_diarize_function() -> Callable:
    return importlib.import_module("diarization").diarize_audio


@dataclass(frozen=True)
class _TranscriptionContext:
    """Settings both input modes resolve before any audio is processed."""

    model_dir: Path
    output_dir: Path
    hotwords: str
    initial_prompt: str
    device: str
    npu_context_tokens: bool


def _prepare_transcription(
    args: argparse.Namespace,
    engine,
    writer: EventWriter,
    *,
    default_output_dir: Path,
) -> _TranscriptionContext:
    model_dir = Path(args.model_dir).expanduser().resolve()
    if not model_dir.is_dir():
        raise BridgeFailure(f"Model directory not found: {model_dir}", exit_code=3)

    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else default_output_dir
    )

    hotwords = _call(
        "Hotwords could not be read",
        lambda: engine.read_optional_text(Path(args.hotwords_file).expanduser().resolve()),
    )
    initial_prompt = _call(
        "Initial prompt could not be read",
        lambda: engine.read_optional_text(
            Path(args.initial_prompt_file).expanduser().resolve()
        ),
    )
    device = _call(
        "OpenVINO device selection failed",
        lambda: resolve_device(engine, args.device),
    )
    npu_context_tokens = npu_context_tokens_need_word_timestamps(
        device,
        hotwords,
        initial_prompt,
    )
    if npu_context_tokens:
        if hotwords and initial_prompt:
            context_source = "핫워드와 초기 프롬프트"
        elif initial_prompt:
            context_source = "초기 프롬프트"
        else:
            context_source = "핫워드"
        writer.warning(
            f"{context_source}를 적용하기 위해 NPU 단어 타임스탬프 모드를 사용합니다. "
            "최초 모델 준비 시간이 길어질 수 있습니다."
        )
    return _TranscriptionContext(
        model_dir=model_dir,
        output_dir=output_dir,
        hotwords=hotwords,
        initial_prompt=initial_prompt,
        device=device,
        npu_context_tokens=npu_context_tokens,
    )


def _transcribe_and_save(
    args: argparse.Namespace,
    engine,
    writer: EventWriter,
    context: _TranscriptionContext,
    audio,
    result_input_path: Path,
    *,
    diarize_function: Callable | None = None,
    artifacts: dict[str, str] | None = None,
) -> int:
    """Run the Whisper, diarization and saving stages shared by both modes.

    ``audio`` is 16 kHz mono and starts at the time origin of the result, so
    every segment, TXT and SRT timestamp is measured on that timeline.
    ``result_input_path`` names the TXT/SRT files exactly as the existing
    engine names them for a user-selected file.  ``artifacts`` are the extra
    multi-input files reported with ``completed``.
    """

    duration = len(audio) / engine.TARGET_SAMPLE_RATE

    speech_regions: list[SpeechRegion] | None = None
    if getattr(args, "skip_silence", False):
        # Keep runtime dependencies inside main's protocol error boundary.
        from vad import detect_speech_regions

        writer.state("detecting_speech")
        writer.progress(None)
        speech_regions = _call(
            "Speech detection failed",
            lambda: detect_speech_regions(
                audio,
                engine.TARGET_SAMPLE_RATE,
                threshold_db=args.silence_threshold_db,
                min_speech_duration=args.silence_min_speech_duration,
                min_silence_duration=args.silence_min_duration,
                padding_duration=args.silence_padding_duration,
            ),
        )
        if not speech_regions:
            writer.warning("음성 구간이 감지되지 않아 Whisper 전사와 화자 분리를 건너뜁니다.")

    segments = []
    transcription_complete = False
    pipe = None
    config = None
    if speech_regions is None or speech_regions:
        writer.state("loading_model")
        writer.progress(None)
        pipe = _call(
            "Whisper model could not be loaded",
            lambda: engine.load_whisper_pipeline(
                context.model_dir,
                context.device,
                args.model_label,
                word_timestamps=context.npu_context_tokens,
            ),
        )
        initial_prompt_plan = _call(
            "Initial prompt validation failed",
            lambda: engine.prepare_initial_prompt(pipe, context.initial_prompt),
        )
        if initial_prompt_plan.warning:
            writer.warning(initial_prompt_plan.warning)
        config = _call(
            "Whisper generation configuration failed",
            lambda: engine.configure_generation(
                pipe,
                args.language,
                args.beams,
                context.hotwords,
                initial_prompt=initial_prompt_plan.value,
                word_timestamps=context.npu_context_tokens,
            ),
        )

        writer.state("transcribing")

        def progress_callback(processed: float, total: float, _index: int, _count: int) -> None:
            nonlocal transcription_complete
            transcription_complete = _emit_transcription_progress(writer, processed, total)

        if speech_regions is None:
            segments = _call(
                "Transcription failed",
                lambda: engine.transcribe_windows(
                    pipe,
                    config,
                    audio,
                    duration,
                    args.window_seconds,
                    args.overlap_seconds,
                    progress_callback=progress_callback,
                    console_progress=False,
                ),
            )
            transcription_total = duration
        else:
            segments = _call(
                "Transcription failed",
                lambda: transcribe_speech_regions(
                    engine,
                    pipe,
                    config,
                    audio,
                    speech_regions,
                    args.window_seconds,
                    args.overlap_seconds,
                    progress_callback=progress_callback,
                ),
            )
            transcription_total = sum(region.sample_count for region in speech_regions) / engine.TARGET_SAMPLE_RATE
        if not transcription_complete:
            _emit_transcription_progress(writer, transcription_total, transcription_total)

    if args.diarization and (speech_regions is None or speech_regions):
        # Match the existing CLI's resource policy: release Whisper before the
        # segmentation model is compiled on memory-constrained accelerators.
        del pipe
        del config
        gc.collect()

        writer.state("diarization")
        writer.progress(None)

        def perform_diarization():
            function = diarize_function or _load_diarize_function()
            turns = function(
                audio,
                Path(args.diarization_segmentation_model).expanduser().resolve(),
                Path(args.diarization_embedding_model).expanduser().resolve(),
                num_speakers=args.num_speakers,
                cluster_threshold=args.speaker_threshold,
                min_duration_on=args.diarization_min_duration_on,
                min_duration_off=args.diarization_min_duration_off,
                device=args.diarization_device,
                fallback_to_cpu=not args.diarization_no_fallback,
            )
            engine.assign_speakers(segments, turns)

        _call("Speaker diarization failed", perform_diarization)

    # Segment events are emitted only after optional diarization has finished.
    # This is the bridge contract: provisional STT segments are never sent and
    # then resent with speaker labels, so every finalized segment appears at
    # most once in the Qt transcript.
    for segment in segments:
        text = str(segment.text).strip()
        if not text:
            continue
        start = float(segment.start)
        end = float(segment.end)
        if (
            not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0.0
            or end < start
        ):
            raise BridgeFailure("Transcription produced an invalid segment timestamp")
        speaker = None
        if segment.speaker is not None:
            if (
                isinstance(segment.speaker, bool)
                or not isinstance(segment.speaker, Integral)
                or segment.speaker < 0
            ):
                raise BridgeFailure("Transcription produced an invalid speaker number")
            speaker = int(segment.speaker) + 1
        writer.emit(
            "segment",
            start=start,
            end=end,
            speaker=speaker,
            text=text,
        )

    writer.state("saving_result")
    writer.progress(None)

    def save_result():
        context.output_dir.mkdir(parents=True, exist_ok=True)
        paths = engine.save_outputs(
            result_input_path,
            segments,
            duration,
            args.output_suffix,
            diarized=args.diarization,
            output_dir=context.output_dir,
        )
        txt_path, srt_path = (Path(path).resolve() for path in paths)
        if not txt_path.is_file() or not srt_path.is_file():
            raise RuntimeError("the engine did not create both result files")
        return txt_path, srt_path

    txt_path, srt_path = _call("Result files could not be saved", save_result)
    if duration > 0.0:
        writer.progress(100, duration, duration)
    else:
        writer.progress(100)
    writer.completed(txt_path, srt_path, **(artifacts or {}))
    return 0


def _run_single_input(
    args: argparse.Namespace,
    engine,
    writer: EventWriter,
    *,
    diarize_function: Callable | None = None,
) -> int:
    writer.state("preparing")
    writer.progress(None)

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.is_file():
        raise BridgeFailure(f"Input file not found: {input_path}", exit_code=2)

    context = _prepare_transcription(
        args,
        engine,
        writer,
        default_output_dir=input_path.parent,
    )

    writer.state("decoding_audio")
    writer.progress(None)
    audio = _call(
        "Audio decoding failed",
        lambda: engine.decode_audio_16k_mono(input_path),
    )
    return _transcribe_and_save(
        args,
        engine,
        writer,
        context,
        audio,
        input_path,
        diarize_function=diarize_function,
    )


def _input_label(labels: list[str], input_index: int | None) -> str | None:
    if input_index is None or not 0 <= input_index < len(labels):
        return None
    return labels[input_index]


def _preparation_failure(exc, labels: list[str]) -> BridgeFailure:
    details = []
    for failure in exc.failures:
        label = _input_label(labels, failure.input_index)
        details.append(f"{label}: {failure.message}" if label else failure.message)
    message = "Multi-input audio could not be prepared."
    if details:
        message += " " + " ".join(details)
    return BridgeFailure(
        message,
        diagnostic=(
            "Multi-input preparation diagnostics:\n"
            + json.dumps(exc.to_dict(), ensure_ascii=False, indent=2)
        ),
    )


def _unresolved_alignment_failure(alignment, labels: list[str]) -> BridgeFailure:
    unresolved = sorted(alignment.failures, key=lambda item: item.input_index)
    details = []
    for item in unresolved:
        label = _input_label(labels, item.input_index) or f"input {item.input_index}"
        reason = item.warning_reason or item.failure_reason
        details.append(f"{label} ({reason})" if reason else label)
    return BridgeFailure(
        "Time alignment is unresolved for "
        + "; ".join(details)
        + ". Review the alignment and enter a manual offset for these inputs "
        "before transcribing.",
        diagnostic=(
            "Unresolved multi-input alignment:\n"
            + json.dumps(
                [item.to_dict() for item in unresolved],
                ensure_ascii=False,
                indent=2,
            )
        ),
    )


def _run_multi_audio_preprocessing(
    multimic,
    manifest: dict[str, object],
    output_dir: Path,
    writer: EventWriter,
):
    """Report each pre-processing stage and map its failures for the user.

    Expected outcomes (unreadable inputs, unresolved alignment, an override
    beyond the reference recording) become concise ``error`` messages with
    the structured detail kept for stderr; anything else is reported with the
    stage it interrupted.
    """

    inputs = manifest["inputs"]
    labels = [item["label"] for item in inputs]
    alignment = manifest["alignment"]
    selection = manifest["selection"]
    current_stage: str | None = None

    def report_stage(stage: str) -> None:
        nonlocal current_stage
        current_stage = stage
        writer.state(stage)
        writer.progress(None)

    try:
        return multimic.run_multi_audio_preprocessing(
            [item["path"] for item in inputs],
            output_dir,
            reference_index=manifest["reference_index"],
            manual_offsets_ms=alignment["manual_offsets_ms"],
            window_seconds=selection["window_seconds"],
            hop_seconds=selection["hop_seconds"],
            overrides=selection.get("overrides", []),
            input_labels=labels,
            on_stage=report_stage,
        )
    except multimic.MultiAudioPreparationError as exc:
        raise _preparation_failure(exc, labels) from exc
    except multimic.UnresolvedAlignmentError as exc:
        raise _unresolved_alignment_failure(exc.alignment, labels) from exc
    except multimic.SelectionOverrideError as exc:
        raise BridgeFailure(
            f"Invalid bridge arguments: multi-input manifest {exc}",
            exit_code=2,
        ) from exc
    except (BridgeFailure, ModuleNotFoundError):
        raise
    except Exception as exc:
        prefix = MULTI_INPUT_STAGE_FAILURES.get(
            current_stage,
            "Multi-input pre-processing failed",
        )
        raise BridgeFailure(_failure_message(prefix, exc)) from exc


def _warn_about_channel_plan(writer: EventWriter, preprocessing) -> None:
    selection_warnings = preprocessing.plan.selection.warnings
    if selection_warnings:
        codes = ", ".join(sorted({item.code for item in selection_warnings}))
        writer.warning(
            f"채널 선택 경고 {len(selection_warnings)}건({codes})이 있습니다. "
            "해당 구간은 전사 품질이 낮을 수 있으며 상세 내용은 "
            f"{Path(preprocessing.outputs.selection_file).name}에 기록했습니다."
        )
    merged_audio = preprocessing.outputs.merged_audio
    if merged_audio.padding_sample_count > 0:
        seconds = merged_audio.padding_sample_count / merged_audio.sample_rate
        writer.warning(
            f"선택된 입력에 녹음이 없는 {seconds:.1f}초를 무음으로 채워 "
            "기준 녹음의 시간축을 유지했습니다."
        )


def _run_multi_input(
    args: argparse.Namespace,
    engine,
    writer: EventWriter,
    *,
    diarize_function: Callable | None = None,
    multimic_module=None,
) -> int:
    writer.state("preparing")
    writer.progress(None)

    manifest = args.multi_input_manifest_data
    reference_path = Path(manifest["inputs"][manifest["reference_index"]]["path"])
    # Model, device and prompt problems are reported before the comparatively
    # long alignment and analysis stages run.
    context = _prepare_transcription(
        args,
        engine,
        writer,
        default_output_dir=reference_path.parent,
    )

    multimic = multimic_module or importlib.import_module("multimic")
    preprocessing = _run_multi_audio_preprocessing(
        multimic,
        manifest,
        context.output_dir,
        writer,
    )
    _warn_about_channel_plan(writer, preprocessing)

    # The merged WAV is transcribed exactly like a user-selected file.  It
    # starts at the reference recording's first sample, so Whisper and
    # diarization timestamps are reference-timeline timestamps.
    outputs = preprocessing.outputs
    merged_audio_file = Path(outputs.merged_audio_file)
    audio = _call(
        "Merged audio could not be decoded",
        lambda: engine.decode_audio_16k_mono(merged_audio_file),
    )
    if len(audio) != outputs.merged_audio.sample_count:
        raise BridgeFailure(
            "Merged audio does not match the reference recording timeline: "
            f"expected {outputs.merged_audio.sample_count} samples, "
            f"decoded {len(audio)}."
        )
    return _transcribe_and_save(
        args,
        engine,
        writer,
        context,
        audio,
        merged_audio_file,
        diarize_function=diarize_function,
        artifacts=outputs.to_dict(),
    )


def run_bridge(
    args: argparse.Namespace,
    engine,
    writer: EventWriter,
    *,
    diarize_function: Callable | None = None,
    multimic_module=None,
) -> int:
    """Run one job; both input modes end with the same ``completed`` contract."""

    if getattr(args, "multi_input_manifest", None) is not None:
        return _run_multi_input(
            args,
            engine,
            writer,
            diarize_function=diarize_function,
            multimic_module=multimic_module,
        )
    return _run_single_input(
        args,
        engine,
        writer,
        diarize_function=diarize_function,
    )

def _write_diagnostic(diagnostic: TextIO, exc: BaseException) -> None:
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=diagnostic)
    diagnostic.flush()


def _finish_stream(writer: EventWriter, exit_code: int) -> int:
    """Return an exit code that agrees with the run's single terminal event."""

    if writer.terminal is None:
        writer.error("Backend finished without reporting a result.")
    if writer.terminal == "completed":
        return 0
    return exit_code if exit_code != 0 else 1


def _report_failure(writer: EventWriter, message: str, exit_code: int) -> int:
    # A failure after the terminal event must not append a second one: the
    # first terminal event already told Qt how this run ended.
    if writer.terminal is None:
        writer.error(message)
    return _finish_stream(writer, exit_code)


def main(
    argv: list[str] | None = None,
    *,
    engine_module=None,
    diarize_function: Callable | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output = stdout if stdout is not None else sys.stdout
    diagnostic = stderr if stderr is not None else sys.stderr
    writer = EventWriter(output)

    try:
        args = parse_arguments(argv)
    except BridgeArgumentError as exc:
        writer.error(f"Invalid bridge arguments: {exc}")
        diagnostic.write(f"Argument error: {exc}\n")
        diagnostic.flush()
        return 2

    try:
        with redirect_stdout(diagnostic):
            if engine_module is None:
                engine_module = importlib.import_module("transcribe_npu")
            exit_code = run_bridge(
                args,
                engine_module,
                writer,
                diarize_function=diarize_function,
            )
    except KeyboardInterrupt as exc:
        exit_code = _report_failure(writer, "Transcription was cancelled.", 130)
        _write_diagnostic(diagnostic, exc)
        return exit_code
    except BridgeFailure as exc:
        exit_code = _report_failure(writer, str(exc), exc.exit_code)
        if exc.diagnostic:
            diagnostic.write(exc.diagnostic.rstrip("\n") + "\n")
        _write_diagnostic(diagnostic, exc)
        return exit_code
    except ModuleNotFoundError as exc:
        name = exc.name or "unknown"
        exit_code = _report_failure(
            writer,
            (
                f"Required Python module is not installed: {name}. "
                f"Current interpreter: {sys.executable}. "
                f"Run {APP_DIR / 'setup.bat'} to install the runtime packages, "
                "then select the engine virtual environment in Settings: "
                f"{APP_DIR / '.venv' / 'Scripts' / 'python.exe'}."
            ),
            1,
        )
        _write_diagnostic(diagnostic, exc)
        return exit_code
    except Exception as exc:
        exit_code = _report_failure(
            writer,
            _failure_message("Backend processing failed", exc),
            1,
        )
        _write_diagnostic(diagnostic, exc)
        return exit_code
    return _finish_stream(writer, exit_code)


def _configure_standard_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="strict", newline="\n", write_through=True)


def _isolate_protocol_stdout() -> TextIO:
    """Reserve the process stdout for protocol events only.

    ``redirect_stdout`` in ``main`` only redirects Python-level writes. Native
    libraries (OpenVINO, FFmpeg) and child processes write to file descriptor 1
    directly, so the protocol stream gets a private duplicate of that
    descriptor and descriptor 1 is pointed at stderr for everything else.
    """

    try:
        stdout_descriptor = sys.stdout.fileno()
        stderr_descriptor = sys.stderr.fileno()
    except (AttributeError, OSError, ValueError):
        return sys.stdout
    sys.stdout.flush()
    protocol_descriptor = os.dup(stdout_descriptor)
    os.dup2(stderr_descriptor, stdout_descriptor)
    return open(
        protocol_descriptor,
        "w",
        encoding="utf-8",
        errors="strict",
        newline="\n",
    )


if __name__ == "__main__":
    _configure_standard_streams()
    raise SystemExit(main(stdout=_isolate_protocol_stdout()))
