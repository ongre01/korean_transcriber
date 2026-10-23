#!/usr/bin/env python
"""One-shot Qt bridge for offline multi-microphone time alignment.

The full transcription bridge intentionally does not process multi-input jobs
yet.  This small command exposes only the already implemented preparation and
alignment stages so the MM-DEV-006 dialog can run them in a cancellable
``QProcess``.

stdout is reserved for exactly one strict JSON object.  Diagnostic output and
tracebacks are written to stderr.  A completed analysis exits with status zero
even when one or more input recordings could not be aligned; those expected
algorithm results are represented by ``alignment.success == false`` and the
per-input failure fields.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import importlib
import json
from pathlib import Path
import sys
import traceback
from typing import Callable, TextIO


RESPONSE_VERSION = 1


class AlignmentContractError(ValueError):
    """A caller-controlled request or API-contract error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AlignmentArgumentParser(argparse.ArgumentParser):
    """Return parser failures through the JSON response contract."""

    def error(self, message: str) -> None:
        raise AlignmentContractError("invalid_arguments", message)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        detail = message.strip() if message else "help requested"
        raise AlignmentContractError("invalid_arguments", detail)


def build_parser() -> argparse.ArgumentParser:
    parser = AlignmentArgumentParser(
        description="Analyze multi-microphone time alignment for the Qt frontend",
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
        raise AlignmentContractError("invalid_manifest", str(exc)) from exc


def _default_multimic_module():
    return importlib.import_module("multimic")


def _strict_payload_text(payload: dict[str, object]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def _write_payload(stream: TextIO, payload: dict[str, object]) -> None:
    stream.write(_strict_payload_text(payload) + "\n")
    stream.flush()


def _write_diagnostic(stream: TextIO, exc: BaseException) -> None:
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=stream)
    stream.flush()


def _warning_payloads(warnings: object) -> list[dict[str, object]]:
    try:
        values = tuple(warnings)  # type: ignore[arg-type]
    except TypeError as exc:
        raise AlignmentContractError(
            "invalid_preparation_result",
            "Prepared multi-audio warnings must be iterable.",
        ) from exc

    result: list[dict[str, object]] = []
    for warning in values:
        to_dict = getattr(warning, "to_dict", None)
        if not callable(to_dict):
            raise AlignmentContractError(
                "invalid_preparation_result",
                "A preparation warning did not provide to_dict().",
            )
        payload = to_dict()
        if not isinstance(payload, dict):
            raise AlignmentContractError(
                "invalid_preparation_result",
                "A preparation warning did not serialize to an object.",
            )
        result.append(payload)
    return result


def _preparation_error_payload(exc: BaseException) -> dict[str, object]:
    failures = getattr(exc, "failures", ())
    warnings = getattr(exc, "warnings", ())
    error_payloads: list[dict[str, object]] = []
    for failure in failures:
        to_dict = getattr(failure, "to_dict", None)
        if not callable(to_dict):
            raise AlignmentContractError(
                "invalid_preparation_error",
                "A preparation failure did not provide to_dict().",
            )
        payload = to_dict()
        if not isinstance(payload, dict):
            raise AlignmentContractError(
                "invalid_preparation_error",
                "A preparation failure did not serialize to an object.",
            )
        error_payloads.append(payload)

    detail = str(exc).strip()
    return {
        "version": RESPONSE_VERSION,
        "ok": False,
        "kind": "preparation",
        "code": "preparation_failed",
        "message": detail or "Multi-audio input preparation failed.",
        "errors": error_payloads,
        "warnings": _warning_payloads(warnings),
    }


def _analyze_manifest(
    manifest: dict[str, object],
    multimic_module,
) -> tuple[dict[str, object], int]:
    try:
        manifest_inputs = manifest["inputs"]
        reference_index = manifest["reference_index"]
    except (KeyError, TypeError) as exc:
        raise AlignmentContractError(
            "invalid_manifest",
            "The normalized manifest is missing inputs or reference_index.",
        ) from exc

    if not isinstance(manifest_inputs, list):
        raise AlignmentContractError(
            "invalid_manifest",
            "The normalized manifest inputs must be an array.",
        )

    paths: list[str] = []
    sources: list[dict[str, object]] = []
    for input_index, item in enumerate(manifest_inputs):
        if not isinstance(item, dict):
            raise AlignmentContractError(
                "invalid_manifest",
                f"The normalized manifest input {input_index} must be an object.",
            )
        path = item.get("path")
        label = item.get("label")
        if not isinstance(path, str) or not path:
            raise AlignmentContractError(
                "invalid_manifest",
                f"The normalized manifest input {input_index} has no path.",
            )
        if not isinstance(label, str) or not label:
            raise AlignmentContractError(
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

    try:
        prepared = multimic_module.prepare_multi_audio_inputs(paths)
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

    report = multimic_module.align_prepared_inputs(
        prepared,
        reference_index=reference_index,
    )
    to_dict = getattr(report, "to_dict", None)
    if not callable(to_dict):
        raise AlignmentContractError(
            "invalid_alignment_result",
            "The multi-audio alignment result did not provide to_dict().",
        )
    alignment_payload = to_dict()
    if not isinstance(alignment_payload, dict):
        raise AlignmentContractError(
            "invalid_alignment_result",
            "The multi-audio alignment result did not serialize to an object.",
        )

    return (
        {
            "version": RESPONSE_VERSION,
            "ok": True,
            "warnings": _warning_payloads(getattr(prepared, "warnings", ())),
            "sources": sources,
            "alignment": alignment_payload,
        },
        0,
    )


def _contract_error_payload(exc: AlignmentContractError) -> dict[str, object]:
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
        "code": "alignment_failed",
        "message": "Multi-audio alignment failed.",
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
    """Run one alignment request and emit exactly one JSON response object."""

    output = stdout if stdout is not None else sys.stdout
    diagnostic = stderr if stderr is not None else sys.stderr
    exit_code = 1

    try:
        args = parse_arguments(argv)
        with redirect_stdout(diagnostic):
            loader = manifest_loader or _default_manifest_loader
            manifest = loader(args.multi_input_manifest)
            if not isinstance(manifest, dict):
                raise AlignmentContractError(
                    "invalid_manifest",
                    "The normalized manifest must be an object.",
                )
            engine = multimic_module or _default_multimic_module()
            payload, exit_code = _analyze_manifest(manifest, engine)
    except AlignmentContractError as exc:
        diagnostic.write(f"Alignment contract error: {exc}\n")
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
            AlignmentContractError("alignment_contract_error", str(exc))
        )
        exit_code = 2
    except KeyboardInterrupt:
        payload = {
            "version": RESPONSE_VERSION,
            "ok": False,
            "kind": "cancelled",
            "code": "cancelled",
            "message": "Multi-audio alignment was cancelled.",
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
