"""MM-DEV-017: compare single-file and multi-input transcription of one meeting.

``run`` transcribes every recording on its own and the multi-input channel
selection with one shared set of bridge settings, then scores the run.
``score`` re-scores an existing run, for example once the human reference
transcript is ready; Whisper is not run again.  ``check-reference`` validates
a reference transcript file without scoring anything.

Exit codes: 0 success, 1 a condition failed, 2 invalid dataset/arguments.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ENGINE_DIR = Path(__file__).resolve().parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from evaluation.dataset import DatasetError, load_dataset  # noqa: E402
from evaluation.runner import default_conditions, parse_condition, run_evaluation  # noqa: E402


DEFAULT_OUTPUT_ROOT = ENGINE_DIR.parent / "artifacts" / "mm-dev-017"


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Run every condition, then score the run.")
    run.add_argument("--dataset", type=Path, required=True, help="Evaluation dataset JSON file.")
    run.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    run.add_argument("--run-id", help="Run folder name. Defaults to the local start time.")
    run.add_argument(
        "--condition",
        action="append",
        help="Run only this condition (repeatable): single_<index> or multi.",
    )
    run.add_argument(
        "--python",
        default=sys.executable,
        help="Interpreter for backend_bridge.py. Defaults to this interpreter.",
    )
    run.add_argument("--no-score", action="store_true", help="Only run the conditions.")

    score = commands.add_parser("score", help="Score an existing run directory.")
    score.add_argument("--run-dir", type=Path, required=True)
    score.add_argument(
        "--reference",
        type=Path,
        help="Reference transcript JSON. Defaults to the dataset's reference_transcript.",
    )

    for command in (run, score):
        command.add_argument(
            "--missed-threshold",
            type=float,
            default=0.5,
            help="An utterance is missed when less than this share of its characters is recognized.",
        )

    check = commands.add_parser("check-reference", help="Validate a reference transcript file.")
    check.add_argument("--reference", type=Path, required=True)
    return parser.parse_args(argv)


def _score(run_dir: Path, reference: Path | None, missed_threshold: float) -> int:
    from evaluation.report import score_run

    report_path, markdown_path, all_completed = score_run(
        run_dir,
        reference_path=reference,
        missed_threshold=missed_threshold,
    )
    print(f"MM-DEV-017 report: {report_path}", file=sys.stderr)
    print(f"MM-DEV-017 summary: {markdown_path}", file=sys.stderr)
    return 0 if all_completed else 1


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    if arguments.command == "check-reference":
        from evaluation.reference import ReferenceError, load_reference

        try:
            reference = load_reference(arguments.reference)
        except ReferenceError as exc:
            print(f"Invalid reference transcript: {exc}", file=sys.stderr)
            return 2
        print(
            f"OK: {len(reference.utterances)} utterances, "
            f"{len(reference.speakers)} speakers, "
            f"{reference.scored_seconds:.1f} s scored"
        )
        return 0

    if arguments.command == "score":
        return _score(arguments.run_dir.resolve(), arguments.reference, arguments.missed_threshold)

    try:
        dataset = load_dataset(arguments.dataset)
        conditions = (
            tuple(parse_condition(name, dataset) for name in dict.fromkeys(arguments.condition))
            if arguments.condition
            else default_conditions(dataset)
        )
    except (DatasetError, ValueError) as exc:
        print(f"Invalid evaluation dataset: {exc}", file=sys.stderr)
        return 2
    if dataset.authorization_status != "approved":
        print(
            "WARNING: authorization.approved_by is empty. The run is recorded as "
            "'unverified' and its results must not be shared outside the team.",
            file=sys.stderr,
        )

    try:
        run_dir = run_evaluation(
            dataset,
            arguments.output_root.expanduser().resolve(),
            conditions=conditions,
            python=arguments.python,
            run_id=arguments.run_id,
        )
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"MM-DEV-017 run directory: {run_dir}", file=sys.stderr)
    if arguments.no_score:
        return 0
    return _score(run_dir, None, arguments.missed_threshold)


if __name__ == "__main__":
    raise SystemExit(main())
