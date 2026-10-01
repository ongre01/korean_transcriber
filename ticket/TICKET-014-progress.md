# TICKET-014: 진행 단계와 진행률 UI

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

진행 단계와 진행률 UI을 완료하여 독립 검증 가능한 결과를 제공한다.

## Background

PROJECT_GOAL.md의 FR-014을 수행하며 사양서 1차 기능 흐름을 구성한다.

## Source

- `Qt Widgets 기반 음성 녹음·전사 UI 개발 사양서.md` — 11, 19절
- `PROJECT_GOAL.md` — Functional Requirements > FR-014

## Requirements

- Preparing/Loading Model/Decoding Audio/Transcribing/Diarization/Saving Result/Completed 단계를 표시한다
- 유효한 progress 값을 표시하고 확정 진행률이 없는 단계는 불확정 표시를 사용한다
- 처리 시간/전체 시간 정보가 제공되면 표시하며 없는 값을 만들어내지 않는다
- 성공·오류·취소 후 이전 진행 표시가 다음 작업에 남지 않게 한다

## Implementation Scope

- 아래 Files의 제안 대상을 실제 저장소 구조에 맞게 생성/연결한다.
- 요구 항목에 필요한 최소 supporting change만 수행하고 기존 Python 엔진 변경 근거를 기록한다.

## Out of Scope

- Streaming STT, 회의 요약, AI 엔진 C++ 이식, 사양서 28절 후속 기능.
- 다른 티켓의 독립 기능을 함께 구현하지 않는다.

## Dependencies

- TICKET-004
- TICKET-007
- TICKET-013

## Acceptance Criteria

- [ ] Preparing/Loading Model/Decoding Audio/Transcribing/Diarization/Saving Result/Completed 단계를 표시한다
- [ ] 유효한 progress 값을 표시하고 확정 진행률이 없는 단계는 불확정 표시를 사용한다
- [ ] 처리 시간/전체 시간 정보가 제공되면 표시하며 없는 값을 만들어내지 않는다
- [ ] 성공·오류·취소 후 이전 진행 표시가 다음 작업에 남지 않게 한다
- [ ] 관련 검증 결과와 미검증 환경을 기록한다.

## Test Requirements

### Normal Cases

- 각 단계와 progress 0/100·누락·범위 밖 값·빠른 이벤트 연속 수신 검증

### Error Cases

- 요구 조건의 오류 입력/실패 경로에서 성공 상태로 오인하지 않는지 확인한다.

### Edge Cases

- 빈 입력, 반복 실행 및 해당 기능의 경계 조건을 검증하고 상태/자원 잔류가 없는지 확인한다.

## Files

### Existing

- 실제 소스 저장소는 이번 입력에 포함되지 않았다. 사양서의 기존 Python 파일 설명은 TICKET-001에서 실제 확인한다.

### To Create

- `gui/src/MainWindow.cpp` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/src/backend/BackendProcess.cpp` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)

## Open Questions

- 전체 단계 기준 % 또는 전사 단계 기준 %인지 계약에 명시한다.
