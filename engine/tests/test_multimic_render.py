from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import wave

import numpy as np


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import multimic
from multimic.align import InputAlignment, MultiAudioAlignment
from multimic.pipeline import (
    ALIGNMENT_FILE_NAME,
    ANALYSIS_SAMPLE_RATE,
    CHANNEL_SELECTION_FILE_NAME,
    MERGED_AUDIO_FILE_NAME,
    AnalysisSignal,
    MergedAudioConfig,
    PreparedAudioInput,
    PreparedMultiAudio,
    align_prepared_inputs,
    fingerprint_file,
    prepare_multi_audio_inputs,
    render_merged_audio,
    source_diagnostics,
    warp_prepared_inputs,
    write_multi_audio_outputs,
    write_pcm16_wav,
)
from multimic.quality import (
    SpeechAnalysisConfig,
    analyze_aligned_quality,
    analyze_prepared_quality,
)
from multimic.select import (
    SELECTION_REASON_DESCRIPTIONS,
    ChannelSelectionOverride,
    select_channels,
)
from test_multimic_align import metadata, speech_like_master
import transcribe_npu


SAMPLE_RATE = ANALYSIS_SAMPLE_RATE


def strict_json(path: Path) -> dict[str, object]:
    def reject(value: str) -> object:
        raise ValueError(f"non-JSON number: {value}")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject)


