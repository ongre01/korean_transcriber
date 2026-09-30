#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
WINDOW_SIZE = 160000
WINDOW_SHIFT = 16000
RECEPTIVE_FIELD_SIZE = 991
RECEPTIVE_FIELD_SHIFT = 270

# pyannote segmentation-3.0 powerset classes:
# silence, A, B, C, A+B, A+C, B+C
POWERSET_MAPPING = np.asarray(
    [
        [0, 0, 0],
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, 1],
        [1, 1, 0],
        [1, 0, 1],
        [0, 1, 1],
    ],
    dtype=np.int32,
)


@dataclass(frozen=True)
class SpeakerTurn:
    start: float
    end: float
    speaker: int


def _compile_segmentation_model(
    model_path: Path,
    requested_device: str,
    *,
    fallback_to_cpu: bool,
):
    import openvino as ov

    core = ov.Core()
    model = core.read_model(str(model_path))
    if len(model.inputs) != 1:
        raise RuntimeError(
            f'Expected one speaker-segmentation input, got {len(model.inputs)}.'
        )

    # The released ONNX model has dynamic N/T axes. Intel NPU requires static
    # shapes, and pyannote segmentation-3.0 operates on fixed 10-second windows.
    model.reshape([1, 1, WINDOW_SIZE])

    requested = requested_device.strip().upper() or 'NPU'
    available = [str(d).upper() for d in core.available_devices]
    target = requested
    target_available = any(
        d == target or d.startswith(target + '.') for d in available
    )

    if not target_available and target != 'CPU':
        if not fallback_to_cpu:
            raise RuntimeError(
                f'OpenVINO diarization device {requested} was not found. '
                f'Available devices: {core.available_devices}'
            )
        print(
            f'      [WARN] Diarization device {requested} is unavailable; '
            'falling back to CPU.'
        )
        target = 'CPU'

    try:
        return core.compile_model(model, target), target
    except Exception as exc:
        if target == 'CPU' or not fallback_to_cpu:
            raise RuntimeError(
                f'Failed to compile speaker segmentation on {target}: {exc}'
            ) from exc

        print(f'      [WARN] Speaker segmentation could not compile on {target}.')
        print(f'             {exc}')
        print('             Falling back to OpenVINO CPU.')
        return core.compile_model(model, 'CPU'), 'CPU'


def _windowed_audio(audio: np.ndarray) -> list[np.ndarray]:
    n = int(audio.size)
    if n <= 0:
        return []

    if n <= WINDOW_SIZE:
        chunk = np.zeros(WINDOW_SIZE, dtype=np.float32)
        chunk[:n] = audio
        return [chunk]

    num_chunks = (n - WINDOW_SIZE) // WINDOW_SHIFT + 1
    has_last_chunk = ((n - WINDOW_SIZE) % WINDOW_SHIFT) > 0
    chunks: list[np.ndarray] = []

    for index in range(num_chunks):
        start = index * WINDOW_SHIFT
        chunks.append(
            np.ascontiguousarray(
                audio[start:start + WINDOW_SIZE], dtype=np.float32
            )
        )

    if has_last_chunk:
        start = num_chunks * WINDOW_SHIFT
        chunk = np.zeros(WINDOW_SIZE, dtype=np.float32)
        remaining = audio[start:]
        chunk[:remaining.size] = remaining
        chunks.append(chunk)

    return chunks


def _run_segmentation(audio: np.ndarray, compiled_model) -> list[np.ndarray]:
    chunks = _windowed_audio(audio)
    outputs: list[np.ndarray] = []

    for index, chunk in enumerate(chunks, start=1):
        x = chunk.reshape(1, 1, WINDOW_SIZE)
        result = compiled_model([x])
        logits = np.asarray(
            result[compiled_model.output(0)], dtype=np.float32
        )

        if logits.ndim != 3 or logits.shape[0] != 1:
            raise RuntimeError(
                f'Unexpected speaker segmentation output shape: {logits.shape}'
            )

        frame_logits = np.ascontiguousarray(logits[0], dtype=np.float32)
        if frame_logits.shape[1] != POWERSET_MAPPING.shape[0]:
            raise RuntimeError(
                'Unexpected pyannote class count: '
                f'{frame_logits.shape[1]} (expected {POWERSET_MAPPING.shape[0]}).'
            )

        outputs.append(frame_logits)
        print(
            f'\r      Segmentation: {index}/{len(chunks)}',
            end='',
            flush=True,
        )

    if chunks:
        print()
    return outputs


