# TICKET-001: 기존 Backend 인터페이스와 JSON Lines 계약 확정

## Metadata

- Type: Validation
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

기존 Backend 인터페이스와 JSON Lines 계약 확정을 완료하여 독립 검증 가능한 결과를 제공한다.

## Background

PROJECT_GOAL.md의 FR-001을 수행하며 사양서 1차 기능 흐름을 구성한다.

## Source

- `Qt Widgets 기반 음성 녹음·전사 UI 개발 사양서.md` — 2, 10–13, 19절
- `PROJECT_GOAL.md` — Functional Requirements > FR-001

## Requirements

- 실제 저장소에서 transcribe_npu.py와 diarization.py의 호출 인터페이스·Segment 구조·저장 방식·fallback 정책을 확인하고 근거를 기록한다
- state/progress/segment/completed/error 이벤트 필드·타입·UTF-8 인코딩·줄 구분·flush 정책을 문서화한다
- 진행 단계, progress 0~100, 화자 분리 OFF의 speaker 표현, 완료 파일 경로 기준을 정의한다
- 파일 정보 조회 방식과 진행 시간 필드를 기존 코드에 맞추어 정의한다

## Implementation Scope

- 아래 Files의 제안 대상을 실제 저장소 구조에 맞게 생성/연결한다.
- 요구 항목에 필요한 최소 supporting change만 수행하고 기존 Python 엔진 변경 근거를 기록한다.

## Out of Scope

- Streaming STT, 회의 요약, AI 엔진 C++ 이식, 사양서 28절 후속 기능.
- 다른 티켓의 독립 기능을 함께 구현하지 않는다.

## Dependencies

None

## Acceptance Criteria

- [ ] 실제 저장소에서 transcribe_npu.py와 diarization.py의 호출 인터페이스·Segment 구조·저장 방식·fallback 정책을 확인하고 근거를 기록한다
- [ ] state/progress/segment/completed/error 이벤트 필드·타입·UTF-8 인코딩·줄 구분·flush 정책을 문서화한다
- [ ] 진행 단계, progress 0~100, 화자 분리 OFF의 speaker 표현, 완료 파일 경로 기준을 정의한다
- [ ] 파일 정보 조회 방식과 진행 시간 필드를 기존 코드에 맞추어 정의한다
- [ ] 관련 검증 결과와 미검증 환경을 기록한다.

## Test Requirements

### Normal Cases

- 실제 함수 시그니처와 계약 예제가 일치하는지 검토

### Error Cases

- 화자 OFF·오류·완료 이벤트 예제 검증

### Edge Cases

- 빈 입력, 반복 실행 및 해당 기능의 경계 조건을 검증하고 상태/자원 잔류가 없는지 확인한다.

## Files

### Existing

- 실제 소스 저장소는 이번 입력에 포함되지 않았다. 사양서의 기존 Python 파일 설명은 TICKET-001에서 실제 확인한다.

### To Create

- `docs/backend-contract.md` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)

## Open Questions

- 소스코드는 첨부되지 않았다. 저장소 확보 후 확인 필수. 기존 기능은 사양서의 설명이며 독립 검증되지 않았다.
