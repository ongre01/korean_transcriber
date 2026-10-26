"""Markdown summary of an MM-DEV-018 ``results.json``."""

from __future__ import annotations

from statistics import median
from typing import Mapping


CATEGORY_TITLES = {
    "overlap": "동시 발화 (두 화자 겹침)",
    "far_small": "작은 원거리 발화 (긴 발화 + 반대편 장치 근처의 짧은 발화)",
    "single": "대조군 (한 화자, 겹침 없음)",
}


def _percent(value: object) -> str:
    return "-" if value is None else f"{100.0 * float(value):.1f}%"


def _delta(item: Mapping[str, object]) -> str:
    delta = item.get("cer_delta_vs_baseline")
    if not delta:
        return "기준"
    low, high = delta["ci95"]
    mark = " *" if delta["significant"] else ""
    return f"{100.0 * delta['delta']:+.1f}%p [{100.0 * low:+.1f}, {100.0 * high:+.1f}]{mark}"


def _method_table(summary: Mapping[str, Mapping[str, object]], methods: list[str], cost: Mapping[str, object]) -> list[str]:
    lines = [
        "| 방식 | CER | V1 대비 CER 차이 [95% CI] | 누락 발화 | 2차 발화 누락 | 2차 발화 재현율 | 삽입률 | 전사 스트림/케이스 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for method in methods:
        item = summary[method]
        streams = cost.get(method, {}).get("asr_streams_per_case") if isinstance(cost.get(method), Mapping) else None
        secondary = (
            f"{item['secondary_lost']}/{item['secondary_utterances']}" if item["secondary_utterances"] else "-"
        )
        lines.append(
            f"| `{method}` | {_percent(item['cer'])} | {_delta(item)} | {item['lost_utterances']}/{item['utterances']} "
            f"| {secondary} | {_percent(item['secondary_recall_mean'])} | {_percent(item['insertion_rate'])} "
            f"| {'-' if streams is None else f'{streams:.1f}'} |"
        )
    return lines


def render_report(results: Mapping[str, object]) -> str:
    methods = list(results["methods"])
    summary = results["summary"]
    cost = results["cost"]
    pool = results["pool"]
    environment = results["environment"]
    lines = [
        "# MM-DEV-018 겹침 발화·원거리 발화 보완 실험 결과",
        "",
        f"- 데이터셋: `{results['dataset']['dataset_id']}` (허가 상태: {results['dataset']['authorization_status']})",
        f"- 실행: {results['started_at_utc']} ~ {results['finished_at_utc']}, seed {results['config']['seed']}, "
        f"장치 {environment['device']}, commit `{environment['git'].get('commit')}`"
        f"{' (작업 트리 변경 있음)' if environment['git'].get('tracked_changes') else ''}",
        f"- 정답: 섞기 전 깨끗한 구간의 Whisper 전사(사람 전사 아님). 후보 {pool['candidates_considered']}개 중 "
        f"{pool['accepted']}개 채택, 제외 {pool['rejected']}",
    ]
    agreements = [item["agreement_cer"] for item in pool["utterances"]]
    if agreements:
        lines.append(f"- 채택 발화의 장치 간 깨끗한 전사 불일치(CER) 중앙값 {_percent(median(agreements))}, 최대 {_percent(max(agreements))}")
    lines += [
        "- CER: 정답 발화를 모든 순서로 이어 붙여 가장 오류가 적은 순서로 계산(cpCER). `*`: 95% 구간이 0을 포함하지 않음.",
        "- 누락: 정답 글자 중 일치 비율이 50% 미만인 발화. 2차 발화: 겹침의 두 번째 화자, 원거리의 짧은 발화.",
        "",
    ]
    for category, title in CATEGORY_TITLES.items():
        key = f"{category}/ALL"
        if key not in summary:
            continue
        cases = summary[key][methods[0]]["cases"]
        lines += [f"## {title}: 전체 {cases}케이스", ""]
        lines += _method_table(summary[key], methods, cost)
        lines.append("")
        for group in sorted(name for name in summary if name.startswith(category + "/") and name != key):
            lines += [f"### `{group}` ({summary[group][methods[0]]['cases']}케이스)", ""]
            lines += _method_table(summary[group], methods, cost)
            lines.append("")
    sensitivity = results.get("merge_threshold_sensitivity")
    if sensitivity:
        lines += [
            "## 채널별 병합의 중복 판정 임계값 민감도",
            "",
            f"저장된 전사를 임계값만 바꿔 다시 병합했다. 본 실행 임계값: {results['config']['merge_duplicate_threshold']}.",
            "",
            "| 임계값 | 분류 | CER | V1 대비 CER 차이 [95% CI] | 2차 발화 누락 | 삽입률 |",
            "|---:|---|---:|---:|---:|---:|",
        ]
        for threshold, categories in sensitivity.items():
            for category, item in categories.items():
                secondary = (
                    f"{item['secondary_lost']}/{item['secondary_utterances']}" if item["secondary_utterances"] else "-"
                )
                lines.append(
                    f"| {threshold} | {category} | {_percent(item['cer'])} | {_delta(item)} | {secondary} "
                    f"| {_percent(item['insertion_rate'])} |"
                )
        lines.append("")
    lines += [
        "## 처리 비용",
        "",
        f"케이스 오디오 합계 {cost['audio_seconds']:.1f}초 기준. Whisper 시간은 같은 오디오를 한 번만 전사했어도 필요한 모든 방식에 각각 계상했다.",
        "",
        "| 방식 | Whisper 초/오디오 초 | 신호 처리 초/오디오 초 | 전사 스트림/케이스 |",
        "|---|---:|---:|---:|",
    ]
    for method in methods:
        item = cost.get(method)
        if not isinstance(item, Mapping):
            continue
        lines.append(
            f"| `{method}` | {item['asr_seconds_per_audio_second']:.3f} | {item['dsp_seconds_per_audio_second']:.3f} "
            f"| {item['asr_streams_per_case']:.1f} |"
        )
    lines.append("")
    return "\n".join(lines)
