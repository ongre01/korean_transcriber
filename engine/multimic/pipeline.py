"""Shared preparation, alignment, and rendering entry points for multi-mic audio.

Preparation turns every source into the same 16 kHz mono representation and
keeps a separate validity mask so later timeline padding is not mistaken for
recorded silence.  Offset and clock-drift estimation are delegated to
``align.py`` and quality scoring/selection to ``quality.py``/``select.py``.
The final stage renders a channel plan into ``merged_16k_mono.wav`` on the
reference timeline and stores traceable alignment and selection diagnostics
without ever writing to a source file.

:func:`plan_multi_audio_selection` and :func:`run_multi_audio_preprocessing`
chain those stages for one job, so the GUI's review and the transcription run
derive their channel plan from the same code path.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
import math
from numbers import Real
import operator
import os
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable, Iterator, Mapping, Sequence
import wave

import numpy as np

from .align import (
    AlignmentConfig,
    InputAlignment,
    MultiAudioAlignment,
    estimate_time_alignments,
)

if TYPE_CHECKING:  # ``select`` imports this module through ``quality``.
    from .quality import ChannelQualityReport
    from .select import ChannelSelectionReport, ChannelSelectionSegment

try:  # The engine is imported both as a source directory and as a package.
    from ..audio_metadata import (
        AudioStreamInfo,
        MetadataFailure,
        SUPPORTED_AUDIO_EXTENSIONS,
        decode_audio_stream,
    )
    from ..result_staging import ResultStaging
except ImportError:  # pragma: no cover - exercised by the application's import layout
    from audio_metadata import (  # type: ignore[no-redef]
        AudioStreamInfo,
        MetadataFailure,
        SUPPORTED_AUDIO_EXTENSIONS,
        decode_audio_stream,
    )
    from result_staging import ResultStaging  # type: ignore[no-redef]


ANALYSIS_SAMPLE_RATE = 16_000
MINIMUM_INPUT_COUNT = 2
RECOMMENDED_MAX_INPUT_COUNT = 8

# Short aliases are kept for downstream alignment/quality modules.
TARGET_SAMPLE_RATE = ANALYSIS_SAMPLE_RATE
MIN_INPUT_COUNT = MINIMUM_INPUT_COUNT
RECOMMENDED_MAX_INPUTS = RECOMMENDED_MAX_INPUT_COUNT

MERGED_AUDIO_FILE_NAME = "merged_16k_mono.wav"
ALIGNMENT_FILE_NAME = "alignment.json"
CHANNEL_SELECTION_FILE_NAME = "channel_selection.json"
DIAGNOSTICS_VERSION = 1

# Pre-processing stages in execution order.  The names are the bridge
# protocol's state values, so the bridge reports them unchanged.
PREPROCESSING_STAGES = (
    "decoding_audio",
    "aligning_audio",
    "detecting_speech",
    "assessing_channels",
    "selecting_channels",
    "rendering_merged_audio",
)

# Result folders are often shared, while source names and folders frequently
# contain participant names.  Diagnostics therefore omit source locations
# unless the caller explicitly opts in; content hashes remain for
# reproducibility checks.
SOURCE_PATH_POLICIES = ("omit", "file_name", "full_path")
DEFAULT_SOURCE_PATH_POLICY = "omit"

_PCM16_FULL_SCALE = 32767.0
_WAV_MAX_DATA_BYTES = 0xFFFFFFFF - 36
_WAV_WRITE_CHUNK_SAMPLES = 1 << 20


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


class UnresolvedAlignmentError(ValueError):
    """Some inputs have neither an automatic alignment nor a manual offset.

    ``alignment`` is the report after manual offsets were applied; its
    ``failures`` are the inputs that still block automatic integration.
    """

    def __init__(self, alignment: MultiAudioAlignment) -> None:
        super().__init__("multi-audio alignment is unresolved")
        self.alignment = alignment


class SelectionOverrideError(ValueError):
    """A manual channel override ends after the decoded reference timeline.

    The manifest validator cannot know the reference length, so this bound is
    only checked once the reference input has been decoded.
    """

    def __init__(self, override_index: int, timeline_sample_count: int) -> None:
        super().__init__(
            f"selection.overrides[{override_index}] ends after the reference "
            f"timeline ({timeline_sample_count} samples)."
        )
        self.override_index = override_index
        self.timeline_sample_count = timeline_sample_count


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


def warp_prepared_inputs(
    prepared: PreparedMultiAudio,
    alignment: MultiAudioAlignment,
) -> tuple[AnalysisSignal, ...]:
    """Validate and warp prepared inputs onto the alignment reference axis.

    Failed or manual-review alignment results are rejected by the existing
    renderer.  They are never silently excluded from automatic integration.
    """

    if not isinstance(prepared, PreparedMultiAudio):
        raise TypeError("prepared must be a PreparedMultiAudio")
    if not isinstance(alignment, MultiAudioAlignment):
        raise TypeError("alignment must be a MultiAudioAlignment")
    if len(prepared.inputs) < 2:
        raise ValueError("at least two prepared inputs are required")
    if (
        alignment.reference_index < 0
        or alignment.reference_index >= len(prepared.inputs)
    ):
        raise ValueError("alignment reference_index is outside the input range")
    if alignment.sample_rate != prepared.analysis_sample_rate:
        raise ValueError("alignment and prepared sample rates must match")
    if len(alignment.inputs) != len(prepared.inputs):
        raise ValueError("alignment must contain one result for every prepared input")
    input_indices = tuple(result.input_index for result in alignment.inputs)
    if sorted(input_indices) != list(range(len(prepared.inputs))):
        raise ValueError("alignment input indexes must cover every prepared input once")
    if any(
        result.reference_index != alignment.reference_index
        for result in alignment.inputs
    ):
        raise ValueError(
            "every alignment result must use the report reference_index"
        )
    if any(
        item.sample_rate != prepared.analysis_sample_rate
        for item in prepared.inputs
    ):
        raise ValueError(
            "every prepared input must use the prepared analysis sample rate"
        )

    reference_result = alignment.result_for_input(alignment.reference_index)
    if (
        not reference_result.success
        or reference_result.offset_samples != 0
        or reference_result.time_scale != 1.0
        or reference_result.correction_applied
    ):
        raise ValueError(
            "the reference input alignment must be an unchanged identity transform"
        )

    timeline_sample_count = prepared.inputs[
        alignment.reference_index
    ].samples.size
    return tuple(
        warp_signal_to_reference_timeline(
            item.analysis,
            alignment.result_for_input(input_index),
            timeline_sample_count,
        )
        for input_index, item in enumerate(prepared.inputs)
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


def apply_manual_alignment_offsets(
    alignment: MultiAudioAlignment,
    manual_offsets_ms: Mapping[int, int],
) -> MultiAudioAlignment:
    """Apply offsets the user entered after reviewing an automatic alignment.

    A manual offset is an explicit decision (MM-DEV-006), so the input is
    placed with that offset only, without clock-drift correction, whatever
    its automatic status was.  The automatic evidence (confidence, windows)
    is kept for diagnostics.  The reference input stays the identity
    transform and therefore only accepts zero.  Inputs without a manual
    offset keep their automatic result unchanged, including failures, so a
    caller can still refuse unresolved inputs through ``failures``.
    """

    if not isinstance(alignment, MultiAudioAlignment):
        raise TypeError("alignment must be a MultiAudioAlignment")
    if not isinstance(manual_offsets_ms, Mapping):
        raise TypeError("manual_offsets_ms must be a mapping")
    input_indices = {item.input_index for item in alignment.inputs}
    offsets: dict[int, int] = {}
    for raw_index, raw_offset in manual_offsets_ms.items():
        if isinstance(raw_index, bool) or isinstance(raw_offset, bool):
            raise ValueError(
                "manual offsets must map integer input indexes to integer milliseconds"
            )
        try:
            input_index = operator.index(raw_index)
            offset_ms = operator.index(raw_offset)
        except TypeError as exc:
            raise ValueError(
                "manual offsets must map integer input indexes to integer milliseconds"
            ) from exc
        if input_index not in input_indices:
            raise ValueError(
                f"manual offset input index {input_index} is outside the input range"
            )
        if input_index == alignment.reference_index and offset_ms != 0:
            raise ValueError("the reference input manual offset must be 0")
        offsets[input_index] = offset_ms

    results: list[InputAlignment] = []
    for item in alignment.inputs:
        offset_ms = offsets.get(item.input_index)
        if offset_ms is None or item.input_index == alignment.reference_index:
            results.append(item)
            continue
        # Exact integer rounding keeps very large offsets deterministic.
        scaled, remainder = divmod(offset_ms * alignment.sample_rate, 1000)
        offset_samples = scaled + (1 if remainder * 2 >= 1000 else 0)
        results.append(
            replace(
                item,
                offset_samples=offset_samples,
                offset_ms=float(offset_samples * 1000.0 / alignment.sample_rate),
                method="manual_offset",
                failure=None,
                time_scale=1.0,
                post_correction_error_ms=None,
                max_post_correction_error_ms=None,
                correction_method="manual_offset",
                correction_applied=False,
                requires_manual_review=False,
                warning=None,
                drift_anchors=(),
            )
        )
    return replace(alignment, inputs=tuple(results))


def _finite_setting(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _setting_samples(seconds: float) -> int:
    return int(round(seconds * ANALYSIS_SAMPLE_RATE))


@dataclass(frozen=True)
class MergedAudioConfig:
    """Transition policy used when a channel plan is rendered to one signal.

    Microphones at different positions differ in delay and phase, so blending
    them can cause comb filtering.  Transitions are therefore hard cuts by
    default.  A positive ``crossfade_seconds`` enables a short linear
    crossfade only where both inputs are recorded around the boundary and a
    local correlation search confirms they are sample aligned; every other
    transition remains a hard cut and records why.
    """

    crossfade_seconds: float = 0.0
    verification_window_seconds: float = 0.05
    max_verification_lag_seconds: float = 0.002
    max_aligned_lag_samples: int = 1
    min_crossfade_correlation: float = 0.95
    min_verification_rms: float = 1.0e-3

    def __post_init__(self) -> None:
        crossfade = _finite_setting("crossfade_seconds", self.crossfade_seconds)
        if crossfade < 0.0:
            raise ValueError("crossfade_seconds must be at least 0")
        if crossfade > 0.0 and _setting_samples(crossfade) < 2:
            raise ValueError(
                "crossfade_seconds must be 0 or span at least two samples"
            )
        object.__setattr__(self, "crossfade_seconds", crossfade)

        for name in (
            "verification_window_seconds",
            "max_verification_lag_seconds",
        ):
            value = _finite_setting(name, getattr(self, name))
            if _setting_samples(value) < 1:
                raise ValueError(f"{name} must span at least one sample")
            object.__setattr__(self, name, value)

        if isinstance(self.max_aligned_lag_samples, bool):
            raise ValueError("max_aligned_lag_samples must be an integer")
        try:
            lag = operator.index(self.max_aligned_lag_samples)
        except TypeError as exc:
            raise ValueError("max_aligned_lag_samples must be an integer") from exc
        if lag < 0:
            raise ValueError("max_aligned_lag_samples must be at least 0")
        if lag >= _setting_samples(self.max_verification_lag_seconds):
            raise ValueError(
                "max_verification_lag_seconds must search beyond "
                "max_aligned_lag_samples"
            )
        object.__setattr__(self, "max_aligned_lag_samples", lag)

        correlation = _finite_setting(
            "min_crossfade_correlation", self.min_crossfade_correlation
        )
        if correlation <= 0.0 or correlation > 1.0:
            raise ValueError("min_crossfade_correlation must be in (0, 1]")
        object.__setattr__(self, "min_crossfade_correlation", correlation)

        rms = _finite_setting("min_verification_rms", self.min_verification_rms)
        if rms < 0.0:
            raise ValueError("min_verification_rms must be at least 0")
        object.__setattr__(self, "min_verification_rms", rms)

    @property
    def crossfade_enabled(self) -> bool:
        return self.crossfade_seconds > 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "crossfade_seconds": self.crossfade_seconds,
            "crossfade_enabled": self.crossfade_enabled,
            "verification_window_seconds": self.verification_window_seconds,
            "max_verification_lag_seconds": self.max_verification_lag_seconds,
            "max_aligned_lag_samples": self.max_aligned_lag_samples,
            "min_crossfade_correlation": self.min_crossfade_correlation,
            "min_verification_rms": self.min_verification_rms,
        }


@dataclass(frozen=True)
class RenderedSegment:
    """Audio actually written for one channel-selection segment.

    ``padding_sample_count`` counts samples for which the selected input has
    no recording on the reference timeline; they are written as silence.
    """

    start_sample: int
    end_sample: int
    input_index: int
    recorded_sample_count: int
    padding_sample_count: int

    @property
    def sample_count(self) -> int:
        return self.end_sample - self.start_sample

    def to_dict(self, sample_rate: int) -> dict[str, object]:
        return {
            "start_sample": self.start_sample,
            "end_sample": self.end_sample,
            "start": self.start_sample / sample_rate,
            "end": self.end_sample / sample_rate,
            "input_index": self.input_index,
            "recorded_sample_count": self.recorded_sample_count,
            "padding_sample_count": self.padding_sample_count,
        }


@dataclass(frozen=True)
class RenderedTransition:
    """How the merged signal changes from one selected input to the next.

    ``verification`` explains the method: ``crossfade_disabled``,
    ``segment_too_short``, ``insufficient_context``, ``unrecorded_audio``,
    ``insufficient_signal``, ``low_correlation`` and ``not_sample_aligned``
    keep a hard cut; only ``verified`` permits a crossfade.
    """

    boundary_sample: int
    from_input_index: int
    to_input_index: int
    method: str
    verification: str
    crossfade_start_sample: int | None = None
    crossfade_end_sample: int | None = None
    lag_samples: int | None = None
    correlation: float | None = None

    def to_dict(self, sample_rate: int) -> dict[str, object]:
        return {
            "boundary_sample": self.boundary_sample,
            "boundary": self.boundary_sample / sample_rate,
            "from_input_index": self.from_input_index,
            "to_input_index": self.to_input_index,
            "method": self.method,
            "verification": self.verification,
            "crossfade_start_sample": self.crossfade_start_sample,
            "crossfade_end_sample": self.crossfade_end_sample,
            "lag_samples": self.lag_samples,
            "correlation": self.correlation,
        }


@dataclass(frozen=True)
class MergedAudio:
    """A channel plan rendered as one 16 kHz mono signal on the reference axis."""

    samples: np.ndarray
    sample_rate: int
    reference_index: int
    config: MergedAudioConfig
    segments: tuple[RenderedSegment, ...]
    transitions: tuple[RenderedTransition, ...]
    clipped_sample_count: int

    @property
    def sample_count(self) -> int:
        return int(self.samples.size)

    @property
    def duration_seconds(self) -> float:
        return self.samples.size / self.sample_rate

    @property
    def padding_sample_count(self) -> int:
        return sum(segment.padding_sample_count for segment in self.segments)

    @property
    def crossfade_count(self) -> int:
        return sum(item.method == "crossfade" for item in self.transitions)

    def to_dict(self) -> dict[str, object]:
        """Return rendering diagnostics; the samples themselves are omitted."""

        return {
            "sample_rate": self.sample_rate,
            "channels": 1,
            "frame_count": self.sample_count,
            "duration_seconds": self.duration_seconds,
            "reference_index": self.reference_index,
            "config": self.config.to_dict(),
            "transition_count": len(self.transitions),
            "crossfade_count": self.crossfade_count,
            "padding_sample_count": self.padding_sample_count,
            "clipped_sample_count": self.clipped_sample_count,
            "segments": [
                segment.to_dict(self.sample_rate) for segment in self.segments
            ],
            "transitions": [
                item.to_dict(self.sample_rate) for item in self.transitions
            ],
            "warnings": [
                {
                    "code": "unrecorded_audio_rendered_as_silence",
                    "message": (
                        "The selected input has no recording for part of this "
                        "segment; silence keeps the reference timeline intact."
                    ),
                    "input_index": segment.input_index,
                    "start_sample": segment.start_sample,
                    "end_sample": segment.end_sample,
                    "start": segment.start_sample / self.sample_rate,
                    "end": segment.end_sample / self.sample_rate,
                    "padding_sample_count": segment.padding_sample_count,
                }
                for segment in self.segments
                if segment.padding_sample_count > 0
            ],
        }


@dataclass(frozen=True)
class MultiAudioOutputs:
    """Final, published multi-microphone pre-processing artifacts."""

    output_directory: Path
    merged_audio_file: Path
    alignment_file: Path
    selection_file: Path
    merged_audio: MergedAudio

    def to_dict(self) -> dict[str, str]:
        return {
            "merged_audio_file": str(self.merged_audio_file),
            "alignment_file": str(self.alignment_file),
            "selection_file": str(self.selection_file),
        }


def _validate_render_plan(
    segments: Sequence[ChannelSelectionSegment],
    timeline_sample_count: int,
    input_count: int,
) -> None:
    if not segments:
        raise ValueError("channel selection contains no segments to render")
    expected_start = 0
    for segment in segments:
        if segment.start_sample != expected_start:
            raise ValueError(
                "channel selection segments must cover the timeline without "
                "gaps or overlaps"
            )
        if not 0 <= segment.selected_input_index < input_count:
            raise ValueError("selected_input_index is outside the input range")
        expected_start = segment.end_sample
    if expected_start != timeline_sample_count:
        raise ValueError("channel selection segments must reach the timeline end")


def _zero_mean(values: np.ndarray) -> np.ndarray:
    data = values.astype(np.float64)
    return data - float(np.mean(data))


def _verify_sample_alignment(
    left: AnalysisSignal,
    right: AnalysisSignal,
    boundary: int,
    crossfade_start: int,
    crossfade_end: int,
    config: MergedAudioConfig,
) -> tuple[str, int | None, float | None]:
    """Measure whether two inputs are sample aligned around one boundary."""

    window = max(2, _setting_samples(config.verification_window_seconds))
    max_lag = _setting_samples(config.max_verification_lag_seconds)
    window_start = boundary - window // 2
    window_end = window_start + window
    span_start = min(crossfade_start, window_start - max_lag)
    span_end = max(crossfade_end, window_end + max_lag)
    if span_start < 0 or span_end > left.samples.size:
        return "insufficient_context", None, None
    span = slice(span_start, span_end)
    if not (left.valid_mask[span].all() and right.valid_mask[span].all()):
        return "unrecorded_audio", None, None

    anchor = _zero_mean(left.samples[window_start:window_end])
    anchor_norm = float(np.linalg.norm(anchor))
    peer_at_boundary = _zero_mean(right.samples[window_start:window_end])
    minimum_norm = config.min_verification_rms * math.sqrt(window)
    if (
        anchor_norm <= 0.0
        or anchor_norm < minimum_norm
        or float(np.linalg.norm(peer_at_boundary)) < minimum_norm
    ):
        return "insufficient_signal", None, None

    best_lag: int | None = None
    best_correlation = -math.inf
    for lag in range(-max_lag, max_lag + 1):
        peer = _zero_mean(right.samples[window_start + lag : window_end + lag])
        peer_norm = float(np.linalg.norm(peer))
        if peer_norm <= 0.0:
            continue
        correlation = float(np.dot(anchor, peer) / (anchor_norm * peer_norm))
        if correlation > best_correlation or (
            correlation == best_correlation
            and best_lag is not None
            and abs(lag) < abs(best_lag)
        ):
            best_lag = lag
            best_correlation = correlation
    if best_lag is None:
        return "insufficient_signal", None, None

    best_correlation = max(-1.0, min(1.0, best_correlation))
    if best_correlation < config.min_crossfade_correlation:
        return "low_correlation", best_lag, best_correlation
    if abs(best_lag) > config.max_aligned_lag_samples:
        return "not_sample_aligned", best_lag, best_correlation
    return "verified", best_lag, best_correlation


def _render_transition(
    output: np.ndarray,
    left_segment: ChannelSelectionSegment,
    right_segment: ChannelSelectionSegment,
    signals: Sequence[AnalysisSignal],
    config: MergedAudioConfig,
) -> RenderedTransition:
    boundary = right_segment.start_sample
    from_index = left_segment.selected_input_index
    to_index = right_segment.selected_input_index

    def hard_cut(
        verification: str,
        lag: int | None = None,
        correlation: float | None = None,
    ) -> RenderedTransition:
        return RenderedTransition(
            boundary_sample=boundary,
            from_input_index=from_index,
            to_input_index=to_index,
            method="hard_cut",
            verification=verification,
            lag_samples=lag,
            correlation=correlation,
        )

    if not config.crossfade_enabled:
        return hard_cut("crossfade_disabled")

    length = _setting_samples(config.crossfade_seconds)
    head = length // 2
    # Each fade may use at most half of a neighbouring segment, so adjacent
    # transitions can never overlap or reach past a segment.
    if (
        head > left_segment.sample_count // 2
        or length - head > right_segment.sample_count // 2
    ):
        return hard_cut("segment_too_short")

    start = boundary - head
    end = start + length
    left = signals[from_index]
    right = signals[to_index]
    verification, lag, correlation = _verify_sample_alignment(
        left,
        right,
        boundary,
        start,
        end,
        config,
    )
    if verification != "verified":
        return hard_cut(verification, lag, correlation)

    weights = (np.arange(length, dtype=np.float64) + 0.5) / length
    left_part = left.samples[start:end].astype(np.float64)
    right_part = right.samples[start:end].astype(np.float64)
    output[start:end] = (
        (1.0 - weights) * left_part + weights * right_part
    ).astype(np.float32)
    return RenderedTransition(
        boundary_sample=boundary,
        from_input_index=from_index,
        to_input_index=to_index,
        method="crossfade",
        verification=verification,
        crossfade_start_sample=start,
        crossfade_end_sample=end,
        lag_samples=lag,
        correlation=correlation,
    )


def render_merged_audio(
    aligned_signals: Sequence[AnalysisSignal],
    selection: ChannelSelectionReport,
    *,
    config: MergedAudioConfig | None = None,
) -> MergedAudio:
    """Render a channel plan onto the reference timeline as one mono signal.

    The output has exactly ``selection.timeline_sample_count`` samples.  Each
    sample comes from the selected input of its segment, except inside a
    verified crossfade.  Where the selected input has no recording the output
    is silence and is counted as padding; audio is never borrowed from an
    unselected input, so the rendered audio matches the selection history.
    """

    from .select import ChannelSelectionReport

    if not isinstance(selection, ChannelSelectionReport):
        raise TypeError("selection must be a ChannelSelectionReport")
    settings = config if config is not None else MergedAudioConfig()
    if not isinstance(settings, MergedAudioConfig):
        raise TypeError("config must be a MergedAudioConfig")
    signals = tuple(aligned_signals)
    if any(not isinstance(signal, AnalysisSignal) for signal in signals):
        raise TypeError("aligned_signals must contain AnalysisSignal values")
    if len(signals) != selection.input_count:
        raise ValueError(
            "aligned_signals must contain one signal for every selection input"
        )
    if selection.sample_rate != ANALYSIS_SAMPLE_RATE:
        raise ValueError(
            f"merged audio requires a {ANALYSIS_SAMPLE_RATE} Hz selection timeline"
        )
    timeline_sample_count = selection.timeline_sample_count
    if timeline_sample_count <= 0:
        raise ValueError("an empty timeline cannot be rendered")
    if any(signal.sample_rate != selection.sample_rate for signal in signals):
        raise ValueError("aligned signals must use the selection sample rate")
    if any(signal.samples.size != timeline_sample_count for signal in signals):
        raise ValueError(
            "aligned signals must match the selection timeline length"
        )
    _validate_render_plan(selection.segments, timeline_sample_count, len(signals))

    output = np.zeros(timeline_sample_count, dtype=np.float32)
    segments: list[RenderedSegment] = []
    for segment in selection.segments:
        source = signals[segment.selected_input_index]
        interval = slice(segment.start_sample, segment.end_sample)
        recorded = source.valid_mask[interval]
        output[interval] = np.where(recorded, source.samples[interval], 0.0)
        recorded_count = int(np.count_nonzero(recorded))
        segments.append(
            RenderedSegment(
                start_sample=segment.start_sample,
                end_sample=segment.end_sample,
                input_index=segment.selected_input_index,
                recorded_sample_count=recorded_count,
                padding_sample_count=segment.sample_count - recorded_count,
            )
        )

    transitions = tuple(
        _render_transition(output, left, right, signals, settings)
        for left, right in zip(selection.segments, selection.segments[1:])
        if left.selected_input_index != right.selected_input_index
    )
    return MergedAudio(
        samples=output,
        sample_rate=selection.sample_rate,
        reference_index=selection.reference_index,
        config=settings,
        segments=tuple(segments),
        transitions=transitions,
        clipped_sample_count=int(np.count_nonzero(np.abs(output) > 1.0)),
    )


def write_pcm16_wav(
    path: str | Path,
    samples: np.ndarray,
    *,
    sample_rate: int = ANALYSIS_SAMPLE_RATE,
) -> None:
    """Write mono float samples in [-1, 1] as a 16-bit PCM WAV file."""

    values = np.asarray(samples)
    if values.ndim != 1:
        raise ValueError("WAV samples must be one-dimensional")
    if not np.isfinite(values).all():
        raise ValueError("WAV samples must contain only finite values")
    if isinstance(sample_rate, bool):
        raise ValueError("sample_rate must be a positive integer")
    try:
        rate = operator.index(sample_rate)
    except TypeError as exc:
        raise ValueError("sample_rate must be a positive integer") from exc
    if rate <= 0:
        raise ValueError("sample_rate must be a positive integer")
    if values.size * 2 > _WAV_MAX_DATA_BYTES:
        raise ValueError("audio is too long for a 16-bit PCM WAV file")

    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.setnframes(values.size)
        for start in range(0, values.size, _WAV_WRITE_CHUNK_SAMPLES):
            chunk = np.clip(
                values[start : start + _WAV_WRITE_CHUNK_SAMPLES], -1.0, 1.0
            )
            output.writeframes(
                np.rint(chunk * _PCM16_FULL_SCALE).astype("<i2").tobytes()
            )


def _source_path_policy(policy: object) -> str:
    if policy not in SOURCE_PATH_POLICIES:
        choices = ", ".join(SOURCE_PATH_POLICIES)
        raise ValueError(f"source_path_policy must be one of: {choices}")
    return str(policy)


def _input_labels(
    labels: Sequence[str] | None,
    input_count: int,
) -> tuple[str, ...] | None:
    if labels is None:
        return None
    if isinstance(labels, str):
        raise TypeError("input_labels must be a sequence of strings")
    values = tuple(labels)
    if len(values) != input_count:
        raise ValueError("input_labels must contain one label for every input")
    if any(not isinstance(value, str) for value in values):
        raise TypeError("input_labels must contain only strings")
    return tuple(value.strip() for value in values)


def source_diagnostics(
    prepared: PreparedMultiAudio,
    *,
    reference_index: int,
    input_labels: Sequence[str] | None = None,
    source_path_policy: str = DEFAULT_SOURCE_PATH_POLICY,
) -> list[dict[str, object]]:
    """Describe sources for diagnostics under the requested path policy.

    ``omit`` keeps neither directories nor file names, ``file_name`` keeps the
    base name only, and ``full_path`` keeps the resolved path.  Size, SHA-256
    and decode metadata are always kept so a result can be matched to its
    originals without exposing where they are stored.
    """

    if not isinstance(prepared, PreparedMultiAudio):
        raise TypeError("prepared must be a PreparedMultiAudio")
    policy = _source_path_policy(source_path_policy)
    labels = _input_labels(input_labels, len(prepared.inputs))
    sources: list[dict[str, object]] = []
    for position, item in enumerate(prepared.inputs):
        metadata = item.metadata
        entry: dict[str, object] = {"input_index": metadata.input_index}
        if labels is not None:
            entry["label"] = labels[position]
        entry["is_reference"] = metadata.input_index == reference_index
        if policy == "full_path":
            entry["path"] = str(metadata.path)
        if policy in {"file_name", "full_path"}:
            entry["file_name"] = metadata.file_name
        entry.update(
            {
                "extension": metadata.extension,
                "size_bytes": metadata.size_bytes,
                "sha256": metadata.sha256,
                "duration_seconds": metadata.duration_seconds,
                "decoded_duration_seconds": metadata.decoded_duration_seconds,
                "sample_rate": metadata.sample_rate,
                "channels": metadata.channels,
                "channel_layout": metadata.channel_layout,
                "codec": metadata.codec,
                "container_format": metadata.container_format,
                "analysis_sample_rate": metadata.analysis_sample_rate,
                "analysis_duration_seconds": metadata.analysis_duration_seconds,
                "downmix_method": metadata.downmix_method,
                "resample_method": metadata.resample_method,
            }
        )
        sources.append(entry)
    return sources


def _timeline_sample_count(
    prepared: PreparedMultiAudio,
    reference_index: int,
) -> int:
    if not 0 <= reference_index < len(prepared.inputs):
        raise ValueError("reference_index is outside the input range")
    return int(prepared.inputs[reference_index].samples.size)


def build_alignment_diagnostics(
    prepared: PreparedMultiAudio,
    alignment: MultiAudioAlignment,
    *,
    input_labels: Sequence[str] | None = None,
    source_path_policy: str = DEFAULT_SOURCE_PATH_POLICY,
) -> dict[str, object]:
    """Build the ``alignment.json`` payload.

    ``inputs`` summarizes offset, drift and confidence per input on the
    reference timeline; ``alignment`` keeps the complete window and drift
    anchor evidence.
    """

    if not isinstance(alignment, MultiAudioAlignment):
        raise TypeError("alignment must be a MultiAudioAlignment")
    sources = source_diagnostics(
        prepared,
        reference_index=alignment.reference_index,
        input_labels=input_labels,
        source_path_policy=source_path_policy,
    )
    timeline_sample_count = _timeline_sample_count(
        prepared, alignment.reference_index
    )
    return {
        "version": DIAGNOSTICS_VERSION,
        "kind": "multimic_alignment",
        "sample_rate": alignment.sample_rate,
        "reference_index": alignment.reference_index,
        "timeline_sample_count": timeline_sample_count,
        "duration_seconds": timeline_sample_count / alignment.sample_rate,
        "success": alignment.success,
        "source_path_policy": source_path_policy,
        "sources": sources,
        "preparation_warnings": [
            warning.to_dict() for warning in prepared.warnings
        ],
        "inputs": [
            {
                "input_index": item.input_index,
                "status": item.status,
                "offset_samples": item.offset_samples,
                "offset_ms": item.offset_ms,
                "time_scale": item.time_scale,
                "drift_ppm": item.drift_ppm,
                "confidence": item.confidence,
                "correction_method": item.correction_method,
                "correction_applied": item.correction_applied,
                "requires_manual_review": item.requires_manual_review,
                "failure_code": item.failure_code,
                "warning_code": item.warning_code,
            }
            for item in sorted(alignment.inputs, key=lambda value: value.input_index)
        ],
        "alignment": alignment.to_dict(),
    }


def build_channel_selection_diagnostics(
    prepared: PreparedMultiAudio,
    selection: ChannelSelectionReport,
    merged_audio: MergedAudio,
    *,
    merged_audio_sha256: str | None = None,
    input_labels: Sequence[str] | None = None,
    source_path_policy: str = DEFAULT_SOURCE_PATH_POLICY,
) -> dict[str, object]:
    """Build the ``channel_selection.json`` payload.

    Every selection segment keeps ``start``/``end`` on the reference timeline,
    ``selected_input_index``, ``quality_score``, ``reason`` and
    ``confidence``, plus how many of its samples were recorded audio.
    """

    from .select import ChannelSelectionReport

    if not isinstance(selection, ChannelSelectionReport):
        raise TypeError("selection must be a ChannelSelectionReport")
    if not isinstance(merged_audio, MergedAudio):
        raise TypeError("merged_audio must be a MergedAudio")
    rendered = merged_audio.segments
    if len(rendered) != len(selection.segments) or any(
        item.start_sample != segment.start_sample
        or item.end_sample != segment.end_sample
        or item.input_index != segment.selected_input_index
        for item, segment in zip(rendered, selection.segments)
    ):
        raise ValueError("merged_audio was not rendered from this selection")

    sources = source_diagnostics(
        prepared,
        reference_index=selection.reference_index,
        input_labels=input_labels,
        source_path_policy=source_path_policy,
    )
    payload = selection.to_dict()
    segments = payload["segments"]
    for segment_payload, item in zip(segments, rendered):
        segment_payload["recorded_sample_count"] = item.recorded_sample_count
        segment_payload["padding_sample_count"] = item.padding_sample_count

    rendering = merged_audio.to_dict()
    merged_info: dict[str, object] = {
        "file_name": MERGED_AUDIO_FILE_NAME,
        "sha256": merged_audio_sha256,
        "sample_format": "pcm_s16le",
    }
    for name in (
        "sample_rate",
        "channels",
        "frame_count",
        "duration_seconds",
        "transition_count",
        "crossfade_count",
        "padding_sample_count",
        "clipped_sample_count",
    ):
        merged_info[name] = rendering.pop(name)
    rendering.pop("segments")
    rendering.pop("reference_index")

    return {
        "version": DIAGNOSTICS_VERSION,
        "kind": "multimic_channel_selection",
        "sample_rate": payload["sample_rate"],
        "reference_index": payload["reference_index"],
        "timeline_sample_count": payload["timeline_sample_count"],
        "duration_seconds": payload["duration_seconds"],
        "source_path_policy": source_path_policy,
        "sources": sources,
        "merged_audio": merged_info,
        "config": payload["config"],
        "switch_count": payload["switch_count"],
        "segments": segments,
        "warnings": payload["warnings"],
        "rendering": rendering,
        "reason_descriptions": payload["reason_descriptions"],
        "quality_analysis": payload["quality_analysis"],
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    text = json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8")


def write_multi_audio_outputs(
    output_directory: str | Path,
    prepared: PreparedMultiAudio,
    alignment: MultiAudioAlignment,
    selection: ChannelSelectionReport,
    *,
    aligned_signals: Sequence[AnalysisSignal] | None = None,
    config: MergedAudioConfig | None = None,
    input_labels: Sequence[str] | None = None,
    source_path_policy: str = DEFAULT_SOURCE_PATH_POLICY,
    staging: ResultStaging | None = None,
) -> MultiAudioOutputs:
    """Render and publish the merged WAV and both diagnostic JSON files.

    Everything is first written to a private staging directory inside
    ``output_directory`` (see :mod:`result_staging`) and only moved to the
    final names once all three files are complete; the staging directory is
    always removed.  A final name that would replace a source recording is
    rejected before anything is written, and sources are only ever read.

    A caller that publishes further results of the same job passes its own
    ``staging``: the three files are then left in it under their final names
    for the caller to publish, and the returned paths are the locations they
    will have once published.
    """

    from .select import ChannelSelectionReport

    if not isinstance(prepared, PreparedMultiAudio):
        raise TypeError("prepared must be a PreparedMultiAudio")
    if not isinstance(alignment, MultiAudioAlignment):
        raise TypeError("alignment must be a MultiAudioAlignment")
    if not isinstance(selection, ChannelSelectionReport):
        raise TypeError("selection must be a ChannelSelectionReport")
    policy = _source_path_policy(source_path_policy)
    labels = _input_labels(input_labels, len(prepared.inputs))
    if selection.reference_index != alignment.reference_index:
        raise ValueError("selection and alignment must use the same reference input")
    if selection.input_count != len(prepared.inputs):
        raise ValueError("selection must describe every prepared input")
    if selection.timeline_sample_count != _timeline_sample_count(
        prepared, alignment.reference_index
    ):
        raise ValueError(
            "selection timeline must match the reference input sample count"
        )

    signals = (
        warp_prepared_inputs(prepared, alignment)
        if aligned_signals is None
        else tuple(aligned_signals)
    )
    merged = render_merged_audio(signals, selection, config=config)

    directory = Path(output_directory).expanduser().resolve()
    if staging is not None and staging.output_directory != directory:
        raise ValueError("staging must belong to the output directory")
    final_paths = {
        name: directory / name
        for name in (
            ALIGNMENT_FILE_NAME,
            CHANNEL_SELECTION_FILE_NAME,
            MERGED_AUDIO_FILE_NAME,
        )
    }
    source_keys = {
        os.path.normcase(os.path.abspath(item.metadata.path))
        for item in prepared.inputs
    }
    for path in final_paths.values():
        if os.path.normcase(str(path)) in source_keys:
            raise ValueError(
                f"output file would replace a source recording: {path.name}"
            )

    job_staging = staging or ResultStaging(directory, prefix="multimic")
    try:
        staged_audio = job_staging.path(MERGED_AUDIO_FILE_NAME)
        write_pcm16_wav(staged_audio, merged.samples, sample_rate=merged.sample_rate)
        audio_fingerprint = fingerprint_file(staged_audio)
        _write_json(
            job_staging.path(ALIGNMENT_FILE_NAME),
            build_alignment_diagnostics(
                prepared,
                alignment,
                input_labels=labels,
                source_path_policy=policy,
            ),
        )
        _write_json(
            job_staging.path(CHANNEL_SELECTION_FILE_NAME),
            build_channel_selection_diagnostics(
                prepared,
                selection,
                merged,
                merged_audio_sha256=audio_fingerprint.sha256,
                input_labels=labels,
                source_path_policy=policy,
            ),
        )
        if staging is None:
            # Diagnostics first, audio last: a consumer that waits for the
            # WAV never sees it without the history that explains it.
            job_staging.publish(list(final_paths))
    finally:
        if staging is None:
            job_staging.discard()

    return MultiAudioOutputs(
        output_directory=directory,
        merged_audio_file=final_paths[MERGED_AUDIO_FILE_NAME],
        alignment_file=final_paths[ALIGNMENT_FILE_NAME],
        selection_file=final_paths[CHANNEL_SELECTION_FILE_NAME],
        merged_audio=merged,
    )


@dataclass(frozen=True)
class MultiAudioSelectionPlan:
    """Every stage result of one job, from decoding up to the channel plan.

    ``selection`` includes the manual overrides and ``automatic_selection``
    is the plan without them.  ``aligned_signals`` are the inputs warped onto
    the reference timeline; rendering reuses them instead of warping again.
    """

    prepared: PreparedMultiAudio
    alignment: MultiAudioAlignment
    aligned_signals: tuple[AnalysisSignal, ...]
    quality: ChannelQualityReport
    automatic_selection: ChannelSelectionReport
    selection: ChannelSelectionReport

    @property
    def timeline_sample_count(self) -> int:
        return self.selection.timeline_sample_count


@dataclass(frozen=True)
class MultiAudioPreprocessingResult:
    """A reviewed channel plan and the published merged WAV and diagnostics."""

    plan: MultiAudioSelectionPlan
    outputs: MultiAudioOutputs


def _report_stage(on_stage: Callable[[str], None] | None, stage: str) -> None:
    if on_stage is not None:
        on_stage(stage)


def plan_multi_audio_selection(
    input_paths: Iterable[str | Path],
    *,
    reference_index: int = 0,
    manual_offsets_ms: Mapping[int, int] | None = None,
    window_seconds: float,
    hop_seconds: float,
    overrides: Sequence[Mapping[str, int]] = (),
    on_stage: Callable[[str], None] | None = None,
) -> MultiAudioSelectionPlan:
    """Decode, align, analyze and select channels for one multi-input job.

    Manual offsets replace their inputs' automatic alignment, and manual
    overrides (``start_sample``/``end_sample``/``input_index`` mappings on
    the reference timeline) are applied after the automatic channel plan.
    ``on_stage`` receives each :data:`PREPROCESSING_STAGES` name, except
    ``rendering_merged_audio``, right before that stage starts.

    Raises :class:`MultiAudioPreparationError` with every file failure,
    :class:`UnresolvedAlignmentError` when an input is still unaligned after
    the manual offsets, and :class:`SelectionOverrideError` for an override
    that ends after the reference recording.  Unaligned inputs are never
    silently dropped from the job.
    """

    from .quality import (
        SpeechAnalysisConfig,
        analyze_aligned_speech,
        score_aligned_quality,
    )
    from .select import ChannelSelectionOverride, select_channels

    speech_config = SpeechAnalysisConfig(
        window_seconds=window_seconds,
        hop_seconds=hop_seconds,
    )

    _report_stage(on_stage, "decoding_audio")
    prepared = prepare_multi_audio_inputs(input_paths)
    # Overrides only need the reference length, so a bad one is rejected
    # before the comparatively slow alignment and analysis stages run.
    timeline_sample_count = _timeline_sample_count(prepared, reference_index)
    manual_overrides: list[ChannelSelectionOverride] = []
    for index, item in enumerate(overrides):
        if item["end_sample"] > timeline_sample_count:
            raise SelectionOverrideError(index, timeline_sample_count)
        manual_overrides.append(
            ChannelSelectionOverride(
                item["start_sample"],
                item["end_sample"],
                item["input_index"],
            )
        )

    _report_stage(on_stage, "aligning_audio")
    alignment = apply_manual_alignment_offsets(
        align_prepared_inputs(prepared, reference_index=reference_index),
        manual_offsets_ms or {},
    )
    if alignment.failures:
        raise UnresolvedAlignmentError(alignment)
    aligned_signals = warp_prepared_inputs(prepared, alignment)

    _report_stage(on_stage, "detecting_speech")
    speech_report = analyze_aligned_speech(
        aligned_signals,
        reference_index=alignment.reference_index,
        config=speech_config,
    )

    _report_stage(on_stage, "assessing_channels")
    quality = score_aligned_quality(aligned_signals, speech_report)

    _report_stage(on_stage, "selecting_channels")
    automatic_selection = select_channels(quality)
    return MultiAudioSelectionPlan(
        prepared=prepared,
        alignment=alignment,
        aligned_signals=aligned_signals,
        quality=quality,
        automatic_selection=automatic_selection,
        selection=(
            select_channels(quality, overrides=tuple(manual_overrides))
            if manual_overrides
            else automatic_selection
        ),
    )


def run_multi_audio_preprocessing(
    input_paths: Iterable[str | Path],
    output_directory: str | Path,
    *,
    reference_index: int = 0,
    manual_offsets_ms: Mapping[int, int] | None = None,
    window_seconds: float,
    hop_seconds: float,
    overrides: Sequence[Mapping[str, int]] = (),
    input_labels: Sequence[str] | None = None,
    source_path_policy: str = DEFAULT_SOURCE_PATH_POLICY,
    merged_audio_config: MergedAudioConfig | None = None,
    on_stage: Callable[[str], None] | None = None,
    staging: ResultStaging | None = None,
) -> MultiAudioPreprocessingResult:
    """Run every pre-processing stage and publish the transcription input.

    The result's ``merged_16k_mono.wav`` covers exactly the reference
    recording from its first sample, so timestamps measured on it are
    reference-timeline timestamps.  ``on_stage`` receives every
    :data:`PREPROCESSING_STAGES` name in order; the errors are those of
    :func:`plan_multi_audio_selection`.  With ``staging`` the files stay
    staged for the caller to publish, as in :func:`write_multi_audio_outputs`.
    """

    plan = plan_multi_audio_selection(
        input_paths,
        reference_index=reference_index,
        manual_offsets_ms=manual_offsets_ms,
        window_seconds=window_seconds,
        hop_seconds=hop_seconds,
        overrides=overrides,
        on_stage=on_stage,
    )
    _report_stage(on_stage, "rendering_merged_audio")
    outputs = write_multi_audio_outputs(
        output_directory,
        plan.prepared,
        plan.alignment,
        plan.selection,
        aligned_signals=plan.aligned_signals,
        config=merged_audio_config,
        input_labels=input_labels,
        source_path_policy=source_path_policy,
        staging=staging,
    )
    return MultiAudioPreprocessingResult(plan=plan, outputs=outputs)
