# TICKET-004: 비동기 BackendProcess와 JSON Lines 수신

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

비동기 BackendProcess와 JSON Lines 수신을 완료하여 독립 검증 가능한 결과를 제공한다.

## Background

PROJECT_GOAL.md의 FR-004을 수행하며 사양서 1차 기능 흐름을 구성한다.

## Source

- `Qt Widgets 기반 음성 녹음·전사 UI 개발 사양서.md` — 10–12, 26절
- `PROJECT_GOAL.md` — Functional Requirements > FR-004

## Requirements

- TranscribeOptions와 TranscriptSegment 자료형을 정의한다
- QProcess signal로 stdout/stderr/finished/errorOccurred를 수신하며 UI 스레드에서 blocking wait를 사용하지 않는다
- 프로그램과 인자를 분리하여 공백·한글 경로를 전달한다
- 분할 수신 버퍼로 완성된 줄만 파싱하고 이벤트를 typed signal로 전달한다
- 완료 이벤트와 exit 상태를 조합해 중복 완료·완료 없이 종료·비정상 종료를 구별한다

## Implementation Scope

- 아래 Files의 제안 대상을 실제 저장소 구조에 맞게 생성/연결한다.
- 요구 항목에 필요한 최소 supporting change만 수행하고 기존 Python 엔진 변경 근거를 기록한다.

## Out of Scope

- Streaming STT, 회의 요약, AI 엔진 C++ 이식, 사양서 28절 후속 기능.
- 다른 티켓의 독립 기능을 함께 구현하지 않는다.

## Dependencies

- TICKET-001
- TICKET-002
- TICKET-003

## Acceptance Criteria

- [ ] TranscribeOptions와 TranscriptSegment 자료형을 정의한다
- [ ] QProcess signal로 stdout/stderr/finished/errorOccurred를 수신하며 UI 스레드에서 blocking wait를 사용하지 않는다
- [ ] 프로그램과 인자를 분리하여 공백·한글 경로를 전달한다
- [ ] 분할 수신 버퍼로 완성된 줄만 파싱하고 이벤트를 typed signal로 전달한다
- [ ] 완료 이벤트와 exit 상태를 조합해 중복 완료·완료 없이 종료·비정상 종료를 구별한다
- [ ] 관련 검증 결과와 미검증 환경을 기록한다.

## Test Requirements

### Normal Cases

- 모의 프로세스로 UTF-8 바이트 분할·여러 줄 묶음·잘못된 JSON·시작 실패·완료 없는 종료 검증

### Error Cases

- 요구 조건의 오류 입력/실패 경로에서 성공 상태로 오인하지 않는지 확인한다.

### Edge Cases

- 빈 입력, 반복 실행 및 해당 기능의 경계 조건을 검증하고 상태/자원 잔류가 없는지 확인한다.

## Files

### Existing

- 실제 소스 저장소는 이번 입력에 포함되지 않았다. 사양서의 기존 Python 파일 설명은 TICKET-001에서 실제 확인한다.

### To Create

- `gui/src/backend/BackendProcess.h` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/src/backend/BackendProcess.cpp` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/src/backend/TranscriptSegment.h` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/src/backend/TranscribeOptions.h` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)

## Open Questions

- 알 수 없는 이벤트 및 최대 줄 길이 정책은 계약에 기록한다.
