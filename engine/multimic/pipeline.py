"""Shared preparation and alignment entry points for multi-microphone audio.

Preparation turns every source into the same 16 kHz mono representation and
keeps a separate validity mask so later timeline padding is not mistaken for
recorded silence.  Offset and clock-drift estimation are delegated to
``align.py``; quality scoring and merged-audio rendering remain later stages.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import math
from numbers import Real
import operator
import os
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np

from .align import (
    AlignmentConfig,
    InputAlignment,
    MultiAudioAlignment,
    estimate_time_alignments,
)

try:  # The engine is imported both as a source directory and as a package.
    from ..audio_metadata import (
        AudioStreamInfo,
        MetadataFailure,
        SUPPORTED_AUDIO_EXTENSIONS,
        decode_audio_stream,
    )
except ImportError:  # pragma: no cover - exercised by the application's import layout
    from audio_metadata import (  # type: ignore[no-redef]
        AudioStreamInfo,
        MetadataFailure,
        SUPPORTED_AUDIO_EXTENSIONS,
        decode_audio_stream,
    )


ANALYSIS_SAMPLE_RATE = 16_000
MINIMUM_INPUT_COUNT = 2
RECOMMENDED_MAX_INPUT_COUNT = 8

# Short aliases are kept for downstream alignment/quality modules.
TARGET_SAMPLE_RATE = ANALYSIS_SAMPLE_RATE
MIN_INPUT_COUNT = MINIMUM_INPUT_COUNT
RECOMMENDED_MAX_INPUTS = RECOMMENDED_MAX_INPUT_COUNT


@dataclass(frozen=True)
class SourceFingerprint:
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return {"size_bytes": self.size_bytes, "sha256": self.sha256}


@dataclass(frozen=True)
class InputFailure:
    input_index: int | None
    path: Path | None
    code: str
    message: str

    @property
    def index(self) -> int | None:
        return self.input_index

    def to_dict(self) -> dict[str, object]:
        return {
            "input_index": self.input_index,
            "path": str(self.path) if self.path is not None else None,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True)
class PreparationWarning:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class AudioInputMetadata:
    input_index: int
    path: Path
    file_name: str
    extension: str
    size_bytes: int
    sha256: str
    duration_seconds: float
    decoded_duration_seconds: float
    sample_rate: int
    channels: int
    channel_layout: str
    codec: str
    container_format: str
    analysis_sample_rate: int
    analysis_duration_seconds: float
    downmix_method: str
    resample_method: str

    @property
    def source_sample_rate(self) -> int:
        return self.sample_rate

    @property
    def source_channels(self) -> int:
        return self.channels

    @property
    def source_sha256(self) -> str:
        return self.sha256

    @property
    def mono_conversion(self) -> str:
        return self.downmix_method

    def to_dict(self) -> dict[str, object]:
        return {
            "input_index": self.input_index,
            "path": str(self.path),
            "file_name": self.file_name,
            "extension": self.extension,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "duration_seconds": self.duration_seconds,
            "decoded_duration_seconds": self.decoded_duration_seconds,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "channel_layout": self.channel_layout,
            "codec": self.codec,
            "container_format": self.container_format,
            "analysis_sample_rate": self.analysis_sample_rate,
            "analysis_duration_seconds": self.analysis_duration_seconds,
            "downmix_method": self.downmix_method,
            "resample_method": self.resample_method,
        }


@dataclass
class AnalysisSignal:
    samples: np.ndarray
    valid_mask: np.ndarray
    sample_rate: int = ANALYSIS_SAMPLE_RATE

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")

        samples = np.ascontiguousarray(self.samples, dtype=np.float32)
        valid_mask = np.ascontiguousarray(self.valid_mask, dtype=np.bool_)
        if samples.ndim != 1:
            raise ValueError("analysis samples must be one-dimensional")
        if valid_mask.ndim != 1:
            raise ValueError("valid_mask must be one-dimensional")
        if samples.shape != valid_mask.shape:
            raise ValueError("analysis samples and valid_mask must have the same length")
        if not np.isfinite(samples).all():
            raise ValueError("analysis samples must contain only finite values")

        self.samples = samples
        self.valid_mask = valid_mask

    @property
    def duration_seconds(self) -> float:
        return self.samples.size / self.sample_rate

    @property
    def valid_sample_count(self) -> int:
        return int(np.count_nonzero(self.valid_mask))

    def valid_intervals(self) -> tuple[tuple[int, int], ...]:
        """Return half-open sample intervals represented by the dense mask."""

        if self.valid_mask.size == 0:
            return ()
        padded = np.pad(self.valid_mask, (1, 1), constant_values=False)
        edges = np.flatnonzero(padded[1:] != padded[:-1])
        return tuple(
            (int(start), int(end))
            for start, end in zip(edges[0::2], edges[1::2])
        )

    def valid_samples(
        self,
        start_sample: int = 0,
        end_sample: int | None = None,
    ) -> np.ndarray:
        """Return only recorded samples from a half-open analysis interval."""

        start = operator.index(start_sample)
        end = self.samples.size if end_sample is None else operator.index(end_sample)
        if start < 0 or end < start or end > self.samples.size:
            raise ValueError("sample bounds must satisfy 0 <= start <= end <= sample_count")
        interval = slice(start, end)
        return self.samples[interval][self.valid_mask[interval]]


@dataclass(frozen=True)
class PreparedAudioInput:
    metadata: AudioInputMetadata
    analysis: AnalysisSignal

    @property
    def samples(self) -> np.ndarray:
        return self.analysis.samples

    @property
    def valid_mask(self) -> np.ndarray:
        return self.analysis.valid_mask

    @property
    def sample_rate(self) -> int:
        return self.analysis.sample_rate


@dataclass(frozen=True)
class PreparedMultiAudio:
    inputs: tuple[PreparedAudioInput, ...]
    warnings: tuple[PreparationWarning, ...]
    analysis_sample_rate: int
    recommended_max_inputs: int = RECOMMENDED_MAX_INPUT_COUNT

    def __len__(self) -> int:
        return len(self.inputs)

    def __iter__(self) -> Iterator[PreparedAudioInput]:
        return iter(self.inputs)


class MultiAudioPreparationError(ValueError):
    """Batch error that retains every file-specific failure."""

    def __init__(
        self,
        failures: Iterable[InputFailure],
        *,
        partial_inputs: Iterable[PreparedAudioInput] = (),
        warnings: Iterable[PreparationWarning] = (),
    ) -> None:
        self.failures = tuple(failures)
        self.partial_inputs = tuple(partial_inputs)
        self.warnings = tuple(warnings)
        summary = "; ".join(
            f"[{failure.code}] {failure.message}" for failure in self.failures
        )
        super().__init__(summary or "Multi-audio input preparation failed.")

    @property
    def file_errors(self) -> tuple[InputFailure, ...]:
        return tuple(failure for failure in self.failures if failure.path is not None)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": False,
            "errors": [failure.to_dict() for failure in self.failures],
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


class _InputPreparationFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def is_supported_audio_path(path: str | Path) -> bool:
    return Path(path).suffix.casefold() in SUPPORTED_AUDIO_EXTENSIONS


def fingerprint_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> SourceFingerprint:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    source = Path(path)
    digest = sha256()
    with source.open("rb") as stream:
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return SourceFingerprint(size_bytes=source.stat().st_size, sha256=digest.hexdigest())


def build_validity_mask(
    sample_count: int,
    *,
    valid_start_sample: int = 0,
    valid_end_sample: int | None = None,
) -> np.ndarray:
    count = operator.index(sample_count)
    start = operator.index(valid_start_sample)
    end = count if valid_end_sample is None else operator.index(valid_end_sample)
    if count < 0:
        raise ValueError("sample_count must not be negative")
    if start < 0 or end < start or end > count:
        raise ValueError("valid sample bounds must satisfy 0 <= start <= end <= sample_count")

    mask = np.zeros(count, dtype=np.bool_)
    mask[start:end] = True
    return mask


def place_signal_on_timeline(
    signal: AnalysisSignal,
    *,
    start_sample: int,
    timeline_sample_count: int,
) -> AnalysisSignal:
    """Place a signal on a shared timeline without marking padding as valid."""

    start = operator.index(start_sample)
    timeline_count = operator.index(timeline_sample_count)
    if timeline_count < 0:
        raise ValueError("timeline_sample_count must not be negative")

    samples = np.zeros(timeline_count, dtype=np.float32)
    valid_mask = np.zeros(timeline_count, dtype=np.bool_)
    source_start = max(0, -start)
    destination_start = max(0, start)
    copy_count = min(
        signal.samples.size - source_start,
        timeline_count - destination_start,
    )
    if copy_count > 0:
        source_slice = slice(source_start, source_start + copy_count)
        destination_slice = slice(destination_start, destination_start + copy_count)
        samples[destination_slice] = signal.samples[source_slice]
        valid_mask[destination_slice] = signal.valid_mask[source_slice]

    return AnalysisSignal(
        samples=samples,
        valid_mask=valid_mask,
        sample_rate=signal.sample_rate,
    )


def warp_signal_to_reference_timeline(
    signal: AnalysisSignal,
    alignment: InputAlignment,
    timeline_sample_count: int,
    chunk_size: int = 262_144,
) -> AnalysisSignal:
    """Map one input onto the reference timeline using its affine alignment.

    ``InputAlignment`` defines the target-to-reference mapping
    ``reference = time_scale * target + offset_samples``.  Rendering therefore
    samples the input at ``(reference - offset_samples) / time_scale``.  The
    output length is supplied by the reference input so neither target padding
    nor clock drift can change the canonical timeline.

    Automatic rendering is deliberately refused for failed alignments and for
    results that require manual review.  Interpolation is processed in bounded
    chunks, and an output sample is valid only when every source sample used by
    the linear interpolation is valid.
    """

    if not isinstance(signal, AnalysisSignal):
        raise TypeError("signal must be an AnalysisSignal")
    if not isinstance(alignment, InputAlignment):
        raise TypeError("alignment must be an InputAlignment")
    if alignment.requires_manual_review:
        raise ValueError(
            "alignment requires manual review before it can be applied"
        )
    if not alignment.success:
        raise ValueError("only a successful alignment can be applied")

    if isinstance(timeline_sample_count, bool):
        raise ValueError("timeline_sample_count must be a non-negative integer")
    try:
        timeline_count = operator.index(timeline_sample_count)
    except TypeError as exc:
        raise ValueError(
            "timeline_sample_count must be a non-negative integer"
        ) from exc
    if timeline_count < 0:
        raise ValueError("timeline_sample_count must not be negative")
    if isinstance(chunk_size, bool):
        raise ValueError("chunk_size must be a positive integer")
    try:
        render_chunk_size = operator.index(chunk_size)
    except TypeError as exc:
        raise ValueError("chunk_size must be a positive integer") from exc
    if render_chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")

    offset = alignment.offset_samples
    scale = alignment.time_scale
    if (
        offset is None
        or isinstance(offset, bool)
        or not isinstance(offset, Real)
        or not math.isfinite(float(offset))
    ):
        raise ValueError("alignment offset_samples must be a finite number")
    if (
        scale is None
        or isinstance(scale, bool)
        or not isinstance(scale, Real)
        or not math.isfinite(float(scale))
        or float(scale) <= 0.0
    ):
        raise ValueError("alignment time_scale must be a finite number greater than 0")
    if not isinstance(alignment.correction_applied, bool):
        raise ValueError("alignment correction_applied must be a boolean")
    if not alignment.correction_applied and float(scale) != 1.0:
        raise ValueError(
            "alignment without an applied affine correction must use time_scale 1"
        )

    # Preserve the established integer-placement behavior (including exact
    # source samples and masks) for the reference and offset-only alignments.
    if float(scale) == 1.0:
        try:
            integer_offset = operator.index(offset)
        except TypeError:
            integer_offset = None
        if integer_offset is not None:
            return place_signal_on_timeline(
                signal,
                start_sample=integer_offset,
                timeline_sample_count=timeline_count,
            )

    samples = np.zeros(timeline_count, dtype=np.float32)
    valid_mask = np.zeros(timeline_count, dtype=np.bool_)
    source_count = signal.samples.size
    if timeline_count == 0 or source_count == 0:
        return AnalysisSignal(
            samples=samples,
            valid_mask=valid_mask,
            sample_rate=signal.sample_rate,
        )

    offset_value = float(offset)
    scale_value = float(scale)
    for chunk_start in range(0, timeline_count, render_chunk_size):
        chunk_end = min(timeline_count, chunk_start + render_chunk_size)
        reference_positions = np.arange(
            chunk_start,
            chunk_end,
            dtype=np.float64,
        )
        source_positions = (reference_positions - offset_value) / scale_value
        inside = (source_positions >= 0.0) & (source_positions <= source_count - 1)
        if not np.any(inside):
            continue

        # Clip only for safe vectorized indexing.  ``inside`` remains part of
        # the validity test, so clipped out-of-range positions stay padding.
        clipped_positions = np.clip(source_positions, 0.0, source_count - 1)
        left_indexes = np.floor(clipped_positions).astype(np.int64)
        right_indexes = np.ceil(clipped_positions).astype(np.int64)
        fractions = clipped_positions - left_indexes
        source_valid = (
            inside
            & signal.valid_mask[left_indexes]
            & signal.valid_mask[right_indexes]
        )
        if not np.any(source_valid):
            continue

        left_values = signal.samples[left_indexes].astype(np.float64, copy=False)
        right_values = signal.samples[right_indexes].astype(np.float64, copy=False)
        interpolated = left_values + fractions * (right_values - left_values)
        destination = slice(chunk_start, chunk_end)
        chunk_samples = samples[destination]
        chunk_mask = valid_mask[destination]
        chunk_samples[source_valid] = interpolated[source_valid].astype(
            np.float32,
            copy=False,
        )
        chunk_mask[source_valid] = True

    return AnalysisSignal(
        samples=samples,
        valid_mask=valid_mask,
        sample_rate=signal.sample_rate,
    )


def _append_resampled_frames(frames: Iterable[object], chunks: list[np.ndarray]) -> None:
    for frame in frames:
        values = np.asarray(frame.to_ndarray(), dtype=np.float32).reshape(-1)
        if values.size:
            if not np.isfinite(values).all():
                raise ValueError("decoded audio contains non-finite samples")
            chunks.append(np.ascontiguousarray(values, dtype=np.float32))


def _decode_for_analysis(
    path: Path,
    target_sample_rate: int,
) -> tuple[AudioStreamInfo, AnalysisSignal]:
    try:
        from av.audio.resampler import AudioResampler
    except ModuleNotFoundError as exc:
        raise _InputPreparationFailure(
            "dependency_missing",
            "Required Python module is not installed: av.",
        ) from exc

    try:
        resampler = AudioResampler(
            format="fltp",
            layout="mono",
            rate=target_sample_rate,
        )
    except Exception as exc:
        raise _InputPreparationFailure(
            "resampler_initialization_failed",
            "The 16 kHz mono audio converter could not be initialized.",
        ) from exc

    chunks: list[np.ndarray] = []

    def consume_frame(frame: object) -> None:
        _append_resampled_frames(resampler.resample(frame), chunks)

    info = decode_audio_stream(path, consume_frame)
    try:
        _append_resampled_frames(resampler.resample(None), chunks)
    except Exception as exc:
        raise _InputPreparationFailure(
            "decode_failed",
            "The selected audio stream could not be fully decoded.",
        ) from exc

    if not chunks:
        raise _InputPreparationFailure(
            "no_samples",
            "Audio decoding produced no analysis samples.",
        )

    samples = np.ascontiguousarray(np.concatenate(chunks), dtype=np.float32)
    signal = AnalysisSignal(
        samples=samples,
        valid_mask=build_validity_mask(samples.size),
        sample_rate=target_sample_rate,
    )
    return info, signal


def _prepare_one(
    index: int,
    path: Path,
    target_sample_rate: int,
) -> PreparedAudioInput:
    if not path.is_file():
        raise _InputPreparationFailure("not_found", "Input file not found.")
    if not is_supported_audio_path(path):
        supported = ", ".join(sorted(SUPPORTED_AUDIO_EXTENSIONS))
        raise _InputPreparationFailure(
            "unsupported_extension",
            f"Unsupported audio file extension. Supported extensions: {supported}.",
        )

    stat_before = path.stat()
    before = fingerprint_file(path)
    info, signal = _decode_for_analysis(path, target_sample_rate)
    stat_after = path.stat()
    if (
        stat_before.st_size != stat_after.st_size
        or stat_before.st_mtime_ns != stat_after.st_mtime_ns
    ):
        raise _InputPreparationFailure(
            "source_changed",
            "The input file changed while it was being decoded.",
        )

    downmix_method = (
        "none_already_mono"
        if info.channels == 1
        else "ffmpeg_libswresample_default_downmix"
    )
    resample_method = (
        "none_same_sample_rate"
        if info.sample_rate == target_sample_rate
        else "ffmpeg_libswresample"
    )
    metadata = AudioInputMetadata(
        input_index=index,
        path=path,
        file_name=info.file_name,
        extension=path.suffix.casefold(),
        size_bytes=before.size_bytes,
        sha256=before.sha256,
        duration_seconds=info.duration_seconds,
        decoded_duration_seconds=info.decoded_duration_seconds,
        sample_rate=info.sample_rate,
        channels=info.channels,
        channel_layout=info.channel_layout,
        codec=info.codec,
        container_format=info.container_format,
        analysis_sample_rate=target_sample_rate,
        analysis_duration_seconds=signal.duration_seconds,
        downmix_method=downmix_method,
        resample_method=resample_method,
    )
    return PreparedAudioInput(metadata=metadata, analysis=signal)


def prepare_multi_audio_inputs(
    input_paths: Iterable[str | Path],
    *,
    target_sample_rate: int = ANALYSIS_SAMPLE_RATE,
) -> PreparedMultiAudio:
    """Validate and decode a batch, collecting every file-specific failure."""

    try:
        rate = operator.index(target_sample_rate)
    except TypeError as exc:
        raise ValueError("target_sample_rate must be an integer") from exc
    if rate <= 0:
        raise ValueError("target_sample_rate must be positive")
    if rate != ANALYSIS_SAMPLE_RATE:
        raise ValueError(
            f"multi-audio analysis requires a {ANALYSIS_SAMPLE_RATE} Hz sample rate"
        )

    if isinstance(input_paths, (str, Path)):
        raw_paths: list[object] = [input_paths]
    else:
        raw_paths = list(input_paths)

    if len(raw_paths) < MINIMUM_INPUT_COUNT:
        failure = InputFailure(
            input_index=None,
            path=None,
            code="insufficient_inputs",
            message=f"At least {MINIMUM_INPUT_COUNT} audio files are required.",
        )
        raise MultiAudioPreparationError((failure,))

    warnings: list[PreparationWarning] = []
    if len(raw_paths) > RECOMMENDED_MAX_INPUT_COUNT:
        warnings.append(
            PreparationWarning(
                code="recommended_input_limit_exceeded",
                message=(
                    f"{len(raw_paths)} files were selected. The initial UI recommends "
                    f"no more than {RECOMMENDED_MAX_INPUT_COUNT}, but the backend will "
                    "process all selected files."
                ),
            )
        )

    prepared: list[PreparedAudioInput] = []
    failures: list[InputFailure] = []
    path_indexes: dict[str, int] = {}
    for index, raw_path in enumerate(raw_paths):
        try:
            path = Path(raw_path).expanduser().resolve()  # type: ignore[arg-type]
        except (TypeError, ValueError, OSError) as exc:
            failures.append(
                InputFailure(
                    input_index=index,
                    path=None,
                    code="invalid_path",
                    message=f"Input path is invalid: {exc}",
                )
            )
            continue

        path_key = os.path.normcase(str(path))
        previous_index = path_indexes.get(path_key)
        if previous_index is not None:
            failures.append(
                InputFailure(
                    input_index=index,
                    path=path,
                    code="duplicate_input",
                    message=(
                        "The same input file was selected more than once "
                        f"(first selected at index {previous_index})."
                    ),
                )
            )
            continue
        path_indexes[path_key] = index

        try:
            prepared.append(_prepare_one(index, path, rate))
        except _InputPreparationFailure as exc:
            failures.append(InputFailure(index, path, exc.code, str(exc)))
        except MetadataFailure as exc:
            failures.append(InputFailure(index, path, exc.code, str(exc)))
        except OSError as exc:
            failures.append(
                InputFailure(
                    index,
                    path,
                    "file_read_failed",
                    f"Input file could not be read: {exc}",
                )
            )
        except Exception as exc:
            failures.append(
                InputFailure(
                    index,
                    path,
                    "preparation_failed",
                    f"Audio preparation failed: {exc}",
                )
            )

    if failures:
        raise MultiAudioPreparationError(
            failures,
            partial_inputs=prepared,
            warnings=warnings,
        )

    return PreparedMultiAudio(
        inputs=tuple(prepared),
        warnings=tuple(warnings),
        analysis_sample_rate=rate,
    )


def prepare_inputs(
    input_paths: Iterable[str | Path],
    *,
    target_sample_rate: int = ANALYSIS_SAMPLE_RATE,
) -> PreparedMultiAudio:
    """Concise alias for callers inside the multimic package."""

    return prepare_multi_audio_inputs(
        input_paths,
        target_sample_rate=target_sample_rate,
    )


def align_prepared_inputs(
    prepared: PreparedMultiAudio,
    *,
    reference_index: int = 0,
    config: AlignmentConfig | None = None,
) -> MultiAudioAlignment:
    """Estimate affine time alignment for already prepared 16 kHz inputs.

    Alignment failures are retained per input in the returned report.  This
    wrapper intentionally does not move samples or mutate ``prepared``; later
    pipeline stages can render each successful result with
    :func:`warp_signal_to_reference_timeline`.
    """

    if not isinstance(prepared, PreparedMultiAudio):
        raise TypeError("prepared must be a PreparedMultiAudio")
    if prepared.analysis_sample_rate != ANALYSIS_SAMPLE_RATE:
        raise ValueError(
            f"multi-audio alignment requires a {ANALYSIS_SAMPLE_RATE} Hz sample rate"
        )
    if any(item.sample_rate != ANALYSIS_SAMPLE_RATE for item in prepared.inputs):
        raise ValueError(
            f"every prepared input must use the {ANALYSIS_SAMPLE_RATE} Hz analysis rate"
        )

    return estimate_time_alignments(
        tuple(item.analysis for item in prepared.inputs),
        reference_index=reference_index,
        config=config,
    )
