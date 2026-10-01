import argparse
import json
import os
from pathlib import Path
import sys
import time


def encoded(event):
    return json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def write_line(event, ending=b"\n"):
    sys.stdout.buffer.write(encoded(event) + ending)
    sys.stdout.buffer.flush()


parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--input", required=True)
parser.add_argument("--device")
parser.add_argument("--model-dir")
args, _unknown = parser.parse_known_args()

if Path.cwd() != Path(__file__).resolve().parent:
    write_line({"type": "error", "message": "unexpected working directory"})
    raise SystemExit(8)

input_path = Path(args.input)
scenario = input_path.stem if input_path.suffix else args.input


def validate_ui_options():
    if not input_path.is_file():
        write_line({"type": "error", "message": "UI input file was not passed"})
        raise SystemExit(9)
    if args.device != "AUTO":
        write_line({"type": "error", "message": "UI device was not AUTO"})
        raise SystemExit(10)
    if not args.model_dir:
        write_line({"type": "error", "message": "UI model directory was not passed"})
        raise SystemExit(11)

if scenario == "normal":
    write_line({"type": "state", "value": "preparing"}, b"\r\n")
    write_line({"type": "progress", "value": None})
    print("mock diagnostic", file=sys.stderr, flush=True)
    time.sleep(0.15)
    write_line(
        {
            "type": "progress",
            "value": 50,
            "processed_seconds": 1.0,
            "total_seconds": 2.0,
        }
    )
    write_line(
        {
            "type": "segment",
            "start": 0.0,
            "end": 1.0,
            "speaker": 1,
            "text": "안녕하세요.",
        }
    )
    write_line(
        {"type": "completed", "text_file": "C:/result.txt", "srt_file": "C:/result.srt"}
    )
elif scenario == "split":
    first = encoded({"type": "state", "value": "preparing"})
    second = encoded({"type": "progress", "value": 25})
    sys.stdout.buffer.write(first + b"\n" + second + b"\n")
    sys.stdout.buffer.flush()

    segment = encoded(
        {
            "type": "segment",
            "start": 0.0,
            "end": 1.0,
            "speaker": None,
            "text": "한글 분할",
        }
    )
    marker = "한".encode("utf-8")
    split_at = segment.index(marker) + 1
    sys.stdout.buffer.write(segment[:split_at])
    sys.stdout.buffer.flush()
    time.sleep(0.05)
    sys.stdout.buffer.write(segment[split_at:] + b"\r\n")
    sys.stdout.buffer.flush()

    # Deliberately omit the final newline so the receiver must consume EOF data.
    sys.stdout.buffer.write(
        encoded({"type": "completed", "text_file": "C:/split.txt", "srt_file": "C:/split.srt"})
    )
    sys.stdout.buffer.flush()
elif scenario == "bridge_error":
    write_line({"type": "error", "message": "mock bridge failure"})
    raise SystemExit(3)
elif scenario == "malformed":
    sys.stdout.buffer.write(b"{not-json}\n")
    sys.stdout.buffer.flush()
    time.sleep(0.1)
    raise SystemExit(1)
elif scenario == "missing_completed":
    write_line({"type": "state", "value": "preparing"})
elif scenario == "trailing_event":
    write_line({"type": "completed", "text_file": "C:/a.txt", "srt_file": "C:/a.srt"})
    write_line({"type": "state", "value": "preparing"})
elif scenario == "duplicate_completed":
    write_line({"type": "completed", "text_file": "C:/a.txt", "srt_file": "C:/a.srt"})
    write_line({"type": "completed", "text_file": "C:/b.txt", "srt_file": "C:/b.srt"})
elif scenario == "crash":
    write_line({"type": "state", "value": "preparing"})
    os._exit(7)
elif scenario == "completed_then_crash":
    write_line({"type": "completed", "text_file": "C:/a.txt", "srt_file": "C:/a.srt"})
    os._exit(9)
elif scenario == "ui_success":
    validate_ui_options()
    write_line({"type": "state", "value": "preparing"})
    time.sleep(0.15)
    write_line(
        {
            "type": "segment",
            "start": 12.0,
            "end": 13.0,
            "speaker": 2,
            "text": "<b>두 번째</b>",
        }
    )
    write_line(
        {
            "type": "segment",
            "start": 2.0,
            "end": 3.0,
            "speaker": None,
            "text": "첫 번째 & 원문",
        }
    )
    write_line(
        {"type": "completed", "text_file": "C:/ui.txt", "srt_file": "C:/ui.srt"}
    )
elif scenario == "ui_second":
    validate_ui_options()
    write_line(
        {
            "type": "segment",
            "start": 3.0,
            "end": 4.0,
            "speaker": 1,
            "text": "새 작업",
        }
    )
    write_line(
        {"type": "completed", "text_file": "C:/second.txt", "srt_file": "C:/second.srt"}
    )
elif scenario == "ui_empty":
    validate_ui_options()
    write_line(
        {"type": "completed", "text_file": "C:/empty.txt", "srt_file": "C:/empty.srt"}
    )
elif scenario == "ui_long":
    validate_ui_options()
    write_line(
        {
            "type": "segment",
            "start": 1.0,
            "end": 2.0,
            "speaker": None,
            "text": "<start>" + ("긴 텍스트 " * 5000) + "<end>",
        }
    )
    write_line(
        {"type": "completed", "text_file": "C:/long.txt", "srt_file": "C:/long.srt"}
    )
elif scenario == "ui_failure":
    validate_ui_options()
    write_line({"type": "error", "message": "mock UI failure"})
    raise SystemExit(3)
else:
    write_line({"type": "error", "message": "unknown mock scenario"})
    raise SystemExit(2)
