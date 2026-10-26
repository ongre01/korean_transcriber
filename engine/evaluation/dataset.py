"""Evaluation dataset files: recordings, permission record and shared settings.

A dataset file names the recordings of one meeting, the permission under
which they are evaluated, an optional human reference transcript and the one
set of bridge settings every condition is run with.  Relative paths are
resolved from the dataset file's directory, like the bridge resolves a
multi-input manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import sys
from typing import Mapping


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import backend_bridge  # noqa: E402  (needs ENGINE_DIR on sys.path)


DATASET_VERSION = 1

# Bridge defaults as the Qt GUI sends them (``TranscribeOptions.h``), so an
# evaluation that names no setting measures what users run.  Only the model
# directory has no default because it selects the precision being measured.
SETTING_DEFAULTS: dict[str, object] = {
    "device": "AUTO",
    "model_label": "",
    "beams": 1,
    "language": "<|ko|>",
    "window_seconds": backend_bridge.DEFAULT_WINDOW_SECONDS,
    "overlap_seconds": backend_bridge.DEFAULT_OVERLAP_SECONDS,
    "hotwords_file": str(backend_bridge.DEFAULT_HOTWORDS_FILE),
    "initial_prompt_file": str(backend_bridge.DEFAULT_INITIAL_PROMPT_FILE),
    "skip_silence": True,
    "silence_threshold_db": -45.0,
    "silence_min_speech_duration": 0.3,
    "silence_min_duration": 0.5,
    "silence_padding_duration": 0.2,
    "diarization": False,
    "num_speakers": "auto",
    "speaker_threshold": 0.5,
    "diarization_min_duration_on": 0.3,
    "diarization_min_duration_off": 0.5,
    "diarization_segmentation_model": str(backend_bridge.DEFAULT_SEGMENTATION_MODEL),
    "diarization_embedding_model": str(backend_bridge.DEFAULT_EMBEDDING_MODEL),
    "diarization_device": "NPU",
    "diarization_no_fallback": False,
}
FLAG_SETTINGS = frozenset({"skip_silence", "diarization", "diarization_no_fallback"})
PATH_SETTINGS = frozenset(
    {
        "model_dir",
        "hotwords_file",
        "initial_prompt_file",
        "diarization_segmentation_model",
        "diarization_embedding_model",
    }
)
SETTING_ORDER = ("model_dir", *SETTING_DEFAULTS)


class DatasetError(ValueError):
    """The dataset file is missing, malformed or names unusable files."""


@dataclass(frozen=True)
class EvaluationInput:
    path: Path
    label: str

    def to_dict(self) -> dict[str, str]:
        return {"path": str(self.path), "label": self.label}


@dataclass(frozen=True)
class EvaluationDataset:
    """One meeting recorded on several devices, ready to be evaluated."""

    path: Path | None
    dataset_id: str
    description: str
    authorization: dict[str, object]
    inputs: tuple[EvaluationInput, ...]
    reference_index: int
    reference_transcript: Path | None
    manual_offsets_ms: dict[int, int]
    window_seconds: float
    hop_seconds: float
    settings: dict[str, object]

    @property
    def authorization_status(self) -> str:
        approved_by = self.authorization.get("approved_by")
        return "approved" if isinstance(approved_by, str) and approved_by.strip() else "unverified"

    def bridge_arguments(self) -> list[str]:
        """Return the settings part of every condition's bridge command."""

        return settings_to_arguments(self.settings)

    def multi_input_manifest(self) -> dict[str, object]:
        return {
            "version": backend_bridge.MULTI_INPUT_MANIFEST_VERSION,
            "inputs": [item.to_dict() for item in self.inputs],
            "reference_index": self.reference_index,
            "alignment": {
                "mode": backend_bridge.MULTI_INPUT_ALIGNMENT_MODE,
                "manual_offsets_ms": {
                    str(index): offset
                    for index, offset in sorted(self.manual_offsets_ms.items())
                },
            },
            "selection": {
                "mode": backend_bridge.MULTI_INPUT_SELECTION_MODE,
                "window_seconds": self.window_seconds,
                "hop_seconds": self.hop_seconds,
            },
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "version": DATASET_VERSION,
            "dataset_file": None if self.path is None else str(self.path),
            "dataset_id": self.dataset_id,
            "description": self.description,
            "authorization": dict(self.authorization),
            "authorization_status": self.authorization_status,
            "inputs": [item.to_dict() for item in self.inputs],
            "reference_index": self.reference_index,
            "reference_transcript": (
                None if self.reference_transcript is None else str(self.reference_transcript)
            ),
            "alignment": {
                "manual_offsets_ms": {
                    str(index): offset
                    for index, offset in sorted(self.manual_offsets_ms.items())
                }
            },
            "selection": {
                "window_seconds": self.window_seconds,
                "hop_seconds": self.hop_seconds,
            },
            "settings": dict(self.settings),
        }


