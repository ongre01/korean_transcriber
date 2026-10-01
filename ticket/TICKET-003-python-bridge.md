# TICKET-003: Python GUI Bridge 전사 실행 구현

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

Python GUI Bridge 전사 실행 구현을 완료하여 독립 검증 가능한 결과를 제공한다.

## Background

PROJECT_GOAL.md의 FR-003을 수행하며 사양서 1차 기능 흐름을 구성한다.

## Source

- `Qt Widgets 기반 음성 녹음·전사 UI 개발 사양서.md` — 2, 11–13, 19, 31절
- `PROJECT_GOAL.md` — Functional Requirements > FR-003

## Requirements

- backend_bridge.py가 input/device와 계약상 출력 경로를 받아 기존 전사 엔진을 호출한다
- stdout에는 JSON Lines만 출력하고 일반 로그·traceback은 stderr로 분리한다
- state/progress/segment/completed/error 이벤트를 계약에 맞게 flush하며 완료는 저장 성공 뒤 한 번 출력한다
- 대용량 Decode·Whisper·OpenVINO 처리를 Python 프로세스에서 수행하고 기존 fallback을 보존한다

## Implementation Scope

- 아래 Files의 제안 대상을 실제 저장소 구조에 맞게 생성/연결한다.
- 요구 항목에 필요한 최소 supporting change만 수행하고 기존 Python 엔진 변경 근거를 기록한다.

## Out of Scope

- Streaming STT, 회의 요약, AI 엔진 C++ 이식, 사양서 28절 후속 기능.
- 다른 티켓의 독립 기능을 함께 구현하지 않는다.

## Dependencies

- TICKET-001

## Acceptance Criteria

- [ ] backend_bridge.py가 input/device와 계약상 출력 경로를 받아 기존 전사 엔진을 호출한다
- [ ] stdout에는 JSON Lines만 출력하고 일반 로그·traceback은 stderr로 분리한다
- [ ] state/progress/segment/completed/error 이벤트를 계약에 맞게 flush하며 완료는 저장 성공 뒤 한 번 출력한다
- [ ] 대용량 Decode·Whisper·OpenVINO 처리를 Python 프로세스에서 수행하고 기존 fallback을 보존한다
- [ ] 관련 검증 결과와 미검증 환경을 기록한다.

## Test Requirements

### Normal Cases

- 모의 Backend로 정상 이벤트 순서·예외·로그 오염 방지 검증

### Error Cases

- 실제 작은 음성으로 CPU smoke test

### Edge Cases

- 빈 입력, 반복 실행 및 해당 기능의 경계 조건을 검증하고 상태/자원 잔류가 없는지 확인한다.

## Files

### Existing

- 실제 소스 저장소는 이번 입력에 포함되지 않았다. 사양서의 기존 Python 파일 설명은 TICKET-001에서 실제 확인한다.

### To Create

- `backend_bridge.py` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)

## Open Questions

- 기존 Python 파일은 수정 대상 후보이며 실제 파일을 읽은 뒤 최소 변경한다.
