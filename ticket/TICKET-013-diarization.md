# TICKET-013: 기존 화자 분리와 Speaker 결과 연결

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

기존 화자 분리와 Speaker 결과 연결을 완료하여 독립 검증 가능한 결과를 제공한다.

## Background

PROJECT_GOAL.md의 FR-013을 수행하며 사양서 1차 기능 흐름을 구성한다.

## Source

- `Qt Widgets 기반 음성 녹음·전사 UI 개발 사양서.md` — 2, 11, 13, 15–16절
- `PROJECT_GOAL.md` — Functional Requirements > FR-013

## Requirements

- 화자 분리 활성 시 기존 diarization 및 assign_speakers 처리에 연결한다
- Auto 또는 지정 화자 수를 기존 코드의 지원 방식으로 전달한다
- 화자 할당 완료 결과를 GUI에 표시하여 초기 Segment와 최종 Segment가 중복되지 않게 한다
- 화자 분리 OFF 경로는 추가 모델 없이 전사를 완료한다
- 화자 모델 누락과 분리 실패를 계약상 오류로 보고한다

## Implementation Scope

- 아래 Files의 제안 대상을 실제 저장소 구조에 맞게 생성/연결한다.
- 요구 항목에 필요한 최소 supporting change만 수행하고 기존 Python 엔진 변경 근거를 기록한다.

## Out of Scope

- Streaming STT, 회의 요약, AI 엔진 C++ 이식, 사양서 28절 후속 기능.
- 다른 티켓의 독립 기능을 함께 구현하지 않는다.

## Dependencies

- TICKET-003
- TICKET-006
- TICKET-012

## Acceptance Criteria

- [ ] 화자 분리 활성 시 기존 diarization 및 assign_speakers 처리에 연결한다
- [ ] Auto 또는 지정 화자 수를 기존 코드의 지원 방식으로 전달한다
- [ ] 화자 할당 완료 결과를 GUI에 표시하여 초기 Segment와 최종 Segment가 중복되지 않게 한다
- [ ] 화자 분리 OFF 경로는 추가 모델 없이 전사를 완료한다
- [ ] 화자 모델 누락과 분리 실패를 계약상 오류로 보고한다
- [ ] 관련 검증 결과와 미검증 환경을 기록한다.

## Test Requirements

### Normal Cases

- 모의 화자 결과와 실제 2인 음성으로 Speaker 구분 확인

### Error Cases

- OFF·모델 없음·불충분 음성 검증

### Edge Cases

- 빈 입력, 반복 실행 및 해당 기능의 경계 조건을 검증하고 상태/자원 잔류가 없는지 확인한다.

## Files

### Existing

- 실제 소스 저장소는 이번 입력에 포함되지 않았다. 사양서의 기존 Python 파일 설명은 TICKET-001에서 실제 확인한다.

### To Create

- `backend_bridge.py` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/src/result/TranscriptView.cpp` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)

## Open Questions

- 사전 STT Segment와 최종 화자 Segment의 교체/지연 전달 정책은 TICKET-001에서 확정한다.