def settings_to_arguments(settings: Mapping[str, object]) -> list[str]:
    arguments: list[str] = []
    for name in SETTING_ORDER:
        value = settings[name]
        option = "--" + name.replace("_", "-")
        if name in FLAG_SETTINGS:
            if value:
                arguments.append(option)
            continue
        if isinstance(value, float):
            text = repr(value)
        else:
            text = str(value)
        arguments.extend((option, text))
    return arguments


def _object(value: object, location: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise DatasetError(f"{location} must be a JSON object")
    return value


def _reject_unknown(value: Mapping[str, object], allowed: set[str], location: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise DatasetError(f"{location} has unknown fields: {', '.join(unknown)}")


def _text(value: object, location: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise DatasetError(f"{location} must be a non-empty string")
    return value.strip()


def _number(value: object, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DatasetError(f"{location} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise DatasetError(f"{location} must be finite")
    return result


def _resolve(base: Path, value: object, location: str) -> Path:
    return (base / Path(_text(value, location)).expanduser()).resolve()


def _settings(raw: object, base: Path) -> dict[str, object]:
    values = _object(raw, "settings")
    _reject_unknown(values, set(SETTING_ORDER), "settings")
    if "model_dir" not in values:
        raise DatasetError("settings.model_dir is required")

    settings: dict[str, object] = {}
    for name in SETTING_ORDER:
        value = values.get(name, SETTING_DEFAULTS.get(name))
        location = f"settings.{name}"
        if name in PATH_SETTINGS:
            settings[name] = str(_resolve(base, value, location))
        elif name in FLAG_SETTINGS:
            if not isinstance(value, bool):
                raise DatasetError(f"{location} must be true or false")
            settings[name] = value
        elif name == "beams":
            if isinstance(value, bool) or not isinstance(value, int):
                raise DatasetError(f"{location} must be an integer")
            settings[name] = value
        elif name == "num_speakers":
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                raise DatasetError(f"{location} must be \"auto\" or an integer")
            settings[name] = value
        elif isinstance(SETTING_DEFAULTS[name], float):
            settings[name] = _number(value, location)
        else:
            settings[name] = _text(value, location, allow_empty=name == "model_label")

    # The bridge's own parser is the authority on ranges and choices, so a
    # setting it would reject fails here instead of after a long first run.
    try:
        backend_bridge.parse_arguments(["--input", "dataset-check", *settings_to_arguments(settings)])
    except backend_bridge.BridgeArgumentError as exc:
        raise DatasetError(f"settings are rejected by the bridge: {exc}") from exc
    return settings


def parse_dataset(
    payload: object,
    *,
    base_directory: Path,
    path: Path | None = None,
    require_files: bool = True,
) -> EvaluationDataset:
    root = _object(payload, "root")
    _reject_unknown(
        root,
        {
            "version",
            "dataset_id",
            "description",
            "authorization",
            "inputs",
            "reference_index",
            "reference_transcript",
            "alignment",
            "selection",
            "settings",
        },
        "root",
    )
    version = root.get("version")
    if isinstance(version, bool) or version != DATASET_VERSION:
        raise DatasetError(f"version must be the integer {DATASET_VERSION}")
    dataset_id = _text(root.get("dataset_id"), "dataset_id")
    if any(character in dataset_id for character in '\\/:*?"<>|') or dataset_id in {".", ".."}:
        raise DatasetError("dataset_id must be usable as a folder name")
    description = root.get("description", "")
    if not isinstance(description, str):
        raise DatasetError("description must be a string")

    authorization = _object(root.get("authorization"), "authorization")
    _text(authorization.get("basis"), "authorization.basis")
    approved_by = authorization.get("approved_by")
    if approved_by is not None and not isinstance(approved_by, str):
        raise DatasetError("authorization.approved_by must be a string or null")

    raw_inputs = root.get("inputs")
    if not isinstance(raw_inputs, list) or len(raw_inputs) < 2:
        raise DatasetError("inputs must list at least two recordings of the same meeting")
    inputs = []
    for index, raw_input in enumerate(raw_inputs):
        item = _object(raw_input, f"inputs[{index}]")
        _reject_unknown(item, {"path", "label"}, f"inputs[{index}]")
        input_path = _resolve(base_directory, item.get("path"), f"inputs[{index}].path")
        if require_files and not input_path.is_file():
            raise DatasetError(f"inputs[{index}].path does not exist: {input_path}")
        inputs.append(EvaluationInput(input_path, _text(item.get("label"), f"inputs[{index}].label")))

    reference_index = root.get("reference_index", 0)
    if (
        isinstance(reference_index, bool)
        or not isinstance(reference_index, int)
        or not 0 <= reference_index < len(inputs)
    ):
        raise DatasetError("reference_index must identify an input")

    reference_transcript = None
    if root.get("reference_transcript") is not None:
        reference_transcript = _resolve(
            base_directory, root["reference_transcript"], "reference_transcript"
        )
        if require_files and not reference_transcript.is_file():
            raise DatasetError(f"reference_transcript does not exist: {reference_transcript}")

    alignment = _object(root.get("alignment", {}), "alignment")
    _reject_unknown(alignment, {"manual_offsets_ms"}, "alignment")
    manual_offsets: dict[int, int] = {}
    for raw_index, raw_offset in _object(
        alignment.get("manual_offsets_ms", {}), "alignment.manual_offsets_ms"
    ).items():
        if not raw_index.isdigit() or int(raw_index) >= len(inputs):
            raise DatasetError(f"alignment.manual_offsets_ms index {raw_index!r} is not an input")
        if isinstance(raw_offset, bool) or not isinstance(raw_offset, int):
            raise DatasetError("alignment.manual_offsets_ms values must be integer milliseconds")
        manual_offsets[int(raw_index)] = raw_offset

    selection = _object(root.get("selection", {}), "selection")
    _reject_unknown(selection, {"window_seconds", "hop_seconds"}, "selection")
    window_seconds = _number(selection.get("window_seconds", 3.0), "selection.window_seconds")
    hop_seconds = _number(selection.get("hop_seconds", 1.0), "selection.hop_seconds")

    dataset = EvaluationDataset(
        path=path,
        dataset_id=dataset_id,
        description=description.strip(),
        authorization=dict(authorization),
        inputs=tuple(inputs),
        reference_index=reference_index,
        reference_transcript=reference_transcript,
        manual_offsets_ms=manual_offsets,
        window_seconds=window_seconds,
        hop_seconds=hop_seconds,
        settings=_settings(root.get("settings"), base_directory),
    )
    if require_files:
        try:
            backend_bridge.validate_multi_input_manifest(
                dataset.multi_input_manifest(), base_directory=base_directory
            )
        except backend_bridge.BridgeArgumentError as exc:
            raise DatasetError(f"the multi-input manifest would be rejected: {exc}") from exc
    return dataset


def load_dataset(path: str | Path, *, require_files: bool = True) -> EvaluationDataset:
    dataset_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(dataset_path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise DatasetError(f"dataset file could not be read: {dataset_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise DatasetError(
            f"dataset file is not valid JSON: {dataset_path} (line {exc.lineno}, column {exc.colno})"
        ) from exc
    return parse_dataset(
        payload,
        base_directory=dataset_path.parent,
        path=dataset_path,
        require_files=require_files,
    )