def _to_multilabel(logits: np.ndarray) -> np.ndarray:
    class_ids = np.argmax(logits, axis=1)
    return np.ascontiguousarray(POWERSET_MAPPING[class_ids], dtype=np.int32)


def _global_frame_count(num_chunks: int) -> int:
    return (
        (WINDOW_SIZE + (num_chunks - 1) * WINDOW_SHIFT)
        // RECEPTIVE_FIELD_SHIFT
        + 1
    )


def _chunk_frame_start(chunk_index: int) -> int:
    return int(chunk_index * WINDOW_SHIFT / RECEPTIVE_FIELD_SHIFT + 0.5)


def _compute_speakers_per_frame(labels: list[np.ndarray]) -> np.ndarray:
    num_frames = _global_frame_count(len(labels))
    count = np.zeros(num_frames, dtype=np.float32)
    weight = np.zeros(num_frames, dtype=np.float32)

    for index, label in enumerate(labels):
        start = _chunk_frame_start(index)
        end = min(num_frames, start + label.shape[0])
        rows = end - start
        if rows <= 0:
            continue
        count[start:end] += label[:rows].sum(axis=1, dtype=np.int32)
        weight[start:end] += 1.0

    return np.asarray(count / (weight + 1e-12) + 0.5, dtype=np.int32)


def _exclude_overlap(labels: list[np.ndarray]) -> list[np.ndarray]:
    result: list[np.ndarray] = []
    for label in labels:
        single = np.zeros_like(label, dtype=np.int32)
        mask = label.sum(axis=1) < 2
        single[mask] = label[mask]
        result.append(single)
    return result


def _active_sample_ranges(
    activity: np.ndarray,
    *,
    sample_offset: int,
    num_frames: int,
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    active = False
    start_index = 0

    for index, value in enumerate(activity):
        if value:
            if not active:
                active = True
                start_index = index
        elif active:
            active = False
            start_sample = int(
                start_index / num_frames * WINDOW_SIZE + sample_offset
            )
            end_sample = int(index / num_frames * WINDOW_SIZE + sample_offset)
            ranges.append((start_sample, end_sample))

    if active:
        start_sample = int(
            start_index / num_frames * WINDOW_SIZE + sample_offset
        )
        end_sample = int(
            (num_frames - 1) / num_frames * WINDOW_SIZE + sample_offset
        )
        ranges.append((start_sample, end_sample))

    return ranges


def _chunk_speaker_sample_indexes(
    labels: list[np.ndarray],
) -> tuple[list[tuple[int, int]], list[list[tuple[int, int]]]]:
    no_overlap = _exclude_overlap(labels)
    pairs: list[tuple[int, int]] = []
    sample_ranges: list[list[tuple[int, int]]] = []

    for chunk_index, label in enumerate(no_overlap):
        num_frames = label.shape[0]
        sample_offset = chunk_index * WINDOW_SHIFT

        for local_speaker in range(label.shape[1]):
            activity = label[:, local_speaker]
            if int(activity.sum()) < 10:
                continue

            pairs.append((chunk_index, local_speaker))
            sample_ranges.append(
                _active_sample_ranges(
                    activity,
                    sample_offset=sample_offset,
                    num_frames=num_frames,
                )
            )

    return pairs, sample_ranges


def _compute_embeddings(
    audio: np.ndarray,
    embedding_model: Path,
    pairs: list[tuple[int, int]],
    sample_ranges: list[list[tuple[int, int]]],
) -> tuple[list[tuple[int, int]], np.ndarray]:
    import sherpa_onnx

    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=str(embedding_model),
        num_threads=1,
        debug=False,
        provider='cpu',
    )
    if not config.validate():
        raise RuntimeError(
            f'Invalid speaker embedding model configuration: {embedding_model}'
        )

    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
    valid_pairs: list[tuple[int, int]] = []
    embeddings: list[np.ndarray] = []

    for index, (pair, ranges) in enumerate(zip(pairs, sample_ranges), start=1):
        stream = extractor.create_stream()
        for start, end in ranges:
            start = max(0, min(int(audio.size), start))
            end = max(start, min(int(audio.size), end))
            if end > start:
                stream.accept_waveform(
                    sample_rate=SAMPLE_RATE,
                    waveform=np.ascontiguousarray(
                        audio[start:end], dtype=np.float32
                    ),
                )

        stream.input_finished()
        if extractor.is_ready(stream):
            embedding = np.asarray(
                extractor.compute(stream), dtype=np.float32
            ).reshape(-1)
            if embedding.size and np.all(np.isfinite(embedding)):
                valid_pairs.append(pair)
                embeddings.append(embedding)

        print(
            f'\r      Embedding   : {index}/{len(pairs)}',
            end='',
            flush=True,
        )

    if pairs:
        print()

    if not embeddings:
        return [], np.empty((0, 0), dtype=np.float32)

    return valid_pairs, np.ascontiguousarray(
        np.vstack(embeddings), dtype=np.float32
    )


