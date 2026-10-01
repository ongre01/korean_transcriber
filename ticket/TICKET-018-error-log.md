# TICKET-018: 오류 안내와 일별 로그

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

오류 안내와 일별 로그을 완료하여 독립 검증 가능한 결과를 제공한다.

## Background

PROJECT_GOAL.md의 FR-018을 수행하며 사양서 1차 기능 흐름을 구성한다.

## Source

- `Qt Widgets 기반 음성 녹음·전사 UI 개발 사양서.md` — 21–22절
- `PROJECT_GOAL.md` — Functional Requirements > FR-018

## Requirements

- 녹음/파일/Python/모듈/모델/OpenVINO/장치/Whisper/화자 오류를 읽기 쉬운 UI 메시지로 전달한다
- traceback은 기본 오류 안내에 표시하지 않고 상세 로그 보기에서 확인하게 한다
- logs/app_YYYYMMDD.log에 사양서의 시작·장치·녹음·입력·Python 명령·Backend 단계·산출물·오류를 기록한다
- stderr 진단을 보존하고 오류 뒤 자원을 정리하여 GUI를 유지한다

## Implementation Scope

- 아래 Files의 제안 대상을 실제 저장소 구조에 맞게 생성/연결한다.
- 요구 항목에 필요한 최소 supporting change만 수행하고 기존 Python 엔진 변경 근거를 기록한다.

## Out of Scope

- Streaming STT, 회의 요약, AI 엔진 C++ 이식, 사양서 28절 후속 기능.
- 다른 티켓의 독립 기능을 함께 구현하지 않는다.

## Dependencies

- TICKET-007
- TICKET-011
- TICKET-013
- TICKET-015
- TICKET-016
- TICKET-017

## Acceptance Criteria

- [ ] 녹음/파일/Python/모듈/모델/OpenVINO/장치/Whisper/화자 오류를 읽기 쉬운 UI 메시지로 전달한다
- [ ] traceback은 기본 오류 안내에 표시하지 않고 상세 로그 보기에서 확인하게 한다
- [ ] logs/app_YYYYMMDD.log에 사양서의 시작·장치·녹음·입력·Python 명령·Backend 단계·산출물·오류를 기록한다
- [ ] stderr 진단을 보존하고 오류 뒤 자원을 정리하여 GUI를 유지한다
- [ ] 관련 검증 결과와 미검증 환경을 기록한다.

## Test Requirements

### Normal Cases

- 각 오류 범주의 모의 실패 주입과 로그 날짜/경로 확인

### Error Cases

- 로그 쓰기 실패에도 GUI 유지 검증

### Edge Cases

- 빈 입력, 반복 실행 및 해당 기능의 경계 조건을 검증하고 상태/자원 잔류가 없는지 확인한다.

## Files

### Existing

- 실제 소스 저장소는 이번 입력에 포함되지 않았다. 사양서의 기존 Python 파일 설명은 TICKET-001에서 실제 확인한다.

### To Create

- `gui/src/logging/AppLogger.h` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/src/logging/AppLogger.cpp` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/src/MainWindow.cpp` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)

## Open Questions

- 프로그램 데이터 경로와 상대 디렉토리 예시의 차이는 플랫폼별 쓰기 가능한 경로로 문서화한다.
