#!/usr/bin/env python
import argparse
import json
from pathlib import Path
import sys
import time


sys.stdout.reconfigure(encoding="utf-8", errors="strict", newline="\n", write_through=True)

parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
args = parser.parse_args()
path = Path(args.input).resolve()

if "slow" in path.name:
    time.sleep(0.6)

if "corrupt" in path.name:
    print(json.dumps({"ok": False, "error": "mock decode failure"}), flush=True)
    raise SystemExit(1)

if not path.is_file():
    print(json.dumps({"ok": False, "error": "mock missing file"}), flush=True)
    raise SystemExit(1)

print(
    json.dumps(
        {
            "ok": True,
            "path": str(path),
            "file_name": path.name,
            "size_bytes": path.stat().st_size,
            "duration_seconds": 65.25,
        },
        ensure_ascii=False,
    ),
    flush=True,
)