def _cluster_embeddings(
    embeddings: np.ndarray,
    *,
    num_speakers: int,
    cluster_threshold: float,
) -> list[int]:
    import sherpa_onnx

    if embeddings.ndim != 2 or embeddings.shape[0] == 0:
        return []

    if num_speakers > embeddings.shape[0]:
        raise RuntimeError(
            f'--num-speakers={num_speakers} exceeds the number of usable '
            f'speaker embeddings ({embeddings.shape[0]}).'
        )

    config = sherpa_onnx.FastClusteringConfig(
        num_clusters=num_speakers,
        threshold=cluster_threshold,
    )
    if not config.validate():
        raise RuntimeError(f'Invalid clustering configuration: {config}')

    clustering = sherpa_onnx.FastClustering(config)
    return [int(x) for x in clustering(embeddings)]


def _relabel(
    labels: list[np.ndarray],
    pair_to_cluster: dict[tuple[int, int], int],
    num_clusters: int,
) -> list[np.ndarray]:
    relabeled: list[np.ndarray] = []

    for chunk_index, label in enumerate(labels):
        new_label = np.zeros((label.shape[0], num_clusters), dtype=np.int32)
        for local_speaker in range(label.shape[1]):
            cluster = pair_to_cluster.get((chunk_index, local_speaker))
            if cluster is None or cluster < 0 or cluster >= num_clusters:
                continue
            new_label[label[:, local_speaker] == 1, cluster] = 1
        relabeled.append(new_label)

    return relabeled


