"""MM-DEV-018: compare remedies for speech lost by single-channel selection.

``run`` builds controlled overlap/far-field cases from a dataset's aligned
recordings, then transcribes every case with V1 channel selection, each single recording, delay-and-sum
beamforming, channel-wise transcript merging and the given separation models.
``meeting-proxy`` measures, without a reference, how much text channel-wise
merging would add to the real meeting transcript of an MM-DEV-017 run.
``report`` re-renders ``report.md`` from an existing ``results.json``.

Exit codes: 0 success, 2 invalid dataset/arguments.
"""

from __future__ import annotations

import argparse
from dataclasses import fields
from datetime import datetime
import json
from pathlib import Path
import sys


ENGINE_DIR = Path(__file__).resolve().parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from evaluation.dataset import DatasetError, load_dataset  # noqa: E402
from evaluation.runner import write_json  # noqa: E402
from overlap_rnd.experiment import RESULTS_FILE_NAME, ExperimentConfig  # noqa: E402


DEFAULT_OUTPUT_ROOT = ENGINE_DIR.parent / "artifacts" / "mm-dev-018"
REPORT_FILE_NAME = "report.md"
PROXY_FILE_NAME = "meeting_merge_proxy.json"


def _separation_model(value: str) -> tuple[str, Path]:
    name, separator, path = value.partition("=")
    if not separator or not name or not path:
        raise argparse.ArgumentTypeError("expected NAME=PATH")
    return name, Path(path)


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Build the cases, run every method and score them.")
    run.add_argument("--dataset", type=Path, required=True, help="MM-DEV-017 evaluation dataset JSON file.")
    run.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    run.add_argument("--run-id", help="Run folder name. Defaults to the local start time.")
    run.add_argument(
        "--separation-model",
        type=_separation_model,
        action="append",
        default=[],
        help="Two-speaker separation ONNX model as NAME=PATH (repeatable).",
    )
    defaults = ExperimentConfig()
    for item in fields(ExperimentConfig):
        run.add_argument(
            "--" + item.name.replace("_", "-"),
            type=type(getattr(defaults, item.name)),
            default=getattr(defaults, item.name),
        )

    proxy = commands.add_parser("meeting-proxy", help="Measure channel-wise merging on a real MM-DEV-017 run.")
    proxy.add_argument("--source-run", type=Path, required=True)
    proxy.add_argument("--output", type=Path, help=f"Defaults to <output-root>/{PROXY_FILE_NAME}.")
    proxy.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    proxy.add_argument("--duplicate-threshold", type=float, default=ExperimentConfig().merge_duplicate_threshold)

    report = commands.add_parser("report", help=f"Re-render {REPORT_FILE_NAME} from {RESULTS_FILE_NAME}.")
    report.add_argument("--run-dir", type=Path, required=True)
    return parser.parse_args(argv)


def _write_report(run_dir: Path) -> Path:
    from overlap_rnd.report import render_report

    results = json.loads((run_dir / RESULTS_FILE_NAME).read_text(encoding="utf-8"))
    path = run_dir / REPORT_FILE_NAME
    path.write_text(render_report(results), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    if arguments.command == "report":
        print(f"MM-DEV-018 report: {_write_report(arguments.run_dir)}", file=sys.stderr)
        return 0

    if arguments.command == "meeting-proxy":
        from overlap_rnd.meeting import meeting_merge_proxy

        result = meeting_merge_proxy(
            arguments.source_run.resolve(), duplicate_threshold=arguments.duplicate_threshold
        )
        output = arguments.output or arguments.output_root / PROXY_FILE_NAME
        output.parent.mkdir(parents=True, exist_ok=True)
        write_json(output, result)
        summary = {key: value for key, value in result.items() if key != "review"}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"MM-DEV-018 meeting proxy: {output}", file=sys.stderr)
        return 0

    from overlap_rnd.experiment import run_experiment

    try:
        dataset = load_dataset(arguments.dataset)
    except DatasetError as error:
        print(f"dataset error: {error}", file=sys.stderr)
        return 2
    if len(dataset.inputs) < 2:
        print("dataset error: the experiment needs at least two recordings", file=sys.stderr)
        return 2
    for name, path in arguments.separation_model:
        if not path.is_file():
            print(f"separation model {name} not found: {path}", file=sys.stderr)
            return 2
    config = ExperimentConfig(**{item.name: getattr(arguments, item.name) for item in fields(ExperimentConfig)})
    run_id = arguments.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = arguments.output_root / dataset.dataset_id / run_id
    if run_dir.exists():
        print(f"run directory already exists: {run_dir}", file=sys.stderr)
        return 2
    run_experiment(
        dataset,
        run_dir,
        config=config,
        separator_models={name: path.resolve() for name, path in arguments.separation_model},
    )
    print(f"MM-DEV-018 results: {run_dir / RESULTS_FILE_NAME}", file=sys.stderr)
    print(f"MM-DEV-018 report: {_write_report(run_dir)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
