"""Run the MM-DEV-018 overlap/far-field comparison end to end.

1. Align the dataset recordings with the unchanged V1 pipeline.
2. Pick utterances with silent margins on every device (energy VAD),
   transcribe each clean clip on every device and keep the ones whose
   transcripts agree (the pool).
3. Build controlled cases from pairs of different-sounding talkers.
4. Run every method on every case through the product transcription path
   (energy VAD, then Whisper on each speech region) and score it.

Every method shares one Whisper pipeline and one generation configuration, so
methods differ only in the audio they transcribe and in how transcripts are
combined.  Identical audio is transcribed once; its measured Whisper time is
still charged to every method that needs it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import platform
from pathlib import Path
import random
import sys
import time
from typing import Callable, Mapping, Protocol, Sequence

import numpy as np

from evaluation.dataset import EvaluationDataset
from evaluation.metrics import character_tokens, edit_distance
from evaluation.runner import _git_state, _package_versions, file_sha256, write_json

from . import cases as case_module
from .cases import Candidate, CaseSpec, PoolUtterance
from .merge import TimedText, joined_text, merge_transcripts
from .scoring import CaseScore, ReferenceUtterance, score_case, summarize_group
from .signals import (
    SAMPLE_RATE,
    OnnxSeparator,
    active_rms,
    delay_and_sum,
    level_db,
    noise_rms,
    v1_render,
)


SCHEMA_VERSION = 1
RESULTS_FILE_NAME = "results.json"
BASELINE = "v1"
ORACLE = "oracle_single"
BEAMFORM = "beamform_das"
MERGE = "merge_channels"
MERGE_THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.9)


@dataclass(frozen=True)
class ExperimentConfig:
    seed: int = 20261027
    device: str = "CPU"
    vad_offset_db: float = 10.0
    vad_padding_seconds: float = 0.1
    margin_seconds: float = 0.4
    min_core_seconds: float = 0.6
    max_core_seconds: float = 8.0
    short_max_seconds: float = 2.0
    max_short_candidates: int = 200
    max_other_candidates: int = 200
    min_level_over_noise_db: float = 12.0
    max_agreement_cer: float = 0.3
    max_similarity: float = 0.25
    target_level_dbfs: float = -26.0
    overlap_pairs: int = 12
    far_small_pairs: int = 10
    single_per_position: int = 8
    merge_tolerance_seconds: float = 1.0
    merge_duplicate_threshold: float = 0.7

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class Transcriber(Protocol):
    def transcribe(self, samples: np.ndarray, source: str) -> tuple[list[TimedText], float]:
        """Segments on the samples' own timeline and the Whisper seconds they cost."""


