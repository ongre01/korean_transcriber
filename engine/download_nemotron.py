#!/usr/bin/env python
"""Download the pinned, official NVIDIA checkpoint; GGUF conversion is separate."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sys
import time

REPO_ID = "nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B"
REVISION = "c17667641642c4265ff2b7037dd101b7d4567634"
MODEL_NAME = REPO_ID.split("/")[-1]
WEIGHTS = "model.safetensors"
# LFS SHA-256 values from NVIDIA's repository at the pinned revision.
FILE_CHECKS = {
    WEIGHTS: (67_496_013_734, "03142dde5176c1d4df21ee829642745a1d670ea4d79e1a4684c64d53195acff2"),
    "tokenizer.json": (17_078_144, "d071004cb4450d3aa31f87aab979cd09ffc99c4e709e6cf72c324346cd948d0d"),
    "alignment_tokenizer/tokenizer.json": (2_219_772, "acb39a7390bd09acf4c224e315427faf41c16a61a692ca8781a059b040c1eb34"),
}
REQUIRED_FILES = (
    "config.json", "llm_backbone/config.json", "tokenizer_config.json",
    "alignment_tokenizer/tokenizer_config.json", "LICENSE", "THIRD_PARTY_NOTICES.md",
)


def default_output_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    return base / "NeMoSpeech" / "checkpoints" / MODEL_NAME


def verify_checkpoint(directory: Path) -> None:
    for filename in REQUIRED_FILES:
        path = directory / filename
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"필수 파일이 없거나 비어 있습니다: {path}")
    for filename, (expected_size, expected_hash) in FILE_CHECKS.items():
        path = directory / filename
        if not path.is_file() or path.stat().st_size != expected_size:
            raise RuntimeError(f"파일이 없거나 크기가 다릅니다: {path} (기대값: {expected_size:,} bytes)")
        print(f"[SHA-256] {filename} 검증 중...", flush=True)
        digest = hashlib.sha256()
        done = 0
        last_report = time.monotonic()
        with path.open("rb") as stream:
            while block := stream.read(8 * 1024 * 1024):
                digest.update(block)
                done += len(block)
                if time.monotonic() - last_report >= 5:
                    print(f"  {done / expected_size:.1%} ({done / 1e9:.2f} GB)", flush=True)
                    last_report = time.monotonic()
        if digest.hexdigest() != expected_hash:
            raise RuntimeError(
                f"SHA-256 불일치: {path}\n"
                "파일을 확인한 뒤 --force-download 옵션으로 다시 다운로드하세요."
            )
        print(f"[OK] {filename}", flush=True)


def check_free_space(directory: Path) -> None:
    existing = sum(
        min((directory / name).stat().st_size, size)
        for name, (size, _) in FILE_CHECKS.items() if (directory / name).is_file()
    )
    # Partial Xet files are not assumed to be resumable; reserve a full transfer.
    needed = sum(size for size, _ in FILE_CHECKS.values()) - existing + 1024**3
    free = shutil.disk_usage(directory).free
    if free < needed:
        raise RuntimeError(
            f"저장 공간이 부족합니다. 필요 여유: {needed / 1e9:.1f} GB, "
            f"현재 여유: {free / 1e9:.1f} GB. --output-dir로 다른 폴더를 지정하세요."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", "-o", type=Path, default=default_output_dir(),
                        help="저장 폴더 (기본: LOCALAPPDATA/NeMoSpeech/checkpoints/모델명)")
    parser.add_argument("--verify-only", action="store_true", help="다운로드 없이 기존 파일만 검증")
    parser.add_argument("--dry-run", action="store_true", help="저장 위치와 다운로드 정보만 표시")
    parser.add_argument("--force-download", action="store_true", help="기존 파일을 다시 다운로드")
    parser.add_argument("--attempts", type=int, default=3, help="전송 실패 시 총 시도 횟수 (기본: 3)")
    args = parser.parse_args(argv)
    if args.attempts < 1:
        parser.error("--attempts는 1 이상이어야 합니다.")
    if args.force_download and args.verify_only:
        parser.error("--force-download와 --verify-only는 함께 사용할 수 없습니다.")
    directory = args.output_dir.expanduser().resolve()
    print(f"NVIDIA 원본: {REPO_ID}\nRevision: {REVISION}\n저장 위치: {directory}", flush=True)
    print("다운로드 약 67.5 GB / GGUF 변환까지 진행할 경우 작업 공간 약 200 GB 필요", flush=True)
    if args.dry_run:
        return 0
    try:
        if not args.verify_only:
            try:
                from huggingface_hub import snapshot_download
            except ImportError:
                raise RuntimeError(
                    'huggingface-hub가 필요합니다. 다음 명령으로 설치하세요:\n'
                    f'"{sys.executable}" -m pip install "huggingface-hub>=0.34,<2"'
                ) from None
            directory.mkdir(parents=True, exist_ok=True)
            if args.force_download:
                # Force-download needs room for temporary copies of existing files.
                needed = sum(size for size, _ in FILE_CHECKS.values()) + 1024**3
                if shutil.disk_usage(directory).free < needed:
                    raise RuntimeError(f"강제 재다운로드에는 약 {needed / 1e9:.1f} GB의 여유 공간이 필요합니다.")
            else:
                check_free_space(directory)
            for attempt in range(1, args.attempts + 1):
                print(f"[다운로드 {attempt}/{args.attempts}] Ctrl+C로 중지할 수 있습니다.", flush=True)
                try:
                    snapshot_download(
                        repo_id=REPO_ID, revision=REVISION, local_dir=str(directory),
                        max_workers=2, force_download=args.force_download,
                    )
                    break
                except Exception as exc:
                    if attempt == args.attempts:
                        raise
                    print(f"전송 실패: {exc}\n잠시 후 다시 시도합니다.", file=sys.stderr, flush=True)
                    time.sleep(min(2**attempt, 10))
        verify_checkpoint(directory)
    except KeyboardInterrupt:
        print("\n중지했습니다. 같은 명령을 다시 실행하면 완료된 파일을 재사용합니다.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"[실패] {exc}", file=sys.stderr)
        return 1
    print(f"\n원본 다운로드 및 검증 완료: {directory}", flush=True)
    print("이 파일은 BF16 원본입니다. 앱에서 사용하려면 IQ4_NL GGUF 변환이 추가로 필요합니다.")
    print("변환 방법: docs/Nemotron_엔진_사용_가이드.md")
    return 0


if __name__ == "__main__":
    for output in (sys.stdout, sys.stderr):
        if hasattr(output, "reconfigure"):
            output.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
