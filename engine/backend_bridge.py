#!/usr/bin/env python
"""JSON Lines bridge between the Qt frontend and the transcription engine."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import gc
import importlib
import json
import math
from numbers import Integral
from pathlib import Path
import sys
import traceback
from typing import Callable, TextIO


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


class BridgeArgumentError(ValueError):
    pass


class BridgeFailure(RuntimeError):
    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


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
    def __init__(self, stream: TextIO):
        self._stream = stream

    def emit(self, event_type: str, **fields) -> None:
        payload = {"type": event_type, **fields}
        line = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        self._stream.write(line + "\n")
        self._stream.flush()

    def state(self, value: str) -> None:
        self.emit("state", value=value)

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


def build_parser() -> argparse.ArgumentParser:
    parser = BridgeArgumentParser(
        description="Qt JSON Lines bridge for Korean transcription"
    )
    parser.add_argument("--input", required=True)
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
    parser.add_argument("--diarization", action="store_true")
    parser.add_argument("--num-speakers", type=_speaker_count, default=-1)
    parser.add_argument("--speaker-threshold", type=float, default=0.5)
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
    return args


def _failure_message(prefix: str, exc: BaseException) -> str:
    detail = str(exc).strip()
    return f"{prefix}: {detail}" if detail else prefix


def _call(prefix: str, operation: Callable):
    try:
        return operation()
    except BridgeFailure:
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


def _emit_transcription_progress(
    writer: EventWriter,
    processed: float,
    total: float,
) -> None:
    processed = float(processed)
    total = float(total)
    if not math.isfinite(processed) or not math.isfinite(total):
        raise RuntimeError("transcription progress must be finite")
    total = max(0.0, total)
    processed = min(total, max(0.0, processed)) if total > 0.0 else 0.0
    value = 100 if total <= 0.0 else int(round(processed / total * 100.0))
    writer.progress(value, processed, total)


def _load_diarize_function() -> Callable:
    return importlib.import_module("diarization").diarize_audio


def run_bridge(
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

    model_dir = Path(args.model_dir).expanduser().resolve()
    if not model_dir.is_dir():
        raise BridgeFailure(f"Model directory not found: {model_dir}", exit_code=3)

    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else input_path.parent
    )

    device = _call(
        "OpenVINO device selection failed",
        lambda: resolve_device(engine, args.device),
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

    writer.state("decoding_audio")
    writer.progress(None)
    audio = _call(
        "Audio decoding failed",
        lambda: engine.decode_audio_16k_mono(input_path),
    )
    duration = len(audio) / engine.TARGET_SAMPLE_RATE

    writer.state("loading_model")
    writer.progress(None)
    pipe = _call(
        "Whisper model could not be loaded",
        lambda: engine.load_whisper_pipeline(model_dir, device, args.model_label),
    )
    config = _call(
        "Whisper generation configuration failed",
        lambda: engine.configure_generation(
            pipe,
            args.language,
            args.beams,
            hotwords,
            initial_prompt,
        ),
    )

    writer.state("transcribing")
    transcription_complete = False

    def progress_callback(processed: float, total: float, _index: int, _count: int) -> None:
        nonlocal transcription_complete
        _emit_transcription_progress(writer, processed, total)
        transcription_complete = total <= 0.0 or processed >= total

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
    if not transcription_complete:
        _emit_transcription_progress(writer, duration, duration)

    if args.diarization:
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
                device=args.diarization_device,
                fallback_to_cpu=not args.diarization_no_fallback,
            )
            engine.assign_speakers(segments, turns)

        _call("Speaker diarization failed", perform_diarization)

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
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = engine.save_outputs(
            input_path,
            segments,
            duration,
            args.output_suffix,
            diarized=args.diarization,
            output_dir=output_dir,
        )
        txt_path, srt_path = (Path(path).resolve() for path in paths)
        if not txt_path.is_file() or not srt_path.is_file():
            raise RuntimeError("the engine did not create both result files")
        return txt_path, srt_path

    txt_path, srt_path = _call("Result files could not be saved", save_result)
    writer.emit(
        "completed",
        text_file=str(txt_path),
        srt_file=str(srt_path),
    )
    return 0


def _write_diagnostic(diagnostic: TextIO, exc: BaseException) -> None:
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=diagnostic)
    diagnostic.flush()


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
        writer.emit("error", message=f"Invalid bridge arguments: {exc}")
        diagnostic.write(f"Argument error: {exc}\n")
        diagnostic.flush()
        return 2

    try:
        if engine_module is None:
            engine_module = importlib.import_module("transcribe_npu")
        with redirect_stdout(diagnostic):
            return run_bridge(
                args,
                engine_module,
                writer,
                diarize_function=diarize_function,
            )
    except KeyboardInterrupt as exc:
        writer.emit("error", message="Transcription was cancelled.")
        _write_diagnostic(diagnostic, exc)
        return 130
    except BridgeFailure as exc:
        writer.emit("error", message=str(exc))
        _write_diagnostic(diagnostic, exc)
        return exc.exit_code
    except ModuleNotFoundError as exc:
        name = exc.name or "unknown"
        writer.emit(
            "error",
            message=f"Required Python module is not installed: {name}.",
        )
        _write_diagnostic(diagnostic, exc)
        return 1
    except Exception as exc:
        writer.emit("error", message=_failure_message("Backend processing failed", exc))
        _write_diagnostic(diagnostic, exc)
        return 1


def _configure_standard_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="strict", newline="\n", write_through=True)


if __name__ == "__main__":
    _configure_standard_streams()
    raise SystemExit(main())
