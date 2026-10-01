# TICKET-017: QSettings와 숨김 고급 설정

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P1 (선택적 고급 설정 포함)

## Goal

QSettings와 숨김 고급 설정을 완료하여 독립 검증 가능한 결과를 제공한다.

## Background

PROJECT_GOAL.md의 FR-017을 수행하며 사양서 1차 기능 흐름을 구성한다.

## Source

- `Qt Widgets 기반 음성 녹음·전사 UI 개발 사양서.md` — 14, 25절
- `PROJECT_GOAL.md` — Functional Requirements > FR-017

## Requirements

- Python Path/Last Input Directory/Output Directory/Selected Microphone/Selected Device/Diarization Enable/Speaker Count/Whisper Model을 QSettings에 보존한다
- 재시작 때 설정을 복원하고 사라진 마이크·잘못된 경로를 검증한다
- Advanced Settings는 기본 숨김으로 제공한다
- Window/Overlap/Hotwords/Initial Prompt/Segmentation Model/Embedding Model/Cluster Threshold/Minimum Speaker Duration을 확인된 Backend 옵션에 매핑한다
- 지원하지 않는 고급 옵션을 임의로 실행 인자로 보내지 않는다

## Implementation Scope

- 아래 Files의 제안 대상을 실제 저장소 구조에 맞게 생성/연결한다.
- 요구 항목에 필요한 최소 supporting change만 수행하고 기존 Python 엔진 변경 근거를 기록한다.

## Out of Scope

- Streaming STT, 회의 요약, AI 엔진 C++ 이식, 사양서 28절 후속 기능.
- 다른 티켓의 독립 기능을 함께 구현하지 않는다.

## Dependencies

- TICKET-012
- TICKET-016

## Acceptance Criteria

- [ ] Python Path/Last Input Directory/Output Directory/Selected Microphone/Selected Device/Diarization Enable/Speaker Count/Whisper Model을 QSettings에 보존한다
- [ ] 재시작 때 설정을 복원하고 사라진 마이크·잘못된 경로를 검증한다
- [ ] Advanced Settings는 기본 숨김으로 제공한다
- [ ] Window/Overlap/Hotwords/Initial Prompt/Segmentation Model/Embedding Model/Cluster Threshold/Minimum Speaker Duration을 확인된 Backend 옵션에 매핑한다
- [ ] 지원하지 않는 고급 옵션을 임의로 실행 인자로 보내지 않는다
- [ ] 관련 검증 결과와 미검증 환경을 기록한다.

## Test Requirements

### Normal Cases

- 설정 저장 후 재실행·기본값·오래된 장치·비정상 값·고급 옵션 경계 확인

### Error Cases

- 요구 조건의 오류 입력/실패 경로에서 성공 상태로 오인하지 않는지 확인한다.

### Edge Cases

- 빈 입력, 반복 실행 및 해당 기능의 경계 조건을 검증하고 상태/자원 잔류가 없는지 확인한다.

## Files

### Existing

- 실제 소스 저장소는 이번 입력에 포함되지 않았다. 사양서의 기존 Python 파일 설명은 TICKET-001에서 실제 확인한다.

### To Create

- `gui/src/settings/Settings.h` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/src/settings/Settings.cpp` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/forms/SettingsDialog.ui` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)

## Open Questions

- 고급 기능은 사양서 14절의 선택적 노출 항목이다. 실제 지원 범위를 확인 후 구현하며 미지원 항목은 비활성/보류 이유를 남긴다.