class WhisperTranscriber:
    """The product transcription path: energy VAD regions, then Whisper.

    Uses the dataset's bridge settings (model, language, beams, window,
    hotwords, initial prompt, silence skipping) on ``device``.
    """

    def __init__(self, settings: Mapping[str, object], device: str) -> None:
        import transcribe_npu as engine

        self._engine = engine
        self._settings = settings
        self.pipe = engine.load_whisper_pipeline(
            Path(str(settings["model_dir"])), device, str(settings.get("model_label") or "")
        )
        hotwords = engine.read_optional_text(Path(str(settings["hotwords_file"])))
        prompt = engine.prepare_initial_prompt(
            self.pipe, engine.read_optional_text(Path(str(settings["initial_prompt_file"])))
        )
        self.config = engine.configure_generation(
            self.pipe,
            str(settings["language"]),
            int(settings["beams"]),
            hotwords,
            initial_prompt=prompt.value,
        )
        self._cache: dict[str, tuple[list[TimedText], float]] = {}
        self.whisper_calls = 0

    def _regions(self, samples: np.ndarray) -> list:
        from vad import SpeechRegion, detect_speech_regions

        settings = self._settings
        if not settings.get("skip_silence", True):
            return [SpeechRegion(0, samples.size)]
        return detect_speech_regions(
            samples,
            SAMPLE_RATE,
            threshold_db=float(settings["silence_threshold_db"]),
            min_speech_duration=float(settings["silence_min_speech_duration"]),
            min_silence_duration=float(settings["silence_min_duration"]),
            padding_duration=float(settings["silence_padding_duration"]),
        )

    def transcribe(self, samples: np.ndarray, source: str) -> tuple[list[TimedText], float]:
        from backend_bridge import transcribe_speech_regions

        values = np.ascontiguousarray(samples, dtype=np.float32)
        key = hashlib.sha1(values.tobytes()).hexdigest()
        if key not in self._cache:
            started = time.perf_counter()
            regions = self._regions(values)
            segments = (
                transcribe_speech_regions(
                    self._engine,
                    self.pipe,
                    self.config,
                    values,
                    regions,
                    float(self._settings["window_seconds"]),
                    float(self._settings["overlap_seconds"]),
                )
                if regions
                else []
            )
            self.whisper_calls += len(regions)
            self._cache[key] = (
                [TimedText(float(item.start), float(item.end), item.text) for item in segments],
                time.perf_counter() - started,
            )
        segments, seconds = self._cache[key]
        return [replace(segment, source=source) for segment in segments], seconds


class SpeakerEmbedder:
    """Speaker embeddings from the diarization model already used by the engine."""

    def __init__(self, model_path: str | Path) -> None:
        import sherpa_onnx

        self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model_path), num_threads=4)
        )

    def embed(self, samples: np.ndarray) -> tuple[float, ...]:
        stream = self._extractor.create_stream()
        stream.accept_waveform(SAMPLE_RATE, np.ascontiguousarray(samples, dtype=np.float32))
        stream.input_finished()
        values = np.asarray(self._extractor.compute(stream), dtype=np.float64)
        return tuple(float(value) for value in values / max(np.linalg.norm(values), 1e-12))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sample_range(start: float, end: float) -> slice:
    return slice(int(round(start * SAMPLE_RATE)), int(round(end * SAMPLE_RATE)))


def character_disagreement(reference: str, other: str) -> float:
    tokens = character_tokens(reference)
    return edit_distance(tokens, character_tokens(other)) / max(1, len(tokens))


def sample_candidates(candidates: Sequence[Candidate], config: ExperimentConfig, rng: random.Random) -> list[Candidate]:
    shorts = [item for item in candidates if item.core_seconds <= config.short_max_seconds]
    others = [item for item in candidates if item.core_seconds > config.short_max_seconds]
    rng.shuffle(shorts)
    rng.shuffle(others)
    chosen = shorts[: config.max_short_candidates] + others[: config.max_other_candidates]
    return sorted(chosen, key=lambda item: item.core_start)


