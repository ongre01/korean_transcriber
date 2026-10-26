"""Turn a run directory into ``report.json`` and a Korean ``report.md``."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Mapping

from .reference import ReferenceError, load_reference
from .runner import write_json, file_sha256, load_run
from .scoring import (
    calibrate_quality_weights,
    compare_conditions,
    diagnose,
    region_disagreement,
    score_condition,
    selection_summary,
    timeline_mapping,
    transcript_disagreement,
    transcript_statistics,
)


REPORT_SCHEMA_VERSION = 1
REPORT_FILE_NAME = "report.json"
SUMMARY_FILE_NAME = "report.md"
LIMITATIONS = (
    "한 회의(데이터셋)의 결과는 그 녹음 환경에 대한 측정값이며 다른 회의실·장치 배치로 일반화하지 않는다.",
    "단일 최적 파일은 채점 후 CER로 고른 oracle 기준선이다. 실제 사용자는 사전에 이 선택을 할 수 없다.",
    "CER은 공백을 제외한 문자 단위, WER은 어절 단위다. 숫자·영문 표기 차이(예: 3시/세 시)는 정규화하지 않으므로 오류로 계산된다.",
    "전체 전사를 시간 순서로 이어 정렬하므로 동시 발화의 순서 차이는 오류로 계산될 수 있다.",
    "발화별 오류는 전역 정렬을 발화 단위로 나눈 근사값이다. 삽입 오류는 바로 앞 발화에 귀속한다.",
    "처리 시간은 장치 상태·모델 캐시·다른 프로세스 부하에 따라 달라지는 참고값이다.",
    "bootstrap 구간은 발화를 독립으로 가정하므로 인접 발화 오류의 상관만큼 실제보다 좁을 수 있다.",
    "조건 간 전사 불일치는 차이의 크기일 뿐 정확도가 아니다.",
    "논문(arXiv:2312.09746)의 수치는 이 평가에서 실측한 값이 아니며 V1의 개선량으로 인용하지 않는다.",
)


def _load_json(path: Path) -> dict[str, object] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _result_file(run_dir: Path, record: Mapping[str, object], key: str) -> Path | None:
    """A result file of a condition, also after the run folder was moved."""

    value = record.get("result_files", {}).get(key)
    if not value:
        return None
    path = Path(str(value))
    if path.is_file():
        return path
    moved = run_dir / "conditions" / str(record["name"]) / "output" / path.name
    return moved if moved.is_file() else None


def _strip_utterances(score: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in score.items() if key != "utterances"}


def build_report(
    run_dir: Path,
    *,
    reference_path: Path | None = None,
    missed_threshold: float = 0.5,
) -> dict[str, object]:
    run, records = load_run(run_dir)
    dataset = run["dataset"]
    labels = [item["label"] for item in dataset["inputs"]]
    reference_index = int(dataset["reference_index"])
    durations = {int(item["input_index"]): item.get("duration_seconds") for item in run["inputs"]}
    conditions = {
        name: {"kind": record["kind"], "input_index": record["input_index"], "label": record["label"]}
        for name, record in records.items()
    }
    order = [name for name in run["condition_order"] if name in records]
    completed = [name for name in order if records[name]["status"] == "completed"]

    selection_payload = alignment_payload = None
    multi = records.get("multi")
    if multi is not None and multi["status"] == "completed":
        selection_file = _result_file(run_dir, multi, "selection_file")
        alignment_file = _result_file(run_dir, multi, "alignment_file")
        selection_payload = _load_json(selection_file) if selection_file else None
        alignment_payload = _load_json(alignment_file) if alignment_file else None

    condition_reports = []
    for name in order:
        record = records[name]
        audio_index = reference_index if record["kind"] == "multi" else int(record["input_index"])
        condition_reports.append(
            {
                "name": name,
                "label": record["label"],
                "kind": record["kind"],
                "input_index": record["input_index"],
                "command": record["command"],
                "statistics": transcript_statistics(record, durations.get(audio_index)),
            }
        )

    manual_offsets = dataset.get("alignment", {}).get("manual_offsets_ms", {})
    mappings = {
        name: timeline_mapping(conditions[name], reference_index, alignment_payload, manual_offsets)
        for name in completed
    }
    report: dict[str, object] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "ticket": "MM-DEV-017",
        "scenario": "MM-10",
        "run_id": run["run_id"],
        "run_created_at_utc": run["created_at_utc"],
        "scored_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": dataset,
        "environment": run["environment"],
        "inputs": run["inputs"],
        "shared_bridge_arguments": run["shared_bridge_arguments"],
        "conditions": condition_reports,
        "multi_input": (
            selection_summary(selection_payload, alignment_payload, labels) if selection_payload else None
        ),
        "transcript_disagreement": transcript_disagreement(
            {
                name: "\n".join(str(item.get("text") or "") for item in records[name]["segments"])
                for name in completed
            }
        ),
        "region_disagreement": (
            region_disagreement(
                {name: records[name]["segments"] for name in completed},
                mappings,
                float(durations[reference_index]),
                selection_payload,
            )
            if durations.get(reference_index)
            else None
        ),
        "limitations": list(LIMITATIONS),
    }

    reference_file = reference_path or (
        Path(dataset["reference_transcript"]) if dataset.get("reference_transcript") else None
    )
    if reference_file is None:
        report["accuracy"] = {
            "status": "not_measured",
            "reason": "정답 전사(reference transcript)가 없어 CER/WER·누락 발화·화자 배정 오류를 계산하지 않았다.",
        }
        return report

    try:
        reference = load_reference(reference_file)
    except ReferenceError as exc:
        report["accuracy"] = {"status": "not_measured", "reason": f"정답 전사를 읽을 수 없다: {exc}"}
        return report

    scores: dict[str, dict[str, object]] = {}
    not_scored: dict[str, str] = {}
    for name in completed:
        mapping = mappings[name]
        try:
            scores[name] = score_condition(
                reference, records[name]["segments"], mapping, missed_threshold=missed_threshold
            )
        except ValueError as exc:
            not_scored[name] = str(exc)
    for name in order:
        if name not in completed:
            not_scored[name] = "the condition did not complete"

    diagnostics = diagnose(reference, scores, conditions, selection_payload)
    report["accuracy"] = {
        "status": "measured",
        "reference": {
            "path": str(reference.path),
            "sha256": file_sha256(reference.path),
            "utterances": len(reference.utterances),
            "speakers": list(reference.speakers),
            "scored_regions": (
                None if reference.scored_regions is None else [list(region) for region in reference.scored_regions]
            ),
            "scored_seconds": reference.scored_seconds,
        },
        "missed_threshold": missed_threshold,
        "scores": {name: _strip_utterances(score) for name, score in scores.items()},
        "not_scored": not_scored,
        "comparison": compare_conditions(
            reference, scores, conditions, reference_index, diagnostics["utterance_tags"]
        ),
        "diagnostics": diagnostics,
        "calibration": calibrate_quality_weights(selection_payload, diagnostics["utterances"]),
    }
    return report


def _percent(value: object, digits: int = 2) -> str:
    return "—" if value is None else f"{float(value) * 100:.{digits}f}%"


def _points(value: object) -> str:
    return "—" if value is None else f"{float(value) * 100:+.2f}%p"


def _seconds(value: object) -> str:
    return "—" if value is None else f"{float(value):,.1f} s"


def _number(value: object, digits: int = 3) -> str:
    return "—" if value is None else f"{float(value):.{digits}f}"


def _table(header: list[str], rows: list[list[object]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines.extend("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows)
    return lines


def render_markdown(report: Mapping[str, object]) -> str:
    dataset = report["dataset"]
    accuracy = report["accuracy"]
    measured = accuracy["status"] == "measured"
    lines = [
        f"# MM-DEV-017 평가 결과 — {dataset['dataset_id']} / {report['run_id']}",
        "",
        f"- 시나리오: MM-10 (단일 파일 전사 vs 다중 입력 통합 전사)",
        f"- 실행: {report['run_created_at_utc']}, 채점: {report['scored_at_utc']}",
        f"- 데이터 사용 허가: **{dataset['authorization_status']}** — {dataset['authorization'].get('basis', '')}",
        f"- 정확도 측정: **{'측정' if measured else '미측정'}**"
        + ("" if measured else f" — {accuracy['reason']}"),
        "",
        "## 1. 조건별 결과",
        "",
    ]

    rows = []
    for condition in report["conditions"]:
        statistics = condition["statistics"]
        row = [
            condition["name"],
            condition["label"],
            statistics["status"],
            _seconds(statistics["wall_seconds"]),
            _number(statistics["real_time_factor"]),
            statistics["segment_count"],
            f"{statistics['characters']:,}",
            len(statistics["speakers"]),
        ]
        if measured:
            score = accuracy["scores"].get(condition["name"])
            if score is None:
                row.extend(["—"] * 4)
            else:
                speaker = score["speaker"]
                row.extend(
                    [
                        _percent(score["cer"]["error_rate"]),
                        _percent(score["wer"]["error_rate"]),
                        score["missed_utterances"]["count"],
                        _percent(speaker.get("error_rate")) if speaker.get("available") else "—",
                    ]
                )
        rows.append(row)
    header = ["조건", "입력", "상태", "처리 시간", "RTF", "세그먼트", "문자 수", "화자 수"]
    if measured:
        header.extend(["CER", "WER", "누락 발화", "화자 오류(문자)"])
    lines.extend(_table(header, rows))
    lines.append("")
    lines.append("RTF = 처리 시간 / 입력 길이. 다중 입력의 입력 길이는 기준 녹음 길이다.")

    if measured:
        comparison = accuracy["comparison"]
        lines.extend(["", "## 2. 다중 입력 vs 단일 파일", ""])
        if not comparison.get("available"):
            lines.append(f"비교 불가: {comparison.get('reason')}")
        else:
            compare_rows = []
            for role, item in comparison["baselines"].items():
                cer = item["cer"]
                bootstrap = cer["bootstrap"]
                compare_rows.append(
                    [
                        {"single_best_oracle": "단일 최적(oracle)", "single_reference_input": "단일 기준 녹음"}[role],
                        item["baseline"],
                        _percent(cer["baseline"]),
                        _percent(cer["candidate"]),
                        _points(cer["difference"]),
                        f"[{_points(bootstrap['ci95_low'])}, {_points(bootstrap['ci95_high'])}]",
                        item["verdict"],
                        f"{item['utterances']['improved']}/{item['utterances']['degraded']}/{item['utterances']['unchanged']}",
                        _points(item["wer"]["difference"]),
                        "—" if item["missed_utterances"]["difference"] is None else f"{item['missed_utterances']['difference']:+d}",
                    ]
                )
            lines.extend(
                _table(
                    [
                        "기준선",
                        "조건",
                        "기준선 CER",
                        "다중 CER",
                        "차이",
                        "95% CI",
                        "판정",
                        "발화 개선/악화/동일",
                        "WER 차이",
                        "누락 차이",
                    ],
                    compare_rows,
                )
            )
            lines.append("")
            lines.append(
                "판정은 발화 단위 bootstrap 95% 구간이 0을 포함하지 않을 때만 `multi_better`/`multi_worse`로 표시한다."
            )
            best = comparison["baselines"]["single_best_oracle"]
            for title, key in (("악화 발화(다중이 더 나쁨)", "most_degraded"), ("개선 발화", "most_improved")):
                items = best["utterances"][key]
                lines.extend(["", f"### {title} — 기준선 {best['baseline']}", ""])
                if not items:
                    lines.append("없음")
                    continue
                lines.extend(
                    _table(
                        ["발화", "시간", "화자", "문자", "기준선 오류", "다중 오류", "태그"],
                        [
                            [
                                item["id"],
                                f"{item['start']:.1f}–{item['end']:.1f}",
                                item["speaker"] or "—",
                                item["characters"],
                                item["baseline_errors"],
                                item["candidate_errors"],
                                ", ".join(item["tags"]) or "—",
                            ]
                            for item in items
                        ],
                    )
                )

        diagnostics = accuracy["diagnostics"]
        lines.extend(["", "## 3. 실패 구간 진단 (태그별 CER)", ""])
        names = list(accuracy["scores"])
        lines.extend(
            _table(
                ["태그", "발화", "문자", *[f"CER {name}" for name in names]],
                [
                    [tag, item["utterances"], item["characters"], *[_percent(item["cer"].get(name)) for name in names]]
                    for tag, item in diagnostics["by_tag"].items()
                ],
            )
        )
        agreement = diagnostics["selection_agreement"]
        lines.extend(
            [
                "",
                f"- 발화 단위 oracle 입력(단일 전사 오류가 가장 적은 녹음)과 V1 선택 입력 일치율: "
                f"{_percent(agreement['rate'], 1)} ({agreement['agreeing_utterances']}/{agreement['judged_utterances']}발화)",
                f"- 발화 단위 oracle 선택 시 CER(단일 전사 결과를 발화마다 최선으로 고른 하한 추정): "
                f"{_percent(diagnostics['utterance_oracle_cer'])}",
            ]
        )

        calibration = accuracy["calibration"]
        lines.extend(["", "## 4. 품질 가중치 보정 (proxy)", ""])
        if not calibration.get("available"):
            lines.append(f"수행 불가: {calibration.get('reason')}")
        else:
            lines.append(
                f"- 판정: **{calibration.get('verdict')}**, 라벨 발화 {calibration['labeled_utterances']}개, "
                f"공식 재현 오차 {calibration['formula_check_max_abs_error']:.2e}"
            )
            if "run_weight_agreement" in calibration:
                lines.append(
                    f"- 실행 가중치 일치율 {_percent(calibration['run_weight_agreement'], 1)}, "
                    f"최선 후보 {_percent(calibration['best_candidate']['agreement'], 1)} "
                    f"(gain {_points(calibration['gain'])})"
                )
                lines.append(f"- 최선 후보 가중치: `{json.dumps(calibration['best_candidate']['weights'])}`")
            lines.append(
                "- 이 값은 같은 데이터로 맞추고 평가한 proxy다. `quality.py` 기본값 변경 전 후보 가중치로 다시 실행해 CER로 확인해야 한다."
            )

    multi_input = report.get("multi_input")
    lines.extend(["", "## 5. 다중 입력 정렬·채널 선택", ""])
    if multi_input is None:
        lines.append("다중 입력 조건이 완료되지 않아 선택 이력이 없다.")
    else:
        lines.append(
            f"- 전환 수 **{multi_input['switch_count']}**, 선택 구간 {multi_input['segment_count']}개, "
            f"crossfade {multi_input['crossfade_count']}, 무음 패딩 {_seconds(multi_input['padding_seconds'])}"
        )
        lines.extend(
            [""]
            + _table(
                ["입력", "선택 시간", "비율"],
                [
                    [f"{item['input_index']}: {item['label']}", _seconds(item["seconds"]), _percent(item["share"], 1)]
                    for item in multi_input["input_seconds"]
                ],
            )
        )
        lines.extend(
            [""]
            + _table(
                ["선택 사유(주 사유)", "시간"],
                [[reason, _seconds(seconds)] for reason, seconds in multi_input["reason_seconds"].items()],
            )
        )
        if multi_input["warning_counts"]:
            lines.append("")
            lines.append(
                "- 선택 경고: "
                + ", ".join(f"{code} {count}건" for code, count in multi_input["warning_counts"].items())
            )
        if multi_input.get("alignment"):
            lines.extend(
                [""]
                + _table(
                    ["입력", "상태", "오프셋", "드리프트", "신뢰도", "보정"],
                    [
                        [
                            f"{item['input_index']}: {item['label']}",
                            item["status"],
                            "—" if item["offset_ms"] is None else f"{item['offset_ms']:+.1f} ms",
                            "—" if item["drift_ppm"] is None else f"{item['drift_ppm']:+.1f} ppm",
                            _number(item["confidence"]),
                            item["correction_method"],
                        ]
                        for item in multi_input["alignment"]
                    ],
                )
            )

    lines.extend(["", "## 6. 단계별 처리 시간", ""])
    stage_names = list(
        dict.fromkeys(
            stage for condition in report["conditions"] for stage in condition["statistics"]["stage_seconds"]
        )
    )
    lines.extend(
        _table(
            ["조건", *stage_names],
            [
                [condition["name"], *[_seconds(condition["statistics"]["stage_seconds"].get(stage)) for stage in stage_names]]
                for condition in report["conditions"]
            ],
        )
    )

    lines.extend(["", "## 7. 조건 간 전사 불일치 (정확도 아님)", ""])
    lines.append("두 조건의 전사 문자열 사이 편집 거리다. 차이의 크기만 보여주며 어느 쪽이 맞는지는 알 수 없다.")
    lines.append("")
    lines.extend(
        _table(
            ["조건 A", "조건 B", "편집 거리", "A 문자 수", "B 문자 수", "A 대비 비율"],
            [
                [
                    item["first"],
                    item["second"],
                    f"{item['edit_distance']:,}",
                    f"{item['first_characters']:,}",
                    f"{item['second_characters']:,}",
                    _percent(item["rate_vs_first"]),
                ]
                for item in report["transcript_disagreement"]
            ],
        )
    )

    regions = report.get("region_disagreement")
    if regions is not None and regions["conditions"]:
        lines.extend(["", f"### 7.1 구간별 불일치와 정답 전사 구간 후보 ({regions['region_seconds']:.0f}초 단위)", ""])
        ordered = sorted(regions["regions"], key=lambda item: -item["max_pair_rate"])[:10]
        lines.extend(
            _table(
                ["구간", "최대 쌍별 불일치", *[f"문자 {name}" for name in regions["conditions"]], "다중 선택(초)"],
                [
                    [
                        f"{item['start']:.0f}–{item['end']:.0f} s",
                        _percent(item["max_pair_rate"], 1),
                        *[item["characters"][name] for name in regions["conditions"]],
                        ", ".join(f"입력{index} {seconds:.0f}" for index, seconds in item["selected_input_seconds"].items())
                        or "—",
                    ]
                    for item in ordered
                ],
            )
        )
        lines.extend(
            [
                "",
                "정답 전사 구간 후보(`scored_regions`에 그대로 넣을 수 있는 기준 녹음 시간축 초 단위):",
                "",
                f"- 무작위 표본(seed {regions['suggested_random_seed']}, 수준 추정용): `{json.dumps(regions['suggested_random'])}`",
                f"- 불일치 상위(진단용, 차이를 과대평가함): `{json.dumps(regions['suggested_high_disagreement'])}`",
                "- 경계가 발화 중간이면 가까운 무음으로 옮긴 뒤 사용한다.",
            ]
        )

    lines.extend(["", "## 8. 재현 정보", ""])
    environment = report["environment"]
    git = environment.get("git", {})
    lines.append(
        f"- 코드: `{git.get('commit')}` ({git.get('branch')}, 추적 파일 변경 {git.get('tracked_changes')})"
    )
    lines.append(f"- 플랫폼: {environment.get('platform')}, Python {environment.get('python_version')}")
    lines.append(
        "- 패키지: "
        + ", ".join(f"{name} {version}" for name, version in environment.get("packages", {}).items())
    )
    lines.append(f"- 모델: `{environment.get('model_dir')}`")
    for item in report["inputs"]:
        lines.append(
            f"- 입력 {item['input_index']} ({item['label']}): `{Path(item['path']).name}`, "
            f"{_seconds(item['duration_seconds'])}, {item['sample_rate']} Hz, {item['channels']} ch, "
            f"sha256 `{item['sha256']}`"
        )
    lines.append("- 모든 조건의 공통 bridge 인자:")
    lines.append("")
    lines.append("```text")
    lines.append(" ".join(report["shared_bridge_arguments"]))
    lines.append("```")

    lines.extend(["", "## 9. 해석 제한", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"


def score_run(
    run_dir: Path,
    *,
    reference_path: Path | None = None,
    missed_threshold: float = 0.5,
) -> tuple[Path, Path, bool]:
    """Write ``report.json``/``report.md``; also says whether every condition completed."""

    report = build_report(run_dir, reference_path=reference_path, missed_threshold=missed_threshold)
    report_path = run_dir / REPORT_FILE_NAME
    markdown_path = run_dir / SUMMARY_FILE_NAME
    write_json(report_path, report)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    all_completed = all(item["statistics"]["status"] == "completed" for item in report["conditions"])
    return report_path, markdown_path, all_completed