def write_source_wav(path: Path, samples: np.ndarray) -> None:
    pcm = np.rint(np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(pcm.tobytes())


def read_wav(path: Path) -> tuple[wave._wave_params, np.ndarray]:
    with wave.open(str(path), "rb") as source:
        params = source.getparams()
        frames = source.readframes(params.nframes)
    return params, np.frombuffer(frames, dtype="<i2")


def colored_noise(seconds: float, *, seed: int) -> np.ndarray:
    count = int(round(seconds * SAMPLE_RATE))
    rng = np.random.default_rng(seed)
    kernel = np.hanning(9)
    kernel /= np.sum(kernel)
    values = np.convolve(rng.normal(0.0, 1.0, count + 16), kernel, mode="same")
    values = values[:count] / max(float(np.std(values)), 1.0e-12)
    return np.asarray(0.1 * values, dtype=np.float32)


def signal(samples: np.ndarray, mask: np.ndarray | None = None) -> AnalysisSignal:
    values = np.asarray(samples, dtype=np.float32)
    if mask is None:
        mask = np.ones(values.size, dtype=np.bool_)
    return AnalysisSignal(values, mask, SAMPLE_RATE)


def selection_speech_config() -> SpeechAnalysisConfig:
    return SpeechAnalysisConfig(
        window_seconds=1.0,
        hop_seconds=1.0,
        threshold_db=-40.0,
        min_speech_duration=0.2,
        min_silence_duration=0.1,
        padding_duration=0.0,
    )


def select_with_overrides(
    signals: tuple[AnalysisSignal, ...],
    overrides: tuple[ChannelSelectionOverride, ...] = (),
):
    quality = analyze_aligned_quality(
        signals,
        speech_config=selection_speech_config(),
    )
    return select_channels(quality, overrides=overrides)


def identity_alignment(input_count: int) -> MultiAudioAlignment:
    return MultiAudioAlignment(
        reference_index=0,
        sample_rate=SAMPLE_RATE,
        inputs=tuple(
            InputAlignment(
                input_index=index,
                reference_index=0,
                offset_samples=0,
                offset_ms=0.0,
                confidence=1.0,
                method="test_identity",
                failure=None,
                selected_window_count=0,
                accepted_window_count=0,
                consistent_window_count=0,
                offset_dispersion_ms=None,
                time_scale=1.0,
            )
            for index in range(input_count)
        ),
        reference_windows=(),
    )


def prepared_from_signals(
    signals: tuple[AnalysisSignal, ...],
    paths: tuple[Path, ...] | None = None,
) -> PreparedMultiAudio:
    inputs = []
    for index, item in enumerate(signals):
        item_metadata = metadata(index, item.samples.size)
        if paths is not None:
            item_metadata = type(item_metadata)(
                **{
                    **item_metadata.__dict__,
                    "path": paths[index],
                    "file_name": paths[index].name,
                }
            )
        inputs.append(PreparedAudioInput(metadata=item_metadata, analysis=item))
    return PreparedMultiAudio(
        inputs=tuple(inputs),
        warnings=(),
        analysis_sample_rate=SAMPLE_RATE,
    )


def transition_at(merged, boundary: int):
    return next(
        item for item in merged.transitions if item.boundary_sample == boundary
    )


class MergedOutputEndToEndTests(unittest.TestCase):
    def test_offset_recordings_render_reference_timeline_and_keep_sources(
        self,
    ) -> None:
        master = speech_like_master(seed=77, duration_seconds=16.0)
        start = 3 * SAMPLE_RATE
        length = 9 * SAMPLE_RATE
        half = SAMPLE_RATE // 2
        reference = master[start : start + length]
        later = (
            0.65 * master[start + half : start + half + length]
            + np.random.default_rng(5).normal(0.0, 0.003, length)
        ).astype(np.float32)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_directory = root / "private_sources"
            output_directory = root / "results" / "meeting_001"
            source_directory.mkdir()
            source_paths = (
                source_directory / "private_name_a.wav",
                source_directory / "private_name_b.wav",
            )
            write_source_wav(source_paths[0], reference)
            write_source_wav(source_paths[1], later)
            before = {path: fingerprint_file(path) for path in source_paths}

            prepared = prepare_multi_audio_inputs(source_paths)
            alignment = align_prepared_inputs(prepared)
            self.assertTrue(alignment.success)
            quality = analyze_prepared_quality(prepared, alignment)
            selection = select_channels(quality)
            aligned = warp_prepared_inputs(prepared, alignment)

            outputs = write_multi_audio_outputs(
                output_directory,
                prepared,
                alignment,
                selection,
                aligned_signals=aligned,
                input_labels=("테이블 중앙", "발표자 근처"),
            )

            timeline = prepared.inputs[0].samples.size
            self.assertEqual(outputs.output_directory, output_directory.resolve())
            self.assertEqual(outputs.merged_audio_file.name, MERGED_AUDIO_FILE_NAME)
            self.assertEqual(outputs.alignment_file.name, ALIGNMENT_FILE_NAME)
            self.assertEqual(
                outputs.selection_file.name,
                CHANNEL_SELECTION_FILE_NAME,
            )
            self.assertEqual(
                sorted(path.name for path in output_directory.iterdir()),
                sorted(
                    (
                        ALIGNMENT_FILE_NAME,
                        CHANNEL_SELECTION_FILE_NAME,
                        MERGED_AUDIO_FILE_NAME,
                    )
                ),
            )

            # The result is a 16 kHz mono PCM WAV on the reference timeline.
            params, pcm = read_wav(outputs.merged_audio_file)
            self.assertEqual(params.nchannels, 1)
            self.assertEqual(params.sampwidth, 2)
            self.assertEqual(params.framerate, 16_000)
            self.assertEqual(params.nframes, timeline)
            merged = outputs.merged_audio.samples
            np.testing.assert_array_equal(
                pcm,
                np.rint(np.clip(merged, -1.0, 1.0) * 32767.0).astype("<i2"),
            )

            # The existing transcription decoder opens it unchanged.
            decoded = transcribe_npu.decode_audio_16k_mono(
                outputs.merged_audio_file
            )
            self.assertEqual(decoded.size, timeline)
            np.testing.assert_allclose(decoded, merged, atol=1.0e-4)

            # Every sample comes from the segment's selected aligned input.
            for segment in selection.segments:
                source = aligned[segment.selected_input_index]
                interval = slice(segment.start_sample, segment.end_sample)
                np.testing.assert_array_equal(
                    merged[interval],
                    np.where(
                        source.valid_mask[interval],
                        source.samples[interval],
                        0.0,
                    ).astype(np.float32),
                )

            # MM-06: before the later device starts, only the reference was
            # recording, and that part is kept as real audio, not silence.
            later_start = int(np.flatnonzero(aligned[1].valid_mask)[0])
            self.assertGreater(later_start, SAMPLE_RATE // 4)
            for segment in selection.segments:
                if segment.start_sample < later_start:
                    self.assertEqual(segment.selected_input_index, 0)
            np.testing.assert_array_equal(
                merged[:later_start],
                prepared.inputs[0].samples[:later_start],
            )
            self.assertGreater(float(np.max(np.abs(merged[:later_start]))), 0.05)
            self.assertEqual(outputs.merged_audio.padding_sample_count, 0)

            for path in source_paths:
                self.assertEqual(fingerprint_file(path), before[path])

            merged_sha256 = fingerprint_file(outputs.merged_audio_file).sha256
            alignment_text = outputs.alignment_file.read_text(encoding="utf-8")
            selection_text = outputs.selection_file.read_text(encoding="utf-8")
            alignment_payload = strict_json(outputs.alignment_file)
            selection_payload = strict_json(outputs.selection_file)

        # Default privacy policy: no source directory or file name is stored.
        for text in (alignment_text, selection_text):
            self.assertNotIn("private_name", text)
            self.assertNotIn("private_sources", text)
            self.assertNotIn(directory.replace("\\", "\\\\"), text)

        self.assertEqual(alignment_payload["kind"], "multimic_alignment")
        self.assertEqual(alignment_payload["sample_rate"], 16_000)
        self.assertEqual(alignment_payload["timeline_sample_count"], timeline)
        self.assertEqual(alignment_payload["source_path_policy"], "omit")
        self.assertEqual(
            [item["label"] for item in alignment_payload["sources"]],
            ["테이블 중앙", "발표자 근처"],
        )
        self.assertEqual(
            [item["sha256"] for item in alignment_payload["sources"]],
            [before[path].sha256 for path in source_paths],
        )
        summaries = alignment_payload["inputs"]
        self.assertEqual([item["input_index"] for item in summaries], [0, 1])
        self.assertEqual(summaries[0]["offset_samples"], 0)
        self.assertEqual(
            summaries[1]["offset_samples"],
            alignment.inputs[1].offset_samples,
        )
        for item in summaries:
            for key in ("offset_ms", "drift_ppm", "time_scale", "confidence"):
                self.assertIn(key, item)
        self.assertEqual(alignment_payload["alignment"], alignment.to_dict())

        self.assertEqual(selection_payload["kind"], "multimic_channel_selection")
        self.assertEqual(selection_payload["timeline_sample_count"], timeline)
        merged_info = selection_payload["merged_audio"]
        self.assertEqual(merged_info["file_name"], MERGED_AUDIO_FILE_NAME)
        self.assertEqual(merged_info["frame_count"], timeline)
        self.assertEqual(merged_info["channels"], 1)
        self.assertEqual(merged_info["sample_format"], "pcm_s16le")
        self.assertEqual(merged_info["sha256"], merged_sha256)
        segments = selection_payload["segments"]
        self.assertEqual(segments[0]["start_sample"], 0)
        self.assertEqual(segments[-1]["end_sample"], timeline)
        for previous, current in zip(segments, segments[1:]):
            self.assertEqual(previous["end_sample"], current["start_sample"])
        used_reasons = set()
        for item in segments:
            for key in (
                "start",
                "end",
                "selected_input_index",
                "quality_score",
                "reason",
                "confidence",
            ):
                self.assertIn(key, item)
            self.assertEqual(item["start"], item["start_sample"] / 16_000)
            self.assertEqual(item["end"], item["end_sample"] / 16_000)
            self.assertEqual(
                item["recorded_sample_count"] + item["padding_sample_count"],
                item["end_sample"] - item["start_sample"],
            )
            used_reasons.update(item["reason_codes"])
        self.assertEqual(set(selection_payload["reason_descriptions"]), used_reasons)
        self.assertEqual(
            selection_payload["rendering"]["config"]["crossfade_enabled"],
            False,
        )
        for item in selection_payload["rendering"]["transitions"]:
            self.assertEqual(item["method"], "hard_cut")
            self.assertEqual(item["verification"], "crossfade_disabled")

    def test_speech_heard_by_one_microphone_survives_rendering(self) -> None:
        # MM-05: input 0 alone hears the second utterance.
        count = 5 * SAMPLE_RATE
        first = np.zeros(count, dtype=np.float32)
        second = np.zeros(count, dtype=np.float32)

        def add_tone(target, start, end, amplitude, frequency):
            time = np.arange(end - start, dtype=np.float64) / SAMPLE_RATE
            level = np.where(
                (np.arange(end - start) // (SAMPLE_RATE // 10)) % 2 == 0,
                1.0,
                0.25,
            )
            target[start:end] = amplitude * level * np.sin(
                2.0 * np.pi * frequency * time
            )

        add_tone(first, 1_600, 12_800, 0.15, 120.0)
        add_tone(second, 1_600, 12_800, 0.24, 120.0)
        add_tone(first, 17_600, 24_000, 0.20, 180.0)
        signals = (signal(first), signal(second))

        selection = select_with_overrides(signals)
        merged = render_merged_audio(signals, selection)

        unique = next(
            candidate
            for candidate in selection.quality_report.speech_report.candidates
            if candidate.status == "single_input"
        )
        self.assertEqual(unique.supporting_input_indices, (0,))
        interval = slice(unique.start_sample, unique.end_sample)
        np.testing.assert_array_equal(merged.samples[interval], first[interval])
        self.assertGreater(float(np.max(np.abs(merged.samples[interval]))), 0.1)
        self.assertEqual(merged.sample_count, count)


class TransitionRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = colored_noise(6.0, seed=11)
        self.left_boundary = 2 * SAMPLE_RATE
        self.right_boundary = 4 * SAMPLE_RATE
        # Both sides are pinned so the boundary does not depend on how the
        # automatic plan scores these noise-only fixtures.
        self.override = (
            ChannelSelectionOverride(0, self.left_boundary, 0),
            ChannelSelectionOverride(self.left_boundary, self.right_boundary, 1),
        )

    def test_default_transitions_are_hard_cuts_without_blending(self) -> None:
        signals = (signal(self.base), signal(0.5 * self.base))
        selection = select_with_overrides(signals, self.override)

        merged = render_merged_audio(signals, selection)

        transition = transition_at(merged, self.left_boundary)
        self.assertEqual(transition.method, "hard_cut")
        self.assertEqual(transition.verification, "crossfade_disabled")
        self.assertIsNone(transition.crossfade_start_sample)
        boundary = self.left_boundary
        np.testing.assert_array_equal(
            merged.samples[boundary - 200 : boundary],
            self.base[boundary - 200 : boundary],
        )
        np.testing.assert_array_equal(
            merged.samples[boundary : boundary + 200],
            (0.5 * self.base)[boundary : boundary + 200].astype(np.float32),
        )
        self.assertEqual(merged.crossfade_count, 0)

    def test_verified_sample_aligned_transition_uses_crossfade(self) -> None:
        second = (0.5 * self.base).astype(np.float32)
        signals = (signal(self.base), signal(second))
        selection = select_with_overrides(signals, self.override)
        config = MergedAudioConfig(crossfade_seconds=0.01)

        merged = render_merged_audio(signals, selection, config=config)

        transition = transition_at(merged, self.left_boundary)
        self.assertEqual(transition.method, "crossfade")
        self.assertEqual(transition.verification, "verified")
        self.assertEqual(transition.lag_samples, 0)
        self.assertGreater(transition.correlation, 0.99)
        start = transition.crossfade_start_sample
        end = transition.crossfade_end_sample
        self.assertEqual((start, end), (self.left_boundary - 80, self.left_boundary + 80))
        weights = (np.arange(160, dtype=np.float64) + 0.5) / 160
        np.testing.assert_allclose(
            merged.samples[start:end],
            (1.0 - weights) * self.base[start:end] + weights * second[start:end],
            atol=1.0e-6,
        )
        np.testing.assert_array_equal(
            merged.samples[start - 100 : start], self.base[start - 100 : start]
        )
        np.testing.assert_array_equal(
            merged.samples[end : end + 100], second[end : end + 100]
        )
        self.assertEqual(merged.sample_count, self.base.size)

    def test_crossfade_is_refused_for_unverified_transitions(self) -> None:
        delayed = np.concatenate(
            (np.zeros(8, dtype=np.float32), self.base[:-8])
        )
        cases = (
            (delayed, "not_sample_aligned"),
            (colored_noise(6.0, seed=99), "low_correlation"),
            (np.zeros_like(self.base), "insufficient_signal"),
        )
        config = MergedAudioConfig(crossfade_seconds=0.01)
        for second, expected in cases:
            with self.subTest(expected=expected):
                signals = (signal(self.base), signal(second))
                selection = select_with_overrides(signals, self.override)

                merged = render_merged_audio(signals, selection, config=config)

                transition = transition_at(merged, self.left_boundary)
                self.assertEqual(transition.method, "hard_cut")
                self.assertEqual(transition.verification, expected)
                boundary = self.left_boundary
                np.testing.assert_array_equal(
                    merged.samples[boundary - 100 : boundary],
                    self.base[boundary - 100 : boundary],
                )
                np.testing.assert_array_equal(
                    merged.samples[boundary : boundary + 100],
                    second[boundary : boundary + 100],
                )

    def test_short_segment_is_never_crossfaded(self) -> None:
        signals = (signal(self.base), signal(0.5 * self.base))
        override = (
            ChannelSelectionOverride(self.left_boundary, self.left_boundary + 100, 1),
        )
        selection = select_with_overrides(signals, override)

        merged = render_merged_audio(
            signals, selection, config=MergedAudioConfig(crossfade_seconds=0.01)
        )

        self.assertEqual(
            transition_at(merged, self.left_boundary).verification,
            "segment_too_short",
        )

    def test_unrecorded_selected_audio_is_silence_and_reported(self) -> None:
        mask = np.ones(self.base.size, dtype=np.bool_)
        gap_end = self.left_boundary + SAMPLE_RATE // 2
        mask[self.left_boundary - 100 : gap_end] = False
        second = np.where(mask, 0.5 * self.base, 0.0).astype(np.float32)
        signals = (signal(self.base), signal(second, mask))
        selection = select_with_overrides(signals, self.override)

        merged = render_merged_audio(
            signals, selection, config=MergedAudioConfig(crossfade_seconds=0.01)
        )

        self.assertEqual(merged.sample_count, self.base.size)
        np.testing.assert_array_equal(
            merged.samples[self.left_boundary : gap_end],
            np.zeros(gap_end - self.left_boundary, dtype=np.float32),
        )
        self.assertEqual(
            merged.padding_sample_count, gap_end - self.left_boundary
        )
        self.assertEqual(
            transition_at(merged, self.left_boundary).verification,
            "unrecorded_audio",
        )
        warnings = merged.to_dict()["warnings"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["code"], "unrecorded_audio_rendered_as_silence")
        self.assertEqual(warnings[0]["input_index"], 1)

    def test_render_rejects_inputs_that_do_not_match_the_plan(self) -> None:
        signals = (signal(self.base), signal(0.5 * self.base))
        selection = select_with_overrides(signals, self.override)

        with self.assertRaisesRegex(ValueError, "timeline length"):
            render_merged_audio(
                (signals[0], signal(self.base[:-1])), selection
            )
        with self.assertRaisesRegex(ValueError, "one signal"):
            render_merged_audio(signals[:1], selection)
        with self.assertRaises(TypeError):
            render_merged_audio(signals, selection.quality_report)

    def test_configuration_is_validated(self) -> None:
        with self.assertRaises(ValueError):
            MergedAudioConfig(crossfade_seconds=-0.01)
        with self.assertRaises(ValueError):
            MergedAudioConfig(crossfade_seconds=1.0e-6)
        with self.assertRaises(ValueError):
            MergedAudioConfig(max_aligned_lag_samples=32)
        with self.assertRaises(ValueError):
            MergedAudioConfig(min_crossfade_correlation=0.0)
        with self.assertRaises(ValueError):
            MergedAudioConfig(crossfade_seconds=float("nan"))
        self.assertFalse(MergedAudioConfig().crossfade_enabled)


class OutputFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signals = (
            signal(colored_noise(3.0, seed=3)),
            signal(colored_noise(3.0, seed=4)),
        )
        self.alignment = identity_alignment(2)
        self.selection = select_with_overrides(self.signals)

    def test_pcm16_writer_clips_to_full_scale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "clip.wav"
            write_pcm16_wav(path, np.array([1.5, -2.0, 0.5, 0.0], dtype=np.float32))
            params, pcm = read_wav(path)

        self.assertEqual((params.nchannels, params.sampwidth), (1, 2))
        np.testing.assert_array_equal(pcm, [32767, -32767, 16384, 0])
        with self.assertRaises(ValueError):
            write_pcm16_wav(Path("unused.wav"), np.array([np.nan]))

    def test_source_path_policy_controls_stored_locations(self) -> None:
        paths = (
            Path("D:/meetings/kim/mic_a.wav"),
            Path("D:/meetings/kim/mic_b.wav"),
        )
        prepared = prepared_from_signals(self.signals, paths)

        omitted = source_diagnostics(prepared, reference_index=0)
        names = source_diagnostics(
            prepared, reference_index=0, source_path_policy="file_name"
        )
        full = source_diagnostics(
            prepared, reference_index=0, source_path_policy="full_path"
        )

        self.assertNotIn("path", omitted[0])
        self.assertNotIn("file_name", omitted[0])
        self.assertIn("sha256", omitted[0])
        self.assertTrue(omitted[0]["is_reference"])
        self.assertFalse(omitted[1]["is_reference"])
        self.assertEqual(names[1]["file_name"], "mic_b.wav")
        self.assertNotIn("path", names[1])
        self.assertEqual(full[1]["path"], str(paths[1]))
        with self.assertRaises(ValueError):
            source_diagnostics(
                prepared, reference_index=0, source_path_policy="everything"
            )
        with self.assertRaises(ValueError):
            source_diagnostics(prepared, reference_index=0, input_labels=("a",))

    def test_output_that_would_replace_a_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            collision = root / MERGED_AUDIO_FILE_NAME
            collision.write_bytes(b"original recording")
            prepared = prepared_from_signals(
                self.signals, (collision, root / "other.wav")
            )

            with self.assertRaisesRegex(ValueError, "source recording"):
                write_multi_audio_outputs(
                    root, prepared, self.alignment, self.selection
                )

            self.assertEqual(collision.read_bytes(), b"original recording")
            self.assertEqual(sorted(path.name for path in root.iterdir()), [MERGED_AUDIO_FILE_NAME])

    def test_failed_write_leaves_no_partial_or_staging_files(self) -> None:
        prepared = prepared_from_signals(self.signals)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch(
                "multimic.pipeline._write_json",
                side_effect=OSError("disk full"),
            ):
                with self.assertRaises(OSError):
                    write_multi_audio_outputs(
                        root, prepared, self.alignment, self.selection
                    )

            self.assertEqual(list(root.iterdir()), [])

    def test_failed_publish_rolls_back_already_published_files(self) -> None:
        prepared = prepared_from_signals(self.signals)
        real_replace = os.replace
        calls = []

        def fail_second(source, target):
            calls.append(target)
            if len(calls) == 2:
                raise OSError("target locked")
            return real_replace(source, target)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch("multimic.pipeline.os.replace", side_effect=fail_second):
                with self.assertRaises(OSError):
                    write_multi_audio_outputs(
                        root, prepared, self.alignment, self.selection
                    )

            self.assertEqual(list(root.iterdir()), [])

    def test_mismatched_stage_results_are_rejected_before_writing(self) -> None:
        prepared = prepared_from_signals(self.signals)
        shorter = (
            signal(self.signals[0].samples[:-10]),
            signal(self.signals[1].samples[:-10]),
        )
        other_selection = select_with_overrides(shorter)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "out"
            with self.assertRaisesRegex(ValueError, "timeline"):
                write_multi_audio_outputs(
                    root, prepared, self.alignment, other_selection
                )
            self.assertFalse(root.exists())

    def test_reason_descriptions_cover_every_reason_code(self) -> None:
        from multimic import select as select_module

        self.assertEqual(
            set(SELECTION_REASON_DESCRIPTIONS),
            set(select_module._REASON_PRIORITY),
        )
        self.assertIs(multimic.write_multi_audio_outputs, write_multi_audio_outputs)
        self.assertIn("render_merged_audio", multimic.__all__)


if __name__ == "__main__":
    unittest.main()
