"""Local Nemotron Transcribe adapter for the NeMo-Speech.cpp CLI.

The command and JSON schema follow NVIDIA's ``app/transcribe.cpp`` and
``docs/salm/README.md``. Only existing local models are accepted; downloading
weights and configuring the native runtime remain explicit setup steps.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Callable
import wave

import numpy as np

from transcribe_npu import Segment, TARGET_SAMPLE_RATE


@dataclass(frozen=True)
class NemotronConfig:
    model: Path
    executable: str = "nemo-speech"
    config: Path | None = None


def validate_config(config: NemotronConfig) -> NemotronConfig:
    """Resolve the executable and local files before expensive audio work."""
    executable = os.path.expandvars(os.path.expanduser(str(config.executable).strip()))
    resolved_executable = shutil.which(executable) if executable else None
    if not resolved_executable:
        raise ValueError(
            "Nemotron executable was not found. Install a NeMo-Speech.cpp build "
            "with SALM support and select its nemo-speech executable."
        )
    model = Path(config.model).expanduser().resolve()
    if not model.is_file() or model.suffix.lower() != ".gguf":
        raise ValueError(f"Nemotron decoder GGUF file was not found: {model}")
    config_path = Path(config.config).expanduser().resolve() if config.config else None
    if config_path is not None and not config_path.is_file():
        raise ValueError(f"Nemotron YAML configuration file was not found: {config_path}")
    return NemotronConfig(model, str(Path(resolved_executable).resolve()), config_path)


def _language_code(language: str) -> str:
    code = language.strip().lower()
    if code in ("", "auto", "und"):
        return ""
    if code.startswith("<|") and code.endswith("|>"):
        code = code[2:-2]
    if not re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*", code):
        raise ValueError(f"Unsupported Nemotron language code: {language}")
    return code


def _instruction(language: str, diarization: bool, hotwords: str, initial_prompt: str) -> str:
    if not hotwords.strip() and not initial_prompt.strip():
        return ""
    parts = [
        "Transcribe this audio verbatim with punctuation and capitalization. "
        "Preserve all languages as spoken; do not translate. Output only the transcript."
    ]
    if language:
        language_name = {"ko": "Korean", "en": "English"}.get(language, language)
        parts.append(f"The main speech language is {language_name}.")
    if diarization:
        parts.append(
            "Start each speaker turn with one stable speaker tag from "
            "<spk:0> <spk:1> <spk:2> <spk:3> <spk:4> <spk:5> <spk:6> <spk:7>."
        )
    if hotwords.strip():
        parts.append(f"Context terms: {hotwords.strip()}")
    if initial_prompt.strip():
        parts.append(f"Transcription context: {initial_prompt.strip()}")
    return "\n".join(parts)


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=0.5)


def _run_command(command: list[str], output_path: Path, diagnostic_path: Path) -> int:
    # Disk-backed output avoids pipe deadlocks and keeps the bridge's stdout
    # exclusively for JSON Lines events. Short waits let its cancellation
    # watcher deliver KeyboardInterrupt before the watchdog's hard exit.
    with output_path.open("wb") as output, diagnostic_path.open("wb") as diagnostic:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=diagnostic,
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            while True:
                try:
                    return process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    continue
        finally:
            _stop_process(process)


def _diagnostic_tail(path: Path) -> str:
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 16_384))
        return stream.read().decode("utf-8", errors="replace").strip()


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Nemotron output {field} must be a finite number.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"Nemotron output {field} must be a finite number.")
    return number


def _source_order(text: str, words: list[Segment]) -> tuple[list[Segment], list[int]] | None:
    """Recover decoder order when overlapping speakers interleave CLI words.

    CLI JSON omits word_index. Match exact, disjoint source spans and verify
    every spoken character below. Repeated text is only assigned in order
    within one speaker; cross-speaker repetitions are ambiguous and retain
    the conservative full-transcript fallback.
    """
    grouped: dict[str, list[Segment]] = {}
    for word in words:
        grouped.setdefault(word.text, []).append(word)
    occupied = bytearray(len(text))
    matched: list[tuple[int, Segment]] = []
    for token, occurrences in sorted(grouped.items(), key=lambda item: -len(item[0])):
        if len(occurrences) > 1 and len({word.speaker for word in occurrences}) > 1:
            return None
        positions = []
        cursor = 0
        while (position := text.find(token, cursor)) >= 0:
            end = position + len(token)
            if not any(occupied[position:end]):
                positions.append(position)
            cursor = end
        if len(positions) != len(occurrences):
            return None
        for position, word in zip(positions, occurrences):
            occupied[position:position + len(token)] = b"\x01" * len(token)
            matched.append((position, word))
    if any(character.isalnum() and not occupied[index] for index, character in enumerate(text)):
        return None
    matched.sort(key=lambda item: item[0])
    return [word for _, word in matched], [position for position, _ in matched]


def parse_transcript(
    payload: object,
    duration: float,
    *,
    diarization: bool = False,
    warning_callback: Callable[[str], None] | None = None,
) -> list[Segment]:
    """Convert CLI word times (seconds, one-based speakers) to our segments.

    Missing or partial alignment must not silently discard transcript text.
    Such results retain the complete transcript at recording-level timing,
    with a warning explaining that precise times/speakers are unavailable.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        raise ValueError("Nemotron output must be a JSON object with a text string.")
    words = payload.get("words")
    if not isinstance(words, list):
        raise ValueError("Nemotron output must include a words array.")
    text = re.sub(r"<spk:\d+>", "", payload["text"], flags=re.IGNORECASE).strip()
    timed_words: list[Segment] = []
    for index, word in enumerate(words):
        if not isinstance(word, dict) or not isinstance(word.get("word"), str):
            raise ValueError(f"Nemotron output words[{index}] must contain a word string.")
        start = _number(word.get("start"), f"words[{index}].start")
        end = _number(word.get("end"), f"words[{index}].end")
        if start < 0 or end < start or end > duration + 0.1:
            raise ValueError(f"Nemotron output words[{index}] has invalid audio timestamps.")
        start, end = min(start, duration), min(end, duration)
        speaker = word.get("speaker")
        if speaker is not None and (
            isinstance(speaker, bool) or not isinstance(speaker, int) or speaker < 1
        ):
            raise ValueError(f"Nemotron output words[{index}].speaker must be a positive integer.")
        word_text = word["word"].strip()
        if not word_text:
            continue
        # Occasionally an aligner reports a zero-length word at the boundary.
        # Keep its text, falling back below instead of inventing a word time.
        timed_words.append(Segment(start, end, word_text, speaker - 1 if diarization and speaker else None))

    def fallback() -> list[Segment]:
        if not text:
            return []
        if warning_callback is not None:
            warning_callback(
                "Nemotron did not return complete word alignment that can be matched unambiguously. The full transcript "
                "was retained as one segment spanning the recording; precise timestamps "
                "and speaker labels are unavailable."
            )
        return [Segment(0.0, duration, text)]

    if not timed_words:
        return fallback()
    if not text:
        raise ValueError("Nemotron output has timed words but an empty transcript.")

    # Map word text back onto the transcript so punctuation/spacing is kept.
    # Any uncovered spoken characters indicate partial alignment. CLI JSON
    # currently has no alignment status field, so conservatively retain the
    # whole transcript in that case.
    cursor = 0
    positions: list[int] = []
    for word in timed_words:
        position = text.find(word.text, cursor)
        if position < 0 or any(ch.isalnum() for ch in text[cursor:position]):
            break
        positions.append(position)
        cursor = position + len(word.text)
    if any(word.end <= word.start for word in timed_words):
        return fallback()
    if len(positions) != len(timed_words) or any(ch.isalnum() for ch in text[cursor:]):
        reordered = _source_order(text, timed_words)
        if reordered is None:
            return fallback()
        timed_words, positions = reordered

    segments: list[Segment] = []
    group_start = 0
    group_end = 0.0
    for index, word in enumerate(timed_words):
        group_end = max(group_end, word.end)
        next_word = timed_words[index + 1] if index + 1 < len(timed_words) else None
        text_end = positions[index + 1] if next_word else len(text)
        group_text = text[positions[group_start] if group_start else 0:text_end].strip()
        boundary = (
            next_word is None
            or next_word.speaker != word.speaker
            or next_word.start < word.start
            or next_word.start - word.end >= 1.0
            or next_word.end - timed_words[group_start].start > 10.0
            or len(group_text) >= 100
            or group_text.endswith((".", "!", "?", "。", "！", "？"))
        )
        if boundary:
            segments.append(Segment(timed_words[group_start].start, group_end, group_text, word.speaker))
            group_start = index + 1
            group_end = 0.0
    if diarization and any(segment.speaker is None for segment in segments) and warning_callback is not None:
        warning_callback("Nemotron returned no speaker label for some words; those segments have an unknown speaker.")
    return sorted(segments, key=lambda segment: (segment.start, segment.end))


