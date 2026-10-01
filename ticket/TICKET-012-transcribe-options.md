# TICKET-012: Device와 화자 옵션 UI

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

Device와 화자 옵션 UI을 완료하여 독립 검증 가능한 결과를 제공한다.

## Background

PROJECT_GOAL.md의 FR-012을 수행하며 사양서 1차 기능 흐름을 구성한다.

## Source

- `Qt Widgets 기반 음성 녹음·전사 UI 개발 사양서.md` — 5, 12–13절
- `PROJECT_GOAL.md` — Functional Requirements > FR-012

## Requirements

- AUTO/NPU/CPU/GPU 선택을 제공하고 기본 Device는 AUTO로 둔다
- 화자 분리 ON/OFF와 화자 수 Auto/2/3/4/5를 제공한다
- 옵션을 TranscribeOptions와 Bridge 인자로 전달한다
- 화자 분리 OFF에서는 화자 수를 처리에 적용하지 않고 실행 중 옵션 변경이 현재 작업에 영향을 주지 않게 한다

## Implementation Scope

- 아래 Files의 제안 대상을 실제 저장소 구조에 맞게 생성/연결한다.
- 요구 항목에 필요한 최소 supporting change만 수행하고 기존 Python 엔진 변경 근거를 기록한다.

## Out of Scope

- Streaming STT, 회의 요약, AI 엔진 C++ 이식, 사양서 28절 후속 기능.
- 다른 티켓의 독립 기능을 함께 구현하지 않는다.

## Dependencies

- TICKET-007

## Acceptance Criteria

- [ ] AUTO/NPU/CPU/GPU 선택을 제공하고 기본 Device는 AUTO로 둔다
- [ ] 화자 분리 ON/OFF와 화자 수 Auto/2/3/4/5를 제공한다
- [ ] 옵션을 TranscribeOptions와 Bridge 인자로 전달한다
- [ ] 화자 분리 OFF에서는 화자 수를 처리에 적용하지 않고 실행 중 옵션 변경이 현재 작업에 영향을 주지 않게 한다
- [ ] 관련 검증 결과와 미검증 환경을 기록한다.

## Test Requirements

### Normal Cases

- 각 Device와 화자 수 조합의 실제 전달 인자 확인

### Error Cases

- 화자 OFF·실행 중 변경 검증

### Edge Cases

- 빈 입력, 반복 실행 및 해당 기능의 경계 조건을 검증하고 상태/자원 잔류가 없는지 확인한다.

## Files

### Existing

- 실제 소스 저장소는 이번 입력에 포함되지 않았다. 사양서의 기존 Python 파일 설명은 TICKET-001에서 실제 확인한다.

### To Create

- `gui/src/MainWindow.cpp` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/src/backend/TranscribeOptions.h` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)

## Open Questions

- 5절 그림의 Device 목록보다 13절 상세 목록을 따른다. 화자 분리 기본 ON/OFF는 미정이다.
