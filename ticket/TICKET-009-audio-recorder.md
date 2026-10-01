# TICKET-009: 마이크 선택과 비동기 녹음 구현

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

마이크 선택과 비동기 녹음 구현을 완료하여 독립 검증 가능한 결과를 제공한다.

## Background

PROJECT_GOAL.md의 FR-009을 수행하며 사양서 1차 기능 흐름을 구성한다.

## Source

- `Qt Widgets 기반 음성 녹음·전사 UI 개발 사양서.md` — 6–8, 25–26절
- `PROJECT_GOAL.md` — Functional Requirements > FR-009

## Requirements

- AudioRecorder에서 마이크 목록과 선택 기능을 제공한다
- Qt 6 QAudioSource 또는 Qt 5 QAudioInput을 확정한 지원 범위에 맞추어 캡슐화한다
- 지원 장치 형식을 선택하여 비동기 capture 데이터를 WavWriter로 전달한다
- 녹음 시작/중지/오류 signal과 isRecording/outputFile을 제공한다
- 장치 없음·권한 거부·초기화 실패·녹음 중 장치 제거를 오류로 전달하고 자원을 해제한다

## Implementation Scope

- 아래 Files의 제안 대상을 실제 저장소 구조에 맞게 생성/연결한다.
- 요구 항목에 필요한 최소 supporting change만 수행하고 기존 Python 엔진 변경 근거를 기록한다.

## Out of Scope

- Streaming STT, 회의 요약, AI 엔진 C++ 이식, 사양서 28절 후속 기능.
- 다른 티켓의 독립 기능을 함께 구현하지 않는다.

## Dependencies

- TICKET-008

## Acceptance Criteria

- [ ] AudioRecorder에서 마이크 목록과 선택 기능을 제공한다
- [ ] Qt 6 QAudioSource 또는 Qt 5 QAudioInput을 확정한 지원 범위에 맞추어 캡슐화한다
- [ ] 지원 장치 형식을 선택하여 비동기 capture 데이터를 WavWriter로 전달한다
- [ ] 녹음 시작/중지/오류 signal과 isRecording/outputFile을 제공한다
- [ ] 장치 없음·권한 거부·초기화 실패·녹음 중 장치 제거를 오류로 전달하고 자원을 해제한다
- [ ] 관련 검증 결과와 미검증 환경을 기록한다.

## Test Requirements

### Normal Cases

- 실제 마이크 시작/중지·연속 녹음·권한 거부·장치 제거 검증

### Error Cases

- 생성 WAV Decode 확인

### Edge Cases

- 빈 입력, 반복 실행 및 해당 기능의 경계 조건을 검증하고 상태/자원 잔류가 없는지 확인한다.

## Files

### Existing

- 실제 소스 저장소는 이번 입력에 포함되지 않았다. 사양서의 기존 Python 파일 설명은 TICKET-001에서 실제 확인한다.

### To Create

- `gui/src/audio/AudioRecorder.h` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/src/audio/AudioRecorder.cpp` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)

## Open Questions

- 권한 설정은 확정한 OS와 Qt 버전에 맞추어 적용한다.
