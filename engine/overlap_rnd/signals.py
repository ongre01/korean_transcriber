"""Signal-level methods compared in MM-DEV-018.

All signals are 16 kHz mono float32 arrays already on one timeline (the V1
alignment output).  ``v1_render`` runs the unchanged V1 speech analysis,
quality scoring, channel selection and rendering; the other functions are the
candidate alternatives.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Sequence

import numpy as np


SAMPLE_RATE = 16000
FRAME_SECONDS = 0.03
ACTIVE_FRAME_SHARE = 0.5
NOISE_PERCENTILE = 10.0
DEFAULT_MAX_DELAY_SECONDS = 0.02


def db_to_gain(value_db: float) -> float:
    return float(10.0 ** (value_db / 20.0))


def _frame_powers(samples: np.ndarray, frame_samples: int) -> np.ndarray:
    count = samples.size // frame_samples
    if count == 0:
        return np.array([float(np.mean(np.square(samples, dtype=np.float64)))]) if samples.size else np.zeros(1)
    frames = np.asarray(samples[: count * frame_samples], dtype=np.float64).reshape(count, frame_samples)
    return np.mean(frames * frames, axis=1)


def active_rms(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> float:
    """RMS of the louder half of 30 ms frames, a simple speech-level estimate."""

    powers = np.sort(_frame_powers(samples, max(1, int(round(FRAME_SECONDS * sample_rate)))))
    active = powers[int(len(powers) * (1.0 - ACTIVE_FRAME_SHARE)) :]
    return math.sqrt(float(np.mean(active))) if active.size else 0.0


def noise_rms(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> float:
    """RMS of the 10th-percentile 30 ms frame power (noise-floor estimate)."""

    powers = _frame_powers(samples, max(1, int(round(FRAME_SECONDS * sample_rate))))
    return math.sqrt(float(np.percentile(powers, NOISE_PERCENTILE)))


def level_db(rms: float) -> float:
    return 20.0 * math.log10(max(rms, 1e-12))


def gcc_phat_delay(reference: np.ndarray, other: np.ndarray, max_delay_samples: int) -> int:
    """Delay ``d`` of ``other`` against ``reference`` (``other[t] ~ reference[t - d]``)."""

    size = 1 << int(math.ceil(math.log2(max(2, reference.size + other.size))))
    cross = np.fft.rfft(other, size) * np.conj(np.fft.rfft(reference, size))
    cross /= np.maximum(np.abs(cross), 1e-12)
    correlation = np.fft.irfft(cross, size)
    lags = np.concatenate((correlation[-max_delay_samples:], correlation[: max_delay_samples + 1]))
    return int(np.argmax(lags)) - max_delay_samples


def shift(samples: np.ndarray, delay: int) -> np.ndarray:
    """Advance ``samples`` by ``delay`` samples (``out[t] = samples[t + delay]``), zero filled."""

    output = np.zeros_like(samples)
    if delay >= 0:
        output[: samples.size - delay] = samples[delay:]
    else:
        output[-delay:] = samples[: samples.size + delay]
    return output


@dataclass(frozen=True)
class BeamformResult:
    samples: np.ndarray
    delays: tuple[int, ...]
    weights: tuple[float, ...]


def delay_and_sum(
    channels: Sequence[np.ndarray],
    *,
    reference_index: int = 0,
    max_delay_seconds: float = DEFAULT_MAX_DELAY_SECONDS,
    sample_rate: int = SAMPLE_RATE,
) -> BeamformResult:
    """Noise-normalized delay-and-sum toward the dominant source.

    Each channel is advanced by its GCC-PHAT delay to the reference channel
    and weighted by the inverse of its noise-floor RMS, so a noisy device
    contributes less.  With two devices this is the only beamformer that
    needs no array geometry; it cannot null a second talker.
    """

    reference = np.asarray(channels[reference_index], dtype=np.float32)
    max_delay = max(1, int(round(max_delay_seconds * sample_rate)))
    delays = tuple(
        0 if index == reference_index else gcc_phat_delay(reference, np.asarray(channel, np.float32), max_delay)
        for index, channel in enumerate(channels)
    )
    inverse_noise = np.array([1.0 / max(noise_rms(np.asarray(channel)), 1e-9) for channel in channels])
    weights = inverse_noise / inverse_noise.sum()
    output = np.zeros(reference.size, dtype=np.float64)
    for channel, delay, weight in zip(channels, delays, weights):
        output += weight * shift(np.asarray(channel, dtype=np.float32), delay)
    return BeamformResult(output.astype(np.float32), delays, tuple(float(weight) for weight in weights))


@dataclass(frozen=True)
class V1Result:
    samples: np.ndarray
    switch_count: int
    segments: tuple[tuple[float, float, int], ...]

    def input_seconds(self, input_count: int) -> list[float]:
        totals = [0.0] * input_count
        for start, end, index in self.segments:
            totals[index] += end - start
        return totals


def v1_render(
    channels: Sequence[np.ndarray],
    *,
    reference_index: int = 0,
    window_seconds: float = 3.0,
    hop_seconds: float = 1.0,
) -> V1Result:
    """Run the unchanged V1 analysis, selection and rendering on aligned channels."""

    from multimic.pipeline import AnalysisSignal, render_merged_audio
    from multimic.quality import SpeechAnalysisConfig, analyze_aligned_speech, score_aligned_quality
    from multimic.select import select_channels

    signals = [
        AnalysisSignal(np.asarray(channel, dtype=np.float32), np.ones(len(channel), dtype=bool))
        for channel in channels
    ]
    speech = analyze_aligned_speech(
        signals,
        reference_index=reference_index,
        config=SpeechAnalysisConfig(window_seconds=window_seconds, hop_seconds=hop_seconds),
    )
    selection = select_channels(score_aligned_quality(signals, speech))
    merged = render_merged_audio(signals, selection)
    return V1Result(
        samples=merged.samples,
        switch_count=selection.switch_count,
        segments=tuple(
            (
                segment.start_sample / SAMPLE_RATE,
                segment.end_sample / SAMPLE_RATE,
                segment.selected_input_index,
            )
            for segment in selection.segments
        ),
    )


class OnnxSeparator:
    """Two-speaker separation model run by OpenVINO from an ONNX file.

    OpenVINO is already an engine dependency, so the only addition is the
    model file.  The separated streams are rescaled together so that their
    sum has the mixture's power; their relative level is kept.
    """

    def __init__(self, name: str, model_path: str | Path, device: str = "CPU") -> None:
        import openvino as ov

        self.name = name
        self.model_path = Path(model_path)
        core = ov.Core()
        self._compiled = core.compile_model(core.read_model(str(self.model_path)), device)

    def separate(self, mixture: np.ndarray) -> list[np.ndarray]:
        values = np.ascontiguousarray(mixture, dtype=np.float32)
        result = self._compiled([values[None, :]])
        outputs = [np.squeeze(np.asarray(result[output])) for output in self._compiled.outputs]
        if len(outputs) == 1 and outputs[0].ndim == 2:
            streams = [outputs[0][index] for index in range(outputs[0].shape[0])]
        else:
            streams = [output.reshape(-1) for output in outputs]
        fitted = []
        for stream in streams:
            stream = np.asarray(stream[: values.size], dtype=np.float64)
            fitted.append(np.pad(stream, (0, values.size - stream.size)))
        total = np.sum(fitted, axis=0)
        total_power = float(np.mean(total * total))
        scale = math.sqrt(float(np.mean(values.astype(np.float64) ** 2)) / total_power) if total_power > 0 else 1.0
        return [np.clip(stream * scale, -1.0, 1.0).astype(np.float32) for stream in fitted]
