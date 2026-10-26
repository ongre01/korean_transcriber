"""Run every evaluation condition through the bridge the Qt GUI uses.

Each condition is one ``backend_bridge.py`` process.  Single-file conditions
pass ``--input``; the multi-input condition passes ``--multi-input-manifest``.
All of them share the dataset's bridge settings verbatim, so the only
difference between two conditions is the audio they transcribe.  The JSON
Lines protocol stream is stored as received, with the arrival time of every
line, which gives per-stage processing times without instrumenting the engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from typing import Callable

from .dataset import ENGINE_DIR, EvaluationDataset


RUN_SCHEMA_VERSION = 1
BRIDGE_SCRIPT = ENGINE_DIR / "backend_bridge.py"
RUN_FILE_NAME = "run.json"
CONDITION_FILE_NAME = "condition.json"
MANIFEST_FILE_NAME = "multi_input_manifest.json"
MULTI_CONDITION = "multi"
TERMINAL_EVENTS = frozenset({"completed", "error"})
PACKAGES = ("numpy", "openvino", "openvino-genai", "openvino-tokenizers", "sherpa-onnx", "av")


@dataclass(frozen=True)
class Condition:
    """One way of transcribing the meeting."""

    name: str
    kind: str
    input_index: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "kind": self.kind, "input_index": self.input_index}


def single_condition(input_index: int) -> Condition:
    return Condition(f"single_{input_index}", "single", input_index)


def default_conditions(dataset: EvaluationDataset) -> tuple[Condition, ...]:
    """Every recording on its own, then the multi-input channel selection."""

    return (
        *(single_condition(index) for index in range(len(dataset.inputs))),
        Condition(MULTI_CONDITION, "multi"),
    )


def parse_condition(name: str, dataset: EvaluationDataset) -> Condition:
    if name == MULTI_CONDITION:
        return Condition(MULTI_CONDITION, "multi")
    prefix = "single_"
    if name.startswith(prefix) and name[len(prefix):].isdigit():
        index = int(name[len(prefix):])
        if index < len(dataset.inputs):
            return single_condition(index)
    names = [condition.name for condition in default_conditions(dataset)]
    raise ValueError(f"unknown condition {name!r}; expected one of {', '.join(names)}")


def condition_command(
    dataset: EvaluationDataset,
    condition: Condition,
    output_dir: Path,
    manifest_path: Path,
    *,
    python: str,
    bridge_script: Path = BRIDGE_SCRIPT,
) -> list[str]:
    if condition.kind == "multi":
        source = ["--multi-input-manifest", str(manifest_path)]
    else:
        source = ["--input", str(dataset.inputs[condition.input_index].path)]
    return [
        python,
        str(bridge_script),
        *source,
        "--output-dir",
        str(output_dir),
        *dataset.bridge_arguments(),
    ]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: object) -> None:
    text = json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8")


def file_sha256(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _stage_durations(timeline: list[tuple[float, dict[str, object]]]) -> list[dict[str, object]]:
    """Duration of every ``state`` until the next state or the terminal event."""

    marks = [
        (seconds, str(event.get("value")))
        for seconds, event in timeline
        if event.get("type") == "state"
    ]
    end = next(
        (seconds for seconds, event in timeline if event.get("type") in TERMINAL_EVENTS),
        timeline[-1][0] if timeline else 0.0,
    )
    stages = []
    for position, (started, name) in enumerate(marks):
        finished = marks[position + 1][0] if position + 1 < len(marks) else end
        stages.append(
            {"stage": name, "started_seconds": round(started, 3), "seconds": round(finished - started, 3)}
        )
    return stages


def run_condition(
    command: list[str],
    condition: Condition,
    directory: Path,
    *,
    label: str,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, object]:
    """Run one bridge process and keep its protocol stream, stderr and timing.

    ``stdin`` is the null device: end of input is not a cancellation for the
    bridge, and no cancel request is ever sent during an evaluation.
    """

    directory.mkdir(parents=True, exist_ok=True)
    timeline: list[tuple[float, dict[str, object]]] = []
    protocol_errors: list[str] = []
    started_at = _utc_now()
    started = clock()
    with (
        (directory / "stderr.log").open("wb") as stderr_file,
        (directory / "events.jsonl").open("wb") as events_file,
    ):
        process = popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            cwd=str(ENGINE_DIR),
        )
        for raw_line in process.stdout:
            seconds = clock() - started
            events_file.write(raw_line)
            events_file.flush()
            # The protocol is UTF-8 JSON Lines.  A line that is not is kept
            # verbatim in events.jsonl but never scored, because replacement
            # characters would silently count as recognition errors.
            try:
                event = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                protocol_errors.append(raw_line[:200].decode("utf-8", errors="replace").rstrip())
                continue
            if isinstance(event, dict):
                timeline.append((seconds, event))
            else:
                protocol_errors.append(raw_line[:200].decode("utf-8", errors="replace").rstrip())
        process.stdout.close()
        exit_code = process.wait()
    wall_seconds = clock() - started

    terminal = next(
        (event for _, event in reversed(timeline) if event.get("type") in TERMINAL_EVENTS),
        None,
    )
    completed = (
        terminal is not None
        and terminal.get("type") == "completed"
        and exit_code == 0
        and not protocol_errors
    )
    segments = [
        {
            "start": event.get("start"),
            "end": event.get("end"),
            "speaker": event.get("speaker"),
            "text": event.get("text"),
        }
        for _, event in timeline
        if event.get("type") == "segment"
    ]
    record: dict[str, object] = {
        **condition.to_dict(),
        "label": label,
        "command": command,
        "started_at_utc": started_at,
        "status": "completed" if completed else "failed",
        "exit_code": exit_code,
        "wall_seconds": round(wall_seconds, 3),
        "first_event_seconds": round(timeline[0][0], 3) if timeline else None,
        "stages": _stage_durations(timeline),
        "event_arrival_seconds": [round(seconds, 3) for seconds, _ in timeline],
        "warnings": [str(event.get("message")) for _, event in timeline if event.get("type") == "warning"],
        "error_message": (
            str(terminal.get("message"))
            if terminal is not None and terminal.get("type") == "error"
            else f"{len(protocol_errors)} protocol line(s) were not UTF-8 JSON objects"
            if protocol_errors
            else None
        ),
        "protocol_errors": protocol_errors,
        "result_files": (
            {key: value for key, value in terminal.items() if key != "type"} if completed else {}
        ),
        "segment_count": len(segments),
        "segments": segments,
    }
    write_json(directory / CONDITION_FILE_NAME, record)
    return record


def _package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in PACKAGES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def _git_state() -> dict[str, object]:
    def git(*arguments: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", *arguments],
                cwd=str(ENGINE_DIR),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    status = git("status", "--porcelain", "--untracked-files=no")
    return {
        "commit": git("rev-parse", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "tracked_changes": None if status is None else bool(status),
    }


def _model_files(model_dir: Path) -> list[dict[str, object]]:
    if not model_dir.is_dir():
        return []
    return [
        {"name": item.name, "size_bytes": item.stat().st_size}
        for item in sorted(model_dir.iterdir())
        if item.is_file()
    ]


def _optional_sha256(path: Path) -> str | None:
    return file_sha256(path) if path.is_file() else None


def describe_environment(dataset: EvaluationDataset, python: str) -> dict[str, object]:
    settings = dataset.settings
    return {
        "platform": platform.platform(),
        "processor": platform.processor(),
        "runner_python": sys.executable,
        "bridge_python": python,
        "python_version": platform.python_version(),
        "packages": _package_versions(),
        "git": _git_state(),
        "model_dir": settings["model_dir"],
        "model_files": _model_files(Path(str(settings["model_dir"]))),
        "hotwords_sha256": _optional_sha256(Path(str(settings["hotwords_file"]))),
        "initial_prompt_sha256": _optional_sha256(Path(str(settings["initial_prompt_file"]))),
    }


def describe_inputs(dataset: EvaluationDataset) -> list[dict[str, object]]:
    from audio_metadata import inspect_audio

    described = []
    for index, item in enumerate(dataset.inputs):
        metadata = inspect_audio(item.path)
        described.append(
            {
                "input_index": index,
                "label": item.label,
                "path": str(item.path),
                "sha256": file_sha256(item.path),
                "size_bytes": item.path.stat().st_size,
                "duration_seconds": metadata.get("duration_seconds"),
                "sample_rate": metadata.get("sample_rate"),
                "channels": metadata.get("channels"),
                "codec": metadata.get("codec"),
            }
        )
    return described


def _condition_summary(record: dict[str, object], run_dir: Path, directory: Path) -> dict[str, object]:
    summary = {key: value for key, value in record.items() if key not in {"segments", "event_arrival_seconds"}}
    summary["directory"] = directory.relative_to(run_dir).as_posix()
    return summary


def run_evaluation(
    dataset: EvaluationDataset,
    output_root: Path,
    *,
    conditions: tuple[Condition, ...] | None = None,
    python: str = sys.executable,
    run_id: str | None = None,
    bridge_script: Path = BRIDGE_SCRIPT,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    environment: dict[str, object] | None = None,
    inputs: list[dict[str, object]] | None = None,
    log: Callable[[str], None] = lambda message: print(message, file=sys.stderr, flush=True),
) -> Path:
    """Run the conditions in order and return the new run directory.

    A run directory is never reused, so a stored result always belongs to
    exactly one execution.  ``run.json`` is rewritten after every condition,
    which keeps the completed conditions usable if a later one is stopped.
    """

    selected = conditions or default_conditions(dataset)
    run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = (output_root / dataset.dataset_id / run_id).resolve()
    if run_dir.exists():
        raise FileExistsError(f"run directory already exists: {run_dir}")
    run_dir.mkdir(parents=True)

    manifest_path = run_dir / MANIFEST_FILE_NAME
    write_json(manifest_path, dataset.multi_input_manifest())
    log("Describing the environment and the input recordings ...")
    run: dict[str, object] = {
        "schema_version": RUN_SCHEMA_VERSION,
        "ticket": "MM-DEV-017",
        "run_id": run_id,
        "created_at_utc": _utc_now(),
        "dataset": dataset.to_dict(),
        "environment": environment if environment is not None else describe_environment(dataset, python),
        "inputs": inputs if inputs is not None else describe_inputs(dataset),
        "shared_bridge_arguments": dataset.bridge_arguments(),
        "condition_order": [condition.name for condition in selected],
        "conditions": [],
    }
    write_json(run_dir / RUN_FILE_NAME, run)

    for condition in selected:
        directory = run_dir / "conditions" / condition.name
        output_dir = directory / "output"
        output_dir.mkdir(parents=True)
        command = condition_command(
            dataset,
            condition,
            output_dir,
            manifest_path,
            python=python,
            bridge_script=bridge_script,
        )
        label = (
            "multi-input" if condition.kind == "multi" else dataset.inputs[condition.input_index].label
        )
        log(f"[{condition.name}] {label}: running ...")
        record = run_condition(command, condition, directory, label=label, popen=popen)
        log(
            f"[{condition.name}] {record['status']} in {record['wall_seconds']:.1f} s"
            + (f": {record['error_message']}" if record["error_message"] else "")
        )
        run["conditions"].append(_condition_summary(record, run_dir, directory))
        write_json(run_dir / RUN_FILE_NAME, run)
    return run_dir


def load_run(run_dir: Path) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    """Return ``run.json`` and every stored condition record by name."""

    run = json.loads((run_dir / RUN_FILE_NAME).read_text(encoding="utf-8"))
    records = {}
    for summary in run["conditions"]:
        path = run_dir / summary["directory"] / CONDITION_FILE_NAME
        records[summary["name"]] = json.loads(path.read_text(encoding="utf-8"))
    return run, records
