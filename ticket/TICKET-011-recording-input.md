# TICKET-011: 녹음 파일 관리와 자동 입력 연결

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

녹음 파일 관리와 자동 입력 연결을 완료하여 독립 검증 가능한 결과를 제공한다.

## Background

PROJECT_GOAL.md의 FR-011을 수행하며 사양서 1차 기능 흐름을 구성한다.

## Source

- `Qt Widgets 기반 음성 녹음·전사 UI 개발 사양서.md` — 8, 17–18, 30절
- `PROJECT_GOAL.md` — Functional Requirements > FR-011

## Requirements

- 프로그램 데이터 경로 recordings 아래 recording_YYYYMMDD_HHMMSS.wav를 생성한다
- 동일 시각 파일명이 겹쳐도 이전 녹음을 덮어쓰지 않는다
- 정상 종료·WAV finalize 성공 뒤 파일을 CurrentInputFile로 지정한다
- 녹음 중 파일 선택/전사를 비활성화하고 종료 후 InputReady 및 전사 시작을 활성화한다
- 생성 실패나 빈 녹음 처리 정책에 따라 잘못된 파일을 준비 완료로 표시하지 않는다

## Implementation Scope

- 아래 Files의 제안 대상을 실제 저장소 구조에 맞게 생성/연결한다.
- 요구 항목에 필요한 최소 supporting change만 수행하고 기존 Python 엔진 변경 근거를 기록한다.

## Out of Scope

- Streaming STT, 회의 요약, AI 엔진 C++ 이식, 사양서 28절 후속 기능.
- 다른 티켓의 독립 기능을 함께 구현하지 않는다.

## Dependencies

- TICKET-007
- TICKET-009
- TICKET-010

## Acceptance Criteria

- [ ] 프로그램 데이터 경로 recordings 아래 recording_YYYYMMDD_HHMMSS.wav를 생성한다
- [ ] 동일 시각 파일명이 겹쳐도 이전 녹음을 덮어쓰지 않는다
- [ ] 정상 종료·WAV finalize 성공 뒤 파일을 CurrentInputFile로 지정한다
- [ ] 녹음 중 파일 선택/전사를 비활성화하고 종료 후 InputReady 및 전사 시작을 활성화한다
- [ ] 생성 실패나 빈 녹음 처리 정책에 따라 잘못된 파일을 준비 완료로 표시하지 않는다
- [ ] 관련 검증 결과와 미검증 환경을 기록한다.

## Test Requirements

### Normal Cases

- 실제 녹음→WAV→CPU 전사→결과 E2E

### Error Cases

- 같은 초 연속 녹음·저장 경로 쓰기 불가 검증

### Edge Cases

- 빈 입력, 반복 실행 및 해당 기능의 경계 조건을 검증하고 상태/자원 잔류가 없는지 확인한다.

## Files

### Existing

- 실제 소스 저장소는 이번 입력에 포함되지 않았다. 사양서의 기존 Python 파일 설명은 TICKET-001에서 실제 확인한다.

### To Create

- `gui/src/MainWindow.cpp` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/src/audio/AudioRecorder.cpp` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)

## Open Questions

- 실패한 부분 파일 보존/정리 정책을 명시한다. 사용자 입력 파일은 삭제하지 않는다.