def build_pool(
    candidates: Sequence[Candidate],
    channels: Sequence[np.ndarray],
    *,
    noise_floor_db: Sequence[float],
    reference_channel: int,
    transcriber: Transcriber,
    embedder: SpeakerEmbedder,
    config: ExperimentConfig,
    progress: Callable[[str], None] = lambda _message: None,
) -> tuple[list[PoolUtterance], dict[str, list[np.ndarray]], dict[str, object]]:
    pool: list[PoolUtterance] = []
    clips: dict[str, list[np.ndarray]] = {}
    rejected = {"level": 0, "empty": 0, "disagreement": 0}
    asr_seconds = 0.0
    for number, candidate in enumerate(candidates, start=1):
        clip = [np.asarray(channel[_sample_range(candidate.clip_start, candidate.clip_end)], np.float32) for channel in channels]
        core = clip[reference_channel][_sample_range(candidate.core_offset, candidate.core_offset + candidate.core_seconds)]
        if level_db(active_rms(core)) - noise_floor_db[reference_channel] < config.min_level_over_noise_db:
            rejected["level"] += 1
            continue
        texts = []
        for channel_index, values in enumerate(clip):
            segments, seconds = transcriber.transcribe(values, f"clean_{channel_index}")
            asr_seconds += seconds
            texts.append(joined_text(segments))
        reference_text = texts[reference_channel]
        if len(character_tokens(reference_text)) < 2:
            rejected["empty"] += 1
            continue
        agreement = max(
            character_disagreement(reference_text, text)
            for index, text in enumerate(texts)
            if index != reference_channel
        )
        if agreement > config.max_agreement_cer:
            rejected["disagreement"] += 1
            continue
        pool.append(
            PoolUtterance(candidate, reference_text, reference_channel, tuple(texts), agreement, embedder.embed(core))
        )
        clips[candidate.candidate_id] = clip
        if number % 20 == 0:
            progress(f"pool {number}/{len(candidates)}: {len(pool)} accepted")
    stats = {
        "candidates_transcribed": len(candidates) - rejected["level"],
        "candidates_considered": len(candidates),
        "rejected": rejected,
        "accepted": len(pool),
        "noise_floor_dbfs": list(noise_floor_db),
        "asr_seconds": asr_seconds,
    }
    return pool, clips, stats


def method_names(channel_count: int, separators: Sequence[OnnxSeparator]) -> list[str]:
    names = [f"single_{index}" for index in range(channel_count)] + [ORACLE, BASELINE, BEAMFORM, MERGE]
    for separator in separators:
        names += [f"separation_{separator.name}", f"v1_plus_separation_{separator.name}"]
    return names


def run_case_methods(
    channels: Sequence[np.ndarray],
    *,
    reference_channel: int,
    transcriber: Transcriber,
    separators: Sequence[OnnxSeparator],
    config: ExperimentConfig,
    selection_window_seconds: float,
    selection_hop_seconds: float,
) -> tuple[dict[str, list[TimedText]], dict[str, dict[str, float]], dict[str, object]]:
    """Transcripts, costs and diagnostics of every method except the oracle."""

    outputs: dict[str, list[TimedText]] = {}
    costs: dict[str, dict[str, float]] = {}
    details: dict[str, object] = {}

    def cost(asr: float, dsp: float, streams: int) -> dict[str, float]:
        return {"asr_seconds": asr, "dsp_seconds": dsp, "asr_streams": streams}

    singles = []
    for index, channel in enumerate(channels):
        segments, seconds = transcriber.transcribe(channel, f"single_{index}")
        outputs[f"single_{index}"] = segments
        costs[f"single_{index}"] = cost(seconds, 0.0, 1)
        singles.append(seconds)

    started = time.perf_counter()
    v1 = v1_render(
        channels,
        reference_index=reference_channel,
        window_seconds=selection_window_seconds,
        hop_seconds=selection_hop_seconds,
    )
    v1_dsp = time.perf_counter() - started
    outputs[BASELINE], v1_asr = transcriber.transcribe(v1.samples, BASELINE)
    costs[BASELINE] = cost(v1_asr, v1_dsp, 1)
    details["v1"] = {
        "switch_count": v1.switch_count,
        "input_seconds": v1.input_seconds(len(channels)),
        "segments": [list(segment) for segment in v1.segments],
    }

    started = time.perf_counter()
    beam = delay_and_sum(channels, reference_index=reference_channel)
    beam_dsp = time.perf_counter() - started
    outputs[BEAMFORM], beam_asr = transcriber.transcribe(beam.samples, BEAMFORM)
    costs[BEAMFORM] = cost(beam_asr, beam_dsp, 1)
    details["beamform"] = {"delays_samples": list(beam.delays), "weights": list(beam.weights)}

    merged, decisions = merge_transcripts(
        outputs[BASELINE],
        [outputs[f"single_{index}"] for index in range(len(channels))],
        tolerance_seconds=config.merge_tolerance_seconds,
        duplicate_threshold=config.merge_duplicate_threshold,
    )
    outputs[MERGE] = merged
    costs[MERGE] = cost(v1_asr + sum(singles), v1_dsp, 1 + len(channels))
    details["merge_added"] = sum(decision.added for decision in decisions)

    for separator in separators:
        started = time.perf_counter()
        streams = separator.separate(v1.samples)
        separation_dsp = time.perf_counter() - started
        energies = [float(np.mean(np.square(stream, dtype=np.float64))) for stream in streams]
        order = sorted(range(len(streams)), key=lambda index: -energies[index])
        stream_segments = []
        stream_asr = 0.0
        for rank, index in enumerate(order):
            segments, seconds = transcriber.transcribe(streams[index], f"{separator.name}_{rank}")
            stream_segments.append(segments)
            stream_asr += seconds
        name = f"separation_{separator.name}"
        outputs[name], _ = merge_transcripts(
            stream_segments[0],
            stream_segments[1:],
            tolerance_seconds=config.merge_tolerance_seconds,
            duplicate_threshold=config.merge_duplicate_threshold,
        )
        costs[name] = cost(stream_asr, v1_dsp + separation_dsp, len(streams))
        plus = f"v1_plus_separation_{separator.name}"
        outputs[plus], _ = merge_transcripts(
            outputs[BASELINE],
            stream_segments,
            tolerance_seconds=config.merge_tolerance_seconds,
            duplicate_threshold=config.merge_duplicate_threshold,
        )
        costs[plus] = cost(v1_asr + stream_asr, v1_dsp + separation_dsp, 1 + len(streams))
        details[f"separation_{separator.name}_energy_db"] = [
            float(10.0 * np.log10(max(energies[index], 1e-20))) for index in order
        ]
        details[f"separation_{separator.name}_streams"] = [
            [segment.to_dict() for segment in segments] for segments in stream_segments
        ]
    return outputs, costs, details


