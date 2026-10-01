# TICKET-010: 녹음 시간과 입력 Level 표시

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

녹음 시간과 입력 Level 표시을 완료하여 독립 검증 가능한 결과를 제공한다.

## Background

PROJECT_GOAL.md의 FR-010을 수행하며 사양서 1차 기능 흐름을 구성한다.

## Source

- `Qt Widgets 기반 음성 녹음·전사 UI 개발 사양서.md` — 6, 25, 30절
- `PROJECT_GOAL.md` — Functional Requirements > FR-010

## Requirements

- recordingTimeChanged로 경과 시간을 표시하고 새 녹음 때 초기화한다
- 실제 PCM 형식에 맞게 level을 계산하여 0~1 범위로 전달한다
- UI 갱신 빈도를 제한해 capture와 UI 응답을 유지한다
- 중지 및 오류 후 시간·level 표시를 일관되게 정리한다

## Implementation Scope

- 아래 Files의 제안 대상을 실제 저장소 구조에 맞게 생성/연결한다.
- 요구 항목에 필요한 최소 supporting change만 수행하고 기존 Python 엔진 변경 근거를 기록한다.

## Out of Scope

- Streaming STT, 회의 요약, AI 엔진 C++ 이식, 사양서 28절 후속 기능.
- 다른 티켓의 독립 기능을 함께 구현하지 않는다.

## Dependencies

- TICKET-009

## Acceptance Criteria

- [ ] recordingTimeChanged로 경과 시간을 표시하고 새 녹음 때 초기화한다
- [ ] 실제 PCM 형식에 맞게 level을 계산하여 0~1 범위로 전달한다
- [ ] UI 갱신 빈도를 제한해 capture와 UI 응답을 유지한다
- [ ] 중지 및 오류 후 시간·level 표시를 일관되게 정리한다
- [ ] 관련 검증 결과와 미검증 환경을 기록한다.

## Test Requirements

### Normal Cases

- 무음·알려진 크기의 PCM·최대 amplitude·다중 채널 fixture 검증

### Error Cases

- 실제 녹음 시간 증가 확인

### Edge Cases

- 빈 입력, 반복 실행 및 해당 기능의 경계 조건을 검증하고 상태/자원 잔류가 없는지 확인한다.

## Files

### Existing

- 실제 소스 저장소는 이번 입력에 포함되지 않았다. 사양서의 기존 Python 파일 설명은 TICKET-001에서 실제 확인한다.

### To Create

- `gui/src/audio/AudioRecorder.cpp` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/src/MainWindow.cpp` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)

## Open Questions

- RMS 또는 peak 방식과 표시 갱신 주기는 구현 제안으로 기록한다.
