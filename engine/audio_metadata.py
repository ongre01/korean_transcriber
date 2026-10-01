#!/usr/bin/env python
"""Inspect an audio/video file for the Qt frontend without loading the AI engine."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import traceback


class MetadataFailure(RuntimeError):
    pass


def inspect_audio(input_path: Path) -> dict[str, object]:
    path = input_path.expanduser().resolve()
    if not path.is_file():
        raise MetadataFailure("Input file not found.")

    try:
        import av
    except ModuleNotFoundError as exc:
        raise MetadataFailure("Required Python module is not installed: av.") from exc

    try:
        container = av.open(str(path))
    except Exception as exc:
        raise MetadataFailure("The selected file could not be opened as media.") from exc

    try:
        stream = next((candidate for candidate in container.streams if candidate.type == "audio"), None)
        if stream is None:
            raise MetadataFailure("No audio stream was found in the selected file.")

        decoded_seconds = 0.0
        decoded_samples = 0
        try:
            for frame in container.decode(stream):
                samples = int(frame.samples or 0)
                sample_rate = int(frame.sample_rate or 0)
                if samples > 0 and sample_rate > 0:
                    decoded_samples += samples
                    decoded_seconds += samples / sample_rate
        except Exception as exc:
            raise MetadataFailure("The selected audio stream could not be decoded.") from exc

        if decoded_samples <= 0:
            raise MetadataFailure("Audio decoding produced no samples.")

        duration_seconds: float | None = None
        if stream.duration is not None and stream.time_base is not None:
            duration_seconds = float(stream.duration * stream.time_base)
        elif container.duration is not None:
            duration_seconds = float(container.duration / av.time_base)

        if (
            duration_seconds is None
            or not math.isfinite(duration_seconds)
            or duration_seconds <= 0.0
        ):
            duration_seconds = decoded_seconds

        if not math.isfinite(duration_seconds) or duration_seconds < 0.0:
            raise MetadataFailure("The audio duration could not be determined.")

        return {
            "ok": True,
            "path": str(path),
            "file_name": path.name,
            "size_bytes": path.stat().st_size,
            "duration_seconds": duration_seconds,
        }
    finally:
        container.close()


def _write_payload(payload: dict[str, object]) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ),
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect an audio file for the Qt frontend")
    parser.add_argument("--input", required=True)
    args = parser.parse_args(argv)

    try:
        _write_payload(inspect_audio(Path(args.input)))
        return 0
    except MetadataFailure as exc:
        _write_payload({"ok": False, "error": str(exc)})
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
        return 1
    except Exception as exc:
        _write_payload({"ok": False, "error": "Audio metadata could not be read."})
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
        return 1


def _configure_standard_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="strict", newline="\n", write_through=True)


if __name__ == "__main__":
    _configure_standard_streams()
    raise SystemExit(main())
