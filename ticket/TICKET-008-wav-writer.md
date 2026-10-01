# TICKET-008: WAV PCM Writer 구현

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

WAV PCM Writer 구현을 완료하여 독립 검증 가능한 결과를 제공한다.

## Background

PROJECT_GOAL.md의 FR-008을 수행하며 사양서 1차 기능 흐름을 구성한다.

## Source

- `Qt Widgets 기반 음성 녹음·전사 UI 개발 사양서.md` — 7–8, 23절
- `PROJECT_GOAL.md` — Functional Requirements > FR-008

## Requirements

- WavWriter가 실제 sample rate/channel/sample format과 일치하는 PCM WAV 헤더를 생성한다
- 종료 시 RIFF/data 길이를 갱신하고 파일을 닫는다
- 파일 생성·쓰기·헤더 갱신 실패를 호출자에게 전달한다
- 16 kHz를 강제하지 않으며 Mono Int16 권장과 장치 지원 형식 간 차이를 명시한다

## Implementation Scope

- 아래 Files의 제안 대상을 실제 저장소 구조에 맞게 생성/연결한다.
- 요구 항목에 필요한 최소 supporting change만 수행하고 기존 Python 엔진 변경 근거를 기록한다.

## Out of Scope

- Streaming STT, 회의 요약, AI 엔진 C++ 이식, 사양서 28절 후속 기능.
- 다른 티켓의 독립 기능을 함께 구현하지 않는다.

## Dependencies

- TICKET-002

## Acceptance Criteria

- [ ] WavWriter가 실제 sample rate/channel/sample format과 일치하는 PCM WAV 헤더를 생성한다
- [ ] 종료 시 RIFF/data 길이를 갱신하고 파일을 닫는다
- [ ] 파일 생성·쓰기·헤더 갱신 실패를 호출자에게 전달한다
- [ ] 16 kHz를 강제하지 않으며 Mono Int16 권장과 장치 지원 형식 간 차이를 명시한다
- [ ] 관련 검증 결과와 미검증 환경을 기록한다.

## Test Requirements

### Normal Cases

- 메모리 PCM fixture로 헤더·sample count·종료 후 Decode 검증

### Error Cases

- 쓰기 실패·빈 녹음·RIFF 한계 경계 검증

### Edge Cases

- 빈 입력, 반복 실행 및 해당 기능의 경계 조건을 검증하고 상태/자원 잔류가 없는지 확인한다.

## Files

### Existing

- 실제 소스 저장소는 이번 입력에 포함되지 않았다. 사양서의 기존 Python 파일 설명은 TICKET-001에서 실제 확인한다.

### To Create

- `gui/src/audio/WavWriter.h` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/src/audio/WavWriter.cpp` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)

## Open Questions

- 장치가 Int16/Mono를 지원하지 않을 때의 변환 또는 거부 정책과 장시간 녹음 한계를 결정한다.