def score_methods(
    references: Sequence,
    outputs: Mapping[str, Sequence[TimedText]],
    channel_count: int,
) -> tuple[dict[str, CaseScore], str]:
    scores = {name: score_case(references, joined_text(segments)) for name, segments in outputs.items()}
    singles = [f"single_{index}" for index in range(channel_count)]
    oracle = min(singles, key=lambda name: (scores[name].counts.errors, singles.index(name)))
    scores[ORACLE] = scores[oracle]
    return scores, oracle


def _environment(settings: Mapping[str, object], separators: Sequence[OnnxSeparator], config: ExperimentConfig) -> dict[str, object]:
    return {
        "platform": platform.platform(),
        "processor": platform.processor(),
        "python": sys.executable,
        "python_version": platform.python_version(),
        "packages": _package_versions(),
        "git": _git_state(),
        "device": config.device,
        "model_dir": str(settings["model_dir"]),
        "separation_models": [
            {"name": separator.name, "path": str(separator.model_path), "sha256": file_sha256(separator.model_path),
             "size_bytes": separator.model_path.stat().st_size}
            for separator in separators
        ],
    }


def _whisper_settings(settings: Mapping[str, object]) -> dict[str, object]:
    keys = (
        "model_dir", "language", "beams", "window_seconds", "overlap_seconds", "hotwords_file",
        "initial_prompt_file", "skip_silence", "silence_threshold_db", "silence_min_speech_duration",
        "silence_min_duration", "silence_padding_duration",
    )
    return {key: settings[key] for key in keys if key in settings}


def summarize(case_results: Sequence[Mapping[str, object]], methods: Sequence[str]) -> dict[str, object]:
    groups: dict[str, list[Mapping[str, CaseScore]]] = {}
    for result in case_results:
        scores = result["_scores"]
        groups.setdefault(str(result["group"]), []).append(scores)
        groups.setdefault(f"{result['category']}/ALL", []).append(scores)
    return {
        group: summarize_group(rows, methods, baseline=BASELINE)
        for group, rows in sorted(groups.items())
    }