def _compute_speaker_count(
    labels: list[np.ndarray], num_samples: int
) -> np.ndarray:
    num_frames = _global_frame_count(len(labels))
    count = np.zeros((num_frames, labels[0].shape[1]), dtype=np.int32)

    for index, label in enumerate(labels):
        start = _chunk_frame_start(index)
        end = min(num_frames, start + label.shape[0])
        rows = end - start
        if rows > 0:
            count[start:end] += label[:rows]

    has_last_chunk = ((num_samples - WINDOW_SIZE) % WINDOW_SHIFT) > 0
    if not has_last_chunk:
        return count

    last_frame = min(num_samples // RECEPTIVE_FIELD_SHIFT, count.shape[0] - 1)
    return count[:last_frame + 1]


def _finalize_labels(
    count: np.ndarray, speakers_per_frame: np.ndarray
) -> np.ndarray:
    final = np.zeros_like(count, dtype=np.int32)
    rows = min(count.shape[0], speakers_per_frame.size)

    for row in range(rows):
        k = int(speakers_per_frame[row])
        if k <= 0:
            continue
        k = min(k, count.shape[1])
        top = np.argsort(count[row], kind='stable')[-k:]
        final[row, top] = 1

    return final


def _merge_turns(
    turns: list[SpeakerTurn], *, min_duration_off: float
) -> list[SpeakerTurn]:
    if not turns:
        return []

    merged: list[SpeakerTurn] = [turns[0]]
    for turn in turns[1:]:
        previous = merged[-1]
        if (
            turn.speaker == previous.speaker
            and turn.start - previous.end <= min_duration_off
        ):
            merged[-1] = SpeakerTurn(
                previous.start,
                max(previous.end, turn.end),
                previous.speaker,
            )
        else:
            merged.append(turn)
    return merged


def _labels_to_turns(
    final_labels: np.ndarray,
    *,
    duration: float,
    min_duration_on: float,
    min_duration_off: float,
) -> list[SpeakerTurn]:
    if final_labels.size == 0:
        return []

    scale = RECEPTIVE_FIELD_SHIFT / SAMPLE_RATE
    scale_offset = 0.5 * RECEPTIVE_FIELD_SIZE / SAMPLE_RATE
    all_turns: list[SpeakerTurn] = []

    for speaker in range(final_labels.shape[1]):
        activity = final_labels[:, speaker]
        speaker_turns: list[SpeakerTurn] = []
        active = bool(activity[0])
        start_index = 0 if active else -1

        for frame_index in range(1, activity.size):
            if active and activity[frame_index] == 0:
                start = min(duration, start_index * scale + scale_offset)
                end = min(duration, frame_index * scale + scale_offset)
                if end > start:
                    speaker_turns.append(SpeakerTurn(start, end, speaker))
                active = False
            elif not active and activity[frame_index] == 1:
                active = True
                start_index = frame_index

        if active:
            start = min(duration, start_index * scale + scale_offset)
            end = min(
                duration,
                (activity.size - 1) * scale + scale_offset,
            )
            if end > start:
                speaker_turns.append(SpeakerTurn(start, end, speaker))

        speaker_turns = _merge_turns(
            speaker_turns, min_duration_off=min_duration_off
        )
        all_turns.extend(
            turn
            for turn in speaker_turns
            if turn.end - turn.start > min_duration_on
        )

    return sorted(
        all_turns,
        key=lambda turn: (turn.start, turn.end, turn.speaker),
    )


def _one_chunk_turns(
    label: np.ndarray,
    *,
    num_samples: int,
    min_duration_on: float,
    min_duration_off: float,
) -> list[SpeakerTurn]:
    frames_to_keep = min(
        label.shape[0], max(1, num_samples // RECEPTIVE_FIELD_SHIFT)
    )
    return _labels_to_turns(
        label[:frames_to_keep],
        duration=num_samples / SAMPLE_RATE,
        min_duration_on=min_duration_on,
        min_duration_off=min_duration_off,
    )


def diarize_audio(
    audio: np.ndarray,
    segmentation_model: Path,
    embedding_model: Path,
    *,
    num_speakers: int = -1,
    cluster_threshold: float = 0.5,
    min_duration_on: float = 0.3,
    min_duration_off: float = 0.5,
    device: str = 'NPU',
    fallback_to_cpu: bool = True,
) -> list[SpeakerTurn]:
    """Hybrid diarization: OpenVINO segmentation + sherpa embedding/clustering."""
    segmentation_model = Path(segmentation_model).expanduser().resolve()
    embedding_model = Path(embedding_model).expanduser().resolve()

    if not segmentation_model.is_file():
        raise RuntimeError(
            f'Speaker segmentation model not found: {segmentation_model}\n'
            'Run: python download_models.py diarization'
        )
    if not embedding_model.is_file():
        raise RuntimeError(
            f'Speaker embedding model not found: {embedding_model}\n'
            'Run: python download_models.py diarization'
        )

    samples = np.ascontiguousarray(audio, dtype=np.float32).reshape(-1)
    if samples.size == 0:
        return []

    compiled_model, actual_device = _compile_segmentation_model(
        segmentation_model,
        device,
        fallback_to_cpu=fallback_to_cpu,
    )

    print(f'      Speaker segmentation: {actual_device} (OpenVINO)')
    print('      Speaker embedding   : CPU (sherpa-onnx)')
    print('      Speaker clustering  : CPU (sherpa-onnx)')

    segmentations = _run_segmentation(samples, compiled_model)
    labels = [_to_multilabel(item) for item in segmentations]
    if not labels:
        return []

    if len(labels) == 1:
        return _one_chunk_turns(
            labels[0],
            num_samples=samples.size,
            min_duration_on=min_duration_on,
            min_duration_off=min_duration_off,
        )

    speakers_per_frame = _compute_speakers_per_frame(labels)
    if speakers_per_frame.size == 0 or int(speakers_per_frame.max()) == 0:
        return []

    pairs, sample_ranges = _chunk_speaker_sample_indexes(labels)
    valid_pairs, embeddings = _compute_embeddings(
        samples, embedding_model, pairs, sample_ranges
    )
    cluster_labels = _cluster_embeddings(
        embeddings,
        num_speakers=num_speakers,
        cluster_threshold=cluster_threshold,
    )
    if not cluster_labels:
        return []

    pair_to_cluster = dict(zip(valid_pairs, cluster_labels))
    num_clusters = max(cluster_labels) + 1
    relabeled = _relabel(labels, pair_to_cluster, num_clusters)
    count = _compute_speaker_count(relabeled, samples.size)
    final_labels = _finalize_labels(count, speakers_per_frame)

    return _labels_to_turns(
        final_labels,
        duration=samples.size / SAMPLE_RATE,
        min_duration_on=min_duration_on,
        min_duration_off=min_duration_off,
    )
