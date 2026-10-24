#!/usr/bin/env python
"""One-shot Qt bridge for offline multi-microphone quality analysis.

The MM-DEV-011 dialog shows, interval by interval, which microphone the
selection stage prefers and why, and lets the user force another one.  This
command runs the already implemented stages in a cancellable ``QProcess``:
preparation, alignment with the user's reviewed manual offsets, speech and
quality analysis, and channel selection including the manifest's manual
overrides.  It never renders audio and never writes to a source file.
:func:`plan_channel_selection` exposes the same orchestration so a later
rendering/transcription stage uses the plan the user reviewed.

stdout is reserved for exactly one strict JSON object.  Diagnostic output and
tracebacks are written to stderr.  Inputs that are still unaligned after the
manual offsets are applied are reported as an ``alignment`` failure instead
of being silently excluded from the analysis.
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
from contextlib import redirect_stdout
from dataclasses import dataclass
import importlib
import json
from pathlib import Path
import sys
import traceback
from typing import Callable, TextIO


RESPONSE_VERSION = 1
# A selection segment can span most of a recording when one microphone stays
# best, so review rows cut the final plan on a fixed reference-timeline grid.
# A fixed grid keeps row bounds stable across re-analysis, which lets manual
# overrides made on rows line up with the rows of the next result.
REVIEW_ROW_SECONDS = 5.0
MINIMUM_REVIEW_ROW_SECONDS = 1.0


class QualityContractError(ValueError):
    """A caller-controlled request or API-contract error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class UnresolvedAlignmentError(ValueError):
    """Some inputs have neither an automatic alignment nor a manual offset."""

    def __init__(self, alignment) -> None:
        super().__init__("multi-audio alignment is unresolved")
        self.alignment = alignment


@dataclass(frozen=True)
class ChannelSelectionPlan:
    """Everything derived from one normalized manifest up to channel selection.

    ``selection`` is the final plan with the manifest's manual overrides and
    ``automatic_selection`` the plan without them.  Rendering ``selection``
    with ``multimic.write_multi_audio_outputs`` produces exactly the audio the
    reviewed plan describes.
    """

    sources: tuple[dict[str, object], ...]
    manual_offsets: dict[int, int]
    window_seconds: float
    hop_seconds: float
    prepared: object
    alignment: object
    quality: object
    automatic_selection: object
    selection: object


class QualityArgumentParser(argparse.ArgumentParser):
    """Return parser failures through the JSON response contract."""

    def error(self, message: str) -> None:
        raise QualityContractError("invalid_arguments", message)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        detail = message.strip() if message else "help requested"
        raise QualityContractError("invalid_arguments", detail)


def build_parser() -> argparse.ArgumentParser:
    parser = QualityArgumentParser(
        description="Analyze multi-microphone channel quality for the Qt frontend",
        add_help=False,
    )
    parser.add_argument("--multi-input-manifest", required=True, metavar="JSON_PATH")
    return parser


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def _default_manifest_loader(manifest_path: str | Path) -> dict[str, object]:
    bridge = importlib.import_module("backend_bridge")
    try:
        return bridge.load_multi_input_manifest(manifest_path)
    except bridge.BridgeArgumentError as exc:
        raise QualityContractError("invalid_manifest", str(exc)) from exc


def _default_multimic_module():
    return importlib.import_module("multimic")


def _strict_payload_text(payload: dict[str, object]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def _write_diagnostic(stream: TextIO, exc: BaseException) -> None:
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=stream)
    stream.flush()