def merge_threshold_sensitivity(
    case_results: Sequence[Mapping[str, object]],
    channel_count: int,
    config: ExperimentConfig,
) -> dict[str, object]:
    """Channel merging re-scored at other duplicate thresholds from the stored transcripts."""

    sensitivity: dict[str, object] = {}
    for threshold in MERGE_THRESHOLDS:
        rows: dict[str, list[Mapping[str, CaseScore]]] = {}
        for result in case_results:
            outputs = result["_outputs"]
            merged, _ = merge_transcripts(
                outputs[BASELINE],
                [outputs[f"single_{index}"] for index in range(channel_count)],
                tolerance_seconds=config.merge_tolerance_seconds,
                duplicate_threshold=threshold,
            )
            references = [ReferenceUtterance(**item) for item in result["references"]]
            row = {BASELINE: result["_scores"][BASELINE], MERGE: score_case(references, joined_text(merged))}
            rows.setdefault(str(result["category"]), []).append(row)
        sensitivity[f"{threshold:.1f}"] = {
            category: summarize_group(items, [BASELINE, MERGE], baseline=BASELINE)[MERGE]
            for category, items in sorted(rows.items())
        }
    return sensitivity


def cost_summary(case_results: Sequence[Mapping[str, object]], methods: Sequence[str]) -> dict[str, object]:
    audio_seconds = sum(float(result["duration_seconds"]) for result in case_results)
    summary: dict[str, object] = {"audio_seconds": audio_seconds}
    for method in methods:
        if method == ORACLE:
            continue
        items = [result["costs"][method] for result in case_results]
        summary[method] = {
            "asr_seconds_per_audio_second": sum(item["asr_seconds"] for item in items) / audio_seconds,
            "dsp_seconds_per_audio_second": sum(item["dsp_seconds"] for item in items) / audio_seconds,
            "asr_streams_per_case": sum(item["asr_streams"] for item in items) / len(items),
        }
    return summary