def transcribe_audio(
    audio: np.ndarray,
    duration: float,
    config: NemotronConfig,
    *,
    language: str = "<|ko|>",
    diarization: bool = False,
    hotwords: str = "",
    initial_prompt: str = "",
    warning_callback: Callable[[str], None] | None = None,
) -> list[Segment]:
    """Transcribe one complete 16 kHz mono recording with the local runtime."""
    config = validate_config(config)
    samples = np.asarray(audio, dtype=np.float32)
    if samples.ndim != 1 or samples.size < 320 or not np.isfinite(samples).all():
        raise ValueError("Nemotron requires finite 16 kHz mono audio lasting at least 20 ms.")
    actual_duration = samples.size / TARGET_SAMPLE_RATE
    if not math.isfinite(duration) or abs(actual_duration - duration) > 1 / TARGET_SAMPLE_RATE:
        raise ValueError("Nemotron audio duration does not match the prepared 16 kHz samples.")
    if actual_duration > 3600:
        raise ValueError("Nemotron accepts up to one hour per recording; use a shorter input.")
    code = _language_code(language)
    instruction = _instruction(code, diarization, hotwords, initial_prompt)
    with tempfile.TemporaryDirectory(prefix="korean-transcriber-nemotron-") as directory:
        work = Path(directory)
        wav_path = work / "input.wav"
        with wave.open(str(wav_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(TARGET_SAMPLE_RATE)
            wav.writeframes(np.rint(np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
        command = [config.executable, "transcribe", str(wav_path), "--model", str(config.model),
                   "--format", "json", "--word-times"]
        if config.config is not None:
            command.extend(["--config", str(config.config)])
        if code:
            command.extend(["--language", code])
        if diarization:
            command.append("--diarize")
        if instruction:
            command.extend(["--instruction", instruction])
        output_path, diagnostic_path = work / "output.json", work / "diagnostic.log"
        try:
            exit_code = _run_command(command, output_path, diagnostic_path)
        except OSError as exc:
            raise RuntimeError(f"Nemotron executable could not be started: {exc}") from exc
        diagnostic = _diagnostic_tail(diagnostic_path)
        if diagnostic:
            sys.stderr.write(diagnostic + "\n")
            sys.stderr.flush()
        if exit_code:
            detail = diagnostic[-2000:] if diagnostic else "The native runtime returned no diagnostic."
            if "unknown key 'asr.salm.n_cpu_moe'" in diagnostic:
                raise RuntimeError(
                    "설치된 nemo-speech는 8GB 프리셋의 CPU 오프로딩(n_cpu_moe)을 지원하지 않습니다. "
                    "NeMo-Speech.cpp 536b17a 이후의 해당 기능을 포함한 런타임으로 교체해 주세요. "
                    "YAML의 n_cpu_moe 설정은 유지해야 합니다. "
                    f"실행 파일: {config.executable}\n{detail}"
                )
            raise RuntimeError(f"Nemotron transcription failed (exit {exit_code}): {detail}")
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Nemotron returned invalid JSON. Use a NeMo-Speech.cpp build with SALM JSON support.") from exc
        return parse_transcript(payload, duration, diarization=diarization, warning_callback=warning_callback)