def _serialized(values: object, description: str) -> list[dict[str, object]]:
    try:
        items = tuple(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise QualityContractError(
            "invalid_analysis_result",
            f"{description} must be iterable.",
        ) from exc

    result: list[dict[str, object]] = []
    for item in items:
        to_dict = getattr(item, "to_dict", None)
        if not callable(to_dict):
            raise QualityContractError(
                "invalid_analysis_result",
                f"{description} did not provide to_dict().",
            )
        payload = to_dict()
        if not isinstance(payload, dict):
            raise QualityContractError(
                "invalid_analysis_result",
                f"{description} did not serialize to an object.",
            )
        result.append(payload)
    return result


def _preparation_error_payload(exc: BaseException) -> dict[str, object]:
    detail = str(exc).strip()
    return {
        "version": RESPONSE_VERSION,
        "ok": False,
        "kind": "preparation",
        "code": "preparation_failed",
        "message": detail or "Multi-audio input preparation failed.",
        "errors": _serialized(
            getattr(exc, "failures", ()),
            "A preparation failure",
        ),
        "warnings": _serialized(
            getattr(exc, "warnings", ()),
            "A preparation warning",
        ),
    }


def _unresolved_alignment_payload(alignment) -> dict[str, object]:
    errors = []
    for item in sorted(alignment.failures, key=lambda value: value.input_index):
        code = item.failure_code or item.warning_code or "alignment_failed"
        message = (
            item.warning_reason
            or item.failure_reason
            or "The input is not aligned to the reference recording."
        )
        errors.append(
            {
                "input_index": item.input_index,
                "status": item.status,
                "code": code,
                "message": message,
            }
        )
    return {
        "version": RESPONSE_VERSION,
        "ok": False,
        "kind": "alignment",
        "code": "alignment_unresolved",
        "message": (
            "Every used input needs a successful automatic alignment or a "
            "manual offset before its quality can be analyzed."
        ),
        "errors": errors,
        "warnings": [],
    }


def _manifest_settings(
    manifest: dict[str, object],
) -> tuple[
    list[str],
    list[dict[str, object]],
    int,
    dict[int, int],
    float,
    float,
    list[dict[str, int]],
]:
    try:
        manifest_inputs = manifest["inputs"]
        reference_index = manifest["reference_index"]
        manual_offsets = manifest["alignment"]["manual_offsets_ms"]  # type: ignore[index]
        selection = manifest["selection"]
        window_seconds = selection["window_seconds"]  # type: ignore[index]
        hop_seconds = selection["hop_seconds"]  # type: ignore[index]
        overrides = selection.get("overrides", [])  # type: ignore[union-attr]
    except (AttributeError, KeyError, TypeError) as exc:
        raise QualityContractError(
            "invalid_manifest",
            "The normalized manifest is missing inputs, alignment, or selection settings.",
        ) from exc

    if not isinstance(manifest_inputs, list):
        raise QualityContractError(
            "invalid_manifest",
            "The normalized manifest inputs must be an array.",
        )
    if not isinstance(manual_offsets, dict) or not isinstance(overrides, list):
        raise QualityContractError(
            "invalid_manifest",
            "The normalized manifest manual offsets or overrides are malformed.",
        )

    paths: list[str] = []
    sources: list[dict[str, object]] = []
    for input_index, item in enumerate(manifest_inputs):
        if not isinstance(item, dict):
            raise QualityContractError(
                "invalid_manifest",
                f"The normalized manifest input {input_index} must be an object.",
            )
        path = item.get("path")
        label = item.get("label")
        if not isinstance(path, str) or not path:
            raise QualityContractError(
                "invalid_manifest",
                f"The normalized manifest input {input_index} has no path.",
            )
        if not isinstance(label, str) or not label:
            raise QualityContractError(
                "invalid_manifest",
                f"The normalized manifest input {input_index} has no label.",
            )
        paths.append(path)
        sources.append(
            {
                "input_index": input_index,
                "path": path,
                "label": label,
            }
        )
    return (
        paths,
        sources,
        reference_index,  # type: ignore[return-value]
        manual_offsets,
        window_seconds,  # type: ignore[return-value]
        hop_seconds,  # type: ignore[return-value]
        overrides,
    )


def _alignment_summary(alignment, manual_offsets: dict[int, int]) -> dict[str, object]:
    inputs = []
    for item in sorted(alignment.inputs, key=lambda value: value.input_index):
        if item.input_index == alignment.reference_index:
            source = "reference"
        elif item.input_index in manual_offsets:
            source = "manual"
        else:
            source = "automatic"
        inputs.append(
            {
                "input_index": item.input_index,
                "source": source,
                "status": item.status,
                "offset_samples": item.offset_samples,
                "offset_ms": item.offset_ms,
                "time_scale": item.time_scale,
                "drift_ppm": item.drift_ppm,
                "confidence": item.confidence,
                "correction_method": item.correction_method,
            }
        )
    return {
        "reference_index": alignment.reference_index,
        "sample_rate": alignment.sample_rate,
        "inputs": inputs,
    }


def _review_intervals(selection) -> list[tuple[int, int, int]]:
    """Cut final segments at grid points at least a minimum away from ends."""

    step = int(round(REVIEW_ROW_SECONDS * selection.sample_rate))
    margin = int(round(MINIMUM_REVIEW_ROW_SECONDS * selection.sample_rate))
    rows: list[tuple[int, int, int]] = []
    for segment_index, segment in enumerate(selection.segments):
        first_cut = -(-(segment.start_sample + margin) // step) * step
        bounds = [
            segment.start_sample,
            *range(first_cut, segment.end_sample - margin + 1, step),
            segment.end_sample,
        ]
        rows.extend(
            (start, end, segment_index)
            for start, end in zip(bounds, bounds[1:])
        )
    return rows


def _selection_payload(
    final_selection,
    automatic_selection,
    interval_evidence: Callable,
    reason_descriptions: dict[str, str],
    *,
    window_seconds: float,
    hop_seconds: float,
) -> dict[str, object]:
    sample_rate = final_selection.sample_rate
    automatic_segments = automatic_selection.segments
    automatic_starts = [segment.start_sample for segment in automatic_segments]
    intervals = _review_intervals(final_selection)
    evidence = interval_evidence(
        final_selection.quality_report,
        [(start, end) for start, end, _ in intervals],
    )
    used_reasons: set[str] = set()
    rows: list[dict[str, object]] = []
    for (start, end, segment_index), inputs in zip(intervals, evidence):
        segment = final_selection.segments[segment_index]
        position = bisect_right(automatic_starts, start) - 1
        parent = automatic_segments[position] if position >= 0 else None
        # Overrides are applied after the automatic plan and split it only at
        # its own boundaries, so every row has one automatic parent.
        if parent is None or parent.end_sample < end:
            raise RuntimeError("a review row is not inside one automatic segment")
        # Reasons and confidence describe the automatic decision of the parent
        # segment; the score is this row's own evidence for that input.
        rows.append(
            {
                "start_sample": start,
                "end_sample": end,
                "start": start / sample_rate,
                "end": end / sample_rate,
                "segment_index": segment_index,
                "selected_input_index": segment.selected_input_index,
                "automatic_input_index": segment.automatic_input_index,
                "selection_source": segment.selection_source,
                "automatic": {
                    "input_index": segment.automatic_input_index,
                    "quality_score": inputs[
                        segment.automatic_input_index
                    ].quality_score,
                    "confidence": float(parent.confidence),
                    "reason": parent.reason,
                    "reason_codes": list(parent.reason_codes),
                    "segment_start_sample": parent.start_sample,
                    "segment_end_sample": parent.end_sample,
                },
                "inputs": [item.to_dict() for item in inputs],
            }
        )
        used_reasons.update(parent.reason_codes)
    for segment in final_selection.segments:
        used_reasons.update(segment.reason_codes)

    return {
        "sample_rate": int(sample_rate),
        "timeline_sample_count": int(final_selection.timeline_sample_count),
        "duration_seconds": final_selection.timeline_sample_count / sample_rate,
        "reference_index": int(final_selection.reference_index),
        "window_seconds": float(window_seconds),
        "hop_seconds": float(hop_seconds),
        "review_row_seconds": REVIEW_ROW_SECONDS,
        "config": final_selection.config.to_dict(),
        "switch_count": final_selection.switch_count,
        "manual_override_count": sum(
            1 for segment in final_selection.segments if segment.manual_override
        ),
        "segments": [
            segment.to_dict(sample_rate) for segment in final_selection.segments
        ],
        "rows": rows,
        "warnings": [
            warning.to_dict(sample_rate) for warning in final_selection.warnings
        ],
        "reason_descriptions": {
            reason: reason_descriptions.get(reason, reason)
            for reason in sorted(used_reasons)
        },
    }


def plan_channel_selection(
    manifest: dict[str, object],
    multimic_module=None,
) -> ChannelSelectionPlan:
    """Run preparation, reviewed alignment, quality analysis and selection.

    Manual offsets replace their inputs' automatic alignment and manual
    overrides are applied after the automatic channel plan, exactly as the
    GUI presents them.  Raises the module's ``MultiAudioPreparationError``,
    :class:`UnresolvedAlignmentError`, or :class:`QualityContractError`.
    """

    engine = multimic_module or _default_multimic_module()
    (
        paths,
        sources,
        reference_index,
        manual_offsets,
        window_seconds,
        hop_seconds,
        raw_overrides,
    ) = _manifest_settings(manifest)

    prepared = engine.prepare_multi_audio_inputs(paths)
    alignment = engine.apply_manual_alignment_offsets(
        engine.align_prepared_inputs(
            prepared,
            reference_index=reference_index,
        ),
        manual_offsets,
    )
    if alignment.failures:
        raise UnresolvedAlignmentError(alignment)

    quality = engine.analyze_prepared_quality(
        prepared,
        alignment,
        speech_config=engine.SpeechAnalysisConfig(
            window_seconds=window_seconds,
            hop_seconds=hop_seconds,
        ),
    )
    overrides = []
    for index, item in enumerate(raw_overrides):
        if item["end_sample"] > quality.timeline_sample_count:
            raise QualityContractError(
                "invalid_override",
                f"selection.overrides[{index}] ends after the reference timeline "
                f"({quality.timeline_sample_count} samples).",
            )
        overrides.append(
            engine.ChannelSelectionOverride(
                item["start_sample"],
                item["end_sample"],
                item["input_index"],
            )
        )

    automatic_selection = engine.select_channels(quality)
    return ChannelSelectionPlan(
        sources=tuple(sources),
        manual_offsets=dict(manual_offsets),
        window_seconds=window_seconds,
        hop_seconds=hop_seconds,
        prepared=prepared,
        alignment=alignment,
        quality=quality,
        automatic_selection=automatic_selection,
        selection=(
            engine.select_channels(quality, overrides=tuple(overrides))
            if overrides
            else automatic_selection
        ),
    )


def _analyze_manifest(
    manifest: dict[str, object],
    multimic_module,
) -> tuple[dict[str, object], int]:
    try:
        plan = plan_channel_selection(manifest, multimic_module)
    except UnresolvedAlignmentError as exc:
        return _unresolved_alignment_payload(exc.alignment), 1
    except Exception as exc:
        preparation_error_type = getattr(
            multimic_module,
            "MultiAudioPreparationError",
            None,
        )
        if isinstance(preparation_error_type, type) and isinstance(
            exc,
            preparation_error_type,
        ):
            return _preparation_error_payload(exc), 1
        raise

    return (
        {
            "version": RESPONSE_VERSION,
            "ok": True,
            "warnings": _serialized(
                getattr(plan.prepared, "warnings", ()),
                "A preparation warning",
            ),
            "sources": list(plan.sources),
            "alignment": _alignment_summary(plan.alignment, plan.manual_offsets),
            "quality": {
                "inputs": _serialized(plan.quality.inputs, "A quality summary"),
            },
            "selection": _selection_payload(
                plan.selection,
                plan.automatic_selection,
                multimic_module.interval_input_evidence,
                dict(multimic_module.SELECTION_REASON_DESCRIPTIONS),
                window_seconds=plan.window_seconds,
                hop_seconds=plan.hop_seconds,
            ),
        },
        0,
    )


def _contract_error_payload(exc: QualityContractError) -> dict[str, object]:
    return {
        "version": RESPONSE_VERSION,
        "ok": False,
        "kind": "contract",
        "code": exc.code,
        "message": str(exc),
        "errors": [],
        "warnings": [],
    }


def _dependency_error_payload(exc: ModuleNotFoundError) -> dict[str, object]:
    module_name = exc.name or "unknown"
    return {
        "version": RESPONSE_VERSION,
        "ok": False,
        "kind": "dependency",
        "code": "dependency_missing",
        "message": f"Required Python module is not installed: {module_name}.",
        "errors": [],
        "warnings": [],
    }


def _unexpected_error_payload() -> dict[str, object]:
    return {
        "version": RESPONSE_VERSION,
        "ok": False,
        "kind": "unexpected",
        "code": "quality_analysis_failed",
        "message": "Multi-audio quality analysis failed.",
        "errors": [],
        "warnings": [],
    }


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    multimic_module=None,
    manifest_loader: Callable[[str | Path], dict[str, object]] | None = None,
) -> int:
    """Run one quality analysis request and emit exactly one JSON object."""

    output = stdout if stdout is not None else sys.stdout
    diagnostic = stderr if stderr is not None else sys.stderr
    exit_code = 1

    try:
        args = parse_arguments(argv)
        with redirect_stdout(diagnostic):
            loader = manifest_loader or _default_manifest_loader
            manifest = loader(args.multi_input_manifest)
            if not isinstance(manifest, dict):
                raise QualityContractError(
                    "invalid_manifest",
                    "The normalized manifest must be an object.",
                )
            engine = multimic_module or _default_multimic_module()
            payload, exit_code = _analyze_manifest(manifest, engine)
    except QualityContractError as exc:
        diagnostic.write(f"Quality analysis contract error: {exc}\n")
        diagnostic.flush()
        payload = _contract_error_payload(exc)
        exit_code = 2
    except ModuleNotFoundError as exc:
        _write_diagnostic(diagnostic, exc)
        payload = _dependency_error_payload(exc)
        exit_code = 1
    except (TypeError, ValueError) as exc:
        _write_diagnostic(diagnostic, exc)
        payload = _contract_error_payload(
            QualityContractError("quality_contract_error", str(exc))
        )
        exit_code = 2
    except KeyboardInterrupt:
        payload = {
            "version": RESPONSE_VERSION,
            "ok": False,
            "kind": "cancelled",
            "code": "cancelled",
            "message": "Multi-audio quality analysis was cancelled.",
            "errors": [],
            "warnings": [],
        }
        exit_code = 130
    except Exception as exc:
        _write_diagnostic(diagnostic, exc)
        payload = _unexpected_error_payload()
        exit_code = 1

    try:
        serialized = _strict_payload_text(payload)
    except Exception as exc:
        _write_diagnostic(diagnostic, exc)
        payload = _unexpected_error_payload()
        serialized = _strict_payload_text(payload)
        exit_code = 1

    output.write(serialized + "\n")
    output.flush()
    return exit_code


def _configure_standard_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(
                encoding="utf-8",
                errors="strict",
                newline="\n",
                write_through=True,
            )


if __name__ == "__main__":
    _configure_standard_streams()
    raise SystemExit(main())