def run_experiment(
    dataset: EvaluationDataset,
    output_dir: Path,
    *,
    config: ExperimentConfig,
    separator_models: Mapping[str, Path],
    transcriber: Transcriber | None = None,
    embedder: SpeakerEmbedder | None = None,
    progress: Callable[[str], None] = lambda message: print(message, file=sys.stderr, flush=True),
) -> dict[str, object]:
    from multimic.pipeline import plan_multi_audio_selection

    output_dir.mkdir(parents=True, exist_ok=False)
    started_at = _utc_now()
    rng = random.Random(config.seed)
    settings = dataset.settings

    progress("aligning recordings with the V1 pipeline")
    plan = plan_multi_audio_selection(
        [item.path for item in dataset.inputs],
        reference_index=dataset.reference_index,
        manual_offsets_ms=dataset.manual_offsets_ms,
        window_seconds=dataset.window_seconds,
        hop_seconds=dataset.hop_seconds,
    )
    channels = [signal.samples for signal in plan.aligned_signals]
    valid = [signal.valid_mask for signal in plan.aligned_signals]
    timeline_seconds = channels[0].size / SAMPLE_RATE

    noise_floor_db = [level_db(noise_rms(channel)) for channel in channels]
    speech = case_module.detect_any_channel_speech(
        channels,
        noise_floor_db,
        offset_db=config.vad_offset_db,
        min_silence_seconds=config.margin_seconds,
        padding_seconds=config.vad_padding_seconds,
    )
    candidates = case_module.select_candidates(
        speech,
        timeline_seconds=timeline_seconds,
        margin_seconds=config.margin_seconds,
        min_core_seconds=config.min_core_seconds,
        max_core_seconds=config.max_core_seconds,
    )
    candidates = [
        item for item in candidates
        if all(mask[_sample_range(item.clip_start, item.clip_end)].all() for mask in valid)
    ]
    sampled = sample_candidates(candidates, config, rng)
    progress(f"{len(speech)} speech regions, {len(candidates)} isolated utterances, {len(sampled)} sampled")

    separators = [OnnxSeparator(name, path, config.device) for name, path in separator_models.items()]
    if transcriber is None:
        transcriber = WhisperTranscriber(settings, config.device)
    if embedder is None:
        embedder = SpeakerEmbedder(settings["diarization_embedding_model"])

    pool, clips, pool_stats = build_pool(
        sampled,
        channels,
        noise_floor_db=noise_floor_db,
        reference_channel=dataset.reference_index,
        transcriber=transcriber,
        embedder=embedder,
        config=config,
        progress=progress,
    )
    pool_by_id = {item.utterance_id: item for item in pool}
    progress(f"pool: {len(pool)} utterances")

    specs: list[CaseSpec] = case_module.build_cases(
        pool,
        rng,
        channel_count=len(channels),
        reference_channel=dataset.reference_index,
        overlap_pairs=config.overlap_pairs,
        far_small_pairs=config.far_small_pairs,
        single_per_position=config.single_per_position,
        max_similarity=config.max_similarity,
        short_max_seconds=config.short_max_seconds,
    )
    methods = method_names(len(channels), separators)
    case_results: list[dict[str, object]] = []
    for number, spec in enumerate(specs, start=1):
        mixed = case_module.synthesize(spec, clips, target_level_dbfs=config.target_level_dbfs)
        references = case_module.case_references(spec, pool_by_id)
        outputs, costs, details = run_case_methods(
            mixed,
            reference_channel=dataset.reference_index,
            transcriber=transcriber,
            separators=separators,
            config=config,
            selection_window_seconds=dataset.window_seconds,
            selection_hop_seconds=dataset.hop_seconds,
        )
        scores, oracle = score_methods(references, outputs, len(channels))
        case_results.append(
            {
                **spec.to_dict(),
                "references": [asdict(reference) for reference in references],
                "oracle_choice": oracle,
                "details": details,
                "costs": costs,
                "methods": {
                    name: {
                        "text": joined_text(outputs[name]) if name in outputs else None,
                        "segments": [segment.to_dict() for segment in outputs.get(name, [])],
                        "score": scores[name].to_dict(),
                    }
                    for name in methods
                },
                "_scores": scores,
                "_outputs": outputs,
            }
        )
        if number % 10 == 0 or number == len(specs):
            progress(f"cases {number}/{len(specs)}")

    results = {
        "schema_version": SCHEMA_VERSION,
        "ticket": "MM-DEV-018",
        "started_at_utc": started_at,
        "finished_at_utc": _utc_now(),
        "config": config.to_dict(),
        "dataset": {
            "dataset_file": str(dataset.path) if dataset.path else None,
            "dataset_id": dataset.dataset_id,
            "authorization_status": "verified" if dataset.authorization.get("approved_by") else "unverified",
            "inputs": [{"path": str(item.path), "label": item.label, "sha256": file_sha256(item.path)} for item in dataset.inputs],
            "reference_index": dataset.reference_index,
        },
        "alignment": [
            {"input_index": item.input_index, "offset_samples": item.offset_samples, "drift_ppm": item.drift_ppm}
            for item in plan.alignment.inputs
        ],
        "speech_regions": len(speech),
        "isolated_utterances": len(candidates),
        "whisper_settings": _whisper_settings(settings),
        "environment": _environment(settings, separators, config),
        "pool": {**pool_stats, "utterances": [item.to_dict() for item in pool]},
        "methods": methods,
        "cases": [{key: value for key, value in result.items() if not key.startswith("_")} for result in case_results],
        "summary": summarize(case_results, methods),
        "merge_threshold_sensitivity": merge_threshold_sensitivity(case_results, len(channels), config),
        "cost": cost_summary(case_results, methods),
    }
    write_json(output_dir / RESULTS_FILE_NAME, results)
    return results
