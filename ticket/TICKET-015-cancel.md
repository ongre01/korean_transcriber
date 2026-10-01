# TICKET-015: 전사 취소와 프로세스 정리

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

전사 취소와 프로세스 정리을 완료하여 독립 검증 가능한 결과를 제공한다.

## Background

PROJECT_GOAL.md의 FR-015을 수행하며 사양서 1차 기능 흐름을 구성한다.

## Source

- `Qt Widgets 기반 음성 녹음·전사 UI 개발 사양서.md` — 20, 26절
- `PROJECT_GOAL.md` — Functional Requirements > FR-015

## Requirements

- Processing에서 취소하면 terminate 후 비동기 timer로 대기하고 미종료 시 kill한다
- 취소 이후 완료를 성공으로 처리하지 않고 늦게 도착한 이벤트를 격리한다
- 프로세스 종료 확인 후 유효 입력이 있으면 InputReady, 없으면 Idle로 돌아간다
- 입력 파일을 삭제하지 않고 다음 작업을 시작할 수 있게 한다
- 앱 종료 시 실행 프로세스 정리 방식을 정의한다

## Implementation Scope

- 아래 Files의 제안 대상을 실제 저장소 구조에 맞게 생성/연결한다.
- 요구 항목에 필요한 최소 supporting change만 수행하고 기존 Python 엔진 변경 근거를 기록한다.

## Out of Scope

- Streaming STT, 회의 요약, AI 엔진 C++ 이식, 사양서 28절 후속 기능.
- 다른 티켓의 독립 기능을 함께 구현하지 않는다.

## Dependencies

- TICKET-004
- TICKET-007

## Acceptance Criteria

- [ ] Processing에서 취소하면 terminate 후 비동기 timer로 대기하고 미종료 시 kill한다
- [ ] 취소 이후 완료를 성공으로 처리하지 않고 늦게 도착한 이벤트를 격리한다
- [ ] 프로세스 종료 확인 후 유효 입력이 있으면 InputReady, 없으면 Idle로 돌아간다
- [ ] 입력 파일을 삭제하지 않고 다음 작업을 시작할 수 있게 한다
- [ ] 앱 종료 시 실행 프로세스 정리 방식을 정의한다
- [ ] 관련 검증 결과와 미검증 환경을 기록한다.

## Test Requirements

### Normal Cases

- 즉시 종료·terminate 무시·완료와 취소 경합·반복 취소·앱 종료 중 처리 검증

### Error Cases

- 요구 조건의 오류 입력/실패 경로에서 성공 상태로 오인하지 않는지 확인한다.

### Edge Cases

- 빈 입력, 반복 실행 및 해당 기능의 경계 조건을 검증하고 상태/자원 잔류가 없는지 확인한다.

## Files

### Existing

- 실제 소스 저장소는 이번 입력에 포함되지 않았다. 사양서의 기존 Python 파일 설명은 TICKET-001에서 실제 확인한다.

### To Create

- `gui/src/backend/BackendProcess.cpp` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/src/MainWindow.cpp` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)

## Open Questions

- terminate→kill 대기 시간은 미정이며 설정값/상수로 명시한다. 자식 프로세스 생성 여부는 기존 코드를 확인한다.
