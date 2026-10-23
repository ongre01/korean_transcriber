#!/usr/bin/env python
"""Inspect an audio/video file for the Qt frontend without loading the AI engine."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import sys
import traceback
from typing import Callable


SUPPORTED_AUDIO_EXTENSIONS = frozenset(
    {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".mp4"}
)


class MetadataFailure(RuntimeError):
    def __init__(self, message: str, *, code: str = "metadata_error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AudioStreamInfo:
    path: Path
    file_name: str
    size_bytes: int
    duration_seconds: float
    decoded_duration_seconds: float
    sample_rate: int
    channels: int
    channel_layout: str
    codec: str
    container_format: str

    def to_payload(self) -> dict[str, object]:
        return {
            "ok": True,
            "path": str(self.path),
            "file_name": self.file_name,
            "size_bytes": self.size_bytes,
            "duration_seconds": self.duration_seconds,
            "decoded_duration_seconds": self.decoded_duration_seconds,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "channel_layout": self.channel_layout,
            "codec": self.codec,
            "container_format": self.container_format,
        }


def _positive_int(value: object) -> int | None:
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result > 0 else None


def _channel_count(value: object) -> int | None:
    layout = getattr(value, "layout", None)
    channels = getattr(layout, "channels", None)
    if channels is not None:
        try:
            count = len(channels)
        except TypeError:
            count = _positive_int(channels)
        if count:
            return int(count)

    codec_context = getattr(value, "codec_context", None)
    return _positive_int(getattr(codec_context, "channels", None))


def _channel_layout_name(value: object) -> str | None:
    layout = getattr(value, "layout", None)
    name = getattr(layout, "name", None)
    if name is None:
        codec_context = getattr(value, "codec_context", None)
        name = getattr(getattr(codec_context, "layout", None), "name", None)
    return str(name) if name else None


def _sample_rate(value: object) -> int | None:
    direct = _positive_int(getattr(value, "sample_rate", None))
    if direct is not None:
        return direct
    codec_context = getattr(value, "codec_context", None)
    return _positive_int(getattr(codec_context, "sample_rate", None))


def decode_audio_stream(
    input_path: Path,
    frame_consumer: Callable[[object], None] | None = None,
) -> AudioStreamInfo:
    """Decode every frame and optionally forward it to an analysis consumer."""

    path = input_path.expanduser().resolve()
    if not path.is_file():
        raise MetadataFailure("Input file not found.", code="not_found")

    try:
        import av
    except ModuleNotFoundError as exc:
        raise MetadataFailure(
            "Required Python module is not installed: av.",
            code="dependency_missing",
        ) from exc

    try:
        container = av.open(str(path))
    except Exception as exc:
        raise MetadataFailure(
            "The selected file could not be opened as media.",
            code="open_failed",
        ) from exc

    try:
        stream = next(
            (candidate for candidate in container.streams if candidate.type == "audio"),
            None,
        )
        if stream is None:
            raise MetadataFailure(
                "No audio stream was found in the selected file.",
                code="no_audio_stream",
            )

        source_sample_rate = _sample_rate(stream)
        source_channels = _channel_count(stream)
        channel_layout = _channel_layout_name(stream)
        decoded_seconds = 0.0
        decoded_samples = 0
        observed_decoded_format = False
        try:
            for frame in container.decode(stream):
                samples = int(frame.samples or 0)
                frame_sample_rate = _sample_rate(frame)
                sample_rate = frame_sample_rate or source_sample_rate or 0
                if samples > 0:
                    if not observed_decoded_format:
                        source_sample_rate = frame_sample_rate or source_sample_rate
                        source_channels = _channel_count(frame) or source_channels
                        channel_layout = _channel_layout_name(frame) or channel_layout
                        observed_decoded_format = True
                    decoded_samples += samples
                    if sample_rate > 0:
                        decoded_seconds += samples / sample_rate
                if frame_consumer is not None:
                    frame_consumer(frame)
        except Exception as exc:
            raise MetadataFailure(
                "The selected audio stream could not be decoded.",
                code="decode_failed",
            ) from exc

        if decoded_samples <= 0:
            raise MetadataFailure(
                "Audio decoding produced no samples.",
                code="no_samples",
            )
        if source_sample_rate is None:
            raise MetadataFailure(
                "The audio sample rate could not be determined.",
                code="invalid_sample_rate",
            )
        if source_channels is None:
            raise MetadataFailure(
                "The audio channel count could not be determined.",
                code="invalid_channel_count",
            )

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
            raise MetadataFailure(
                "The audio duration could not be determined.",
                code="invalid_duration",
            )

        codec_context = getattr(stream, "codec_context", None)
        codec_name = getattr(codec_context, "name", None)
        format_name = getattr(getattr(container, "format", None), "name", None)

        return AudioStreamInfo(
            path=path,
            file_name=path.name,
            size_bytes=path.stat().st_size,
            duration_seconds=duration_seconds,
            decoded_duration_seconds=decoded_seconds,
            sample_rate=source_sample_rate,
            channels=source_channels,
            channel_layout=channel_layout or "unknown",
            codec=str(codec_name) if codec_name else "unknown",
            container_format=str(format_name) if format_name else "unknown",
        )
    finally:
        container.close()


def inspect_audio(input_path: Path) -> dict[str, object]:
    return decode_audio_stream(input_path).to_payload()


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
        _write_payload(
            {
                "ok": False,
                "error": str(exc),
                "code": exc.code,
                "path": str(Path(args.input).expanduser().resolve()),
            }
        )
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
        return 1
    except Exception as exc:
        _write_payload(
            {
                "ok": False,
                "error": "Audio metadata could not be read.",
                "code": "metadata_error",
                "path": str(Path(args.input).expanduser().resolve()),
            }
        )
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
