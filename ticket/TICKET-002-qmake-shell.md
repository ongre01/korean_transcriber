# TICKET-002: qmake GUI 프로젝트 골격 생성

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

qmake GUI 프로젝트 골격 생성을 완료하여 독립 검증 가능한 결과를 제공한다.

## Background

PROJECT_GOAL.md의 FR-002을 수행하며 사양서 1차 기능 흐름을 구성한다.

## Source

- `Qt Widgets 기반 음성 녹음·전사 UI 개발 사양서.md` — 4–5, 23–26절
- `PROJECT_GOAL.md` — Functional Requirements > FR-002

## Requirements

- core/gui/widgets/multimedia와 C++17을 설정한 AudioTranscriber.pro를 생성한다
- MainWindow에 입력·옵션·진행·결과 영역과 SettingsDialog 진입점을 구성한다
- UI에 AI 처리 코드를 넣지 않고 후속 컴포넌트 연결점을 제공한다

## Implementation Scope

- 아래 Files의 제안 대상을 실제 저장소 구조에 맞게 생성/연결한다.
- 요구 항목에 필요한 최소 supporting change만 수행하고 기존 Python 엔진 변경 근거를 기록한다.

## Out of Scope

- Streaming STT, 회의 요약, AI 엔진 C++ 이식, 사양서 28절 후속 기능.
- 다른 티켓의 독립 기능을 함께 구현하지 않는다.

## Dependencies

- TICKET-001

## Acceptance Criteria

- [ ] core/gui/widgets/multimedia와 C++17을 설정한 AudioTranscriber.pro를 생성한다
- [ ] MainWindow에 입력·옵션·진행·결과 영역과 SettingsDialog 진입점을 구성한다
- [ ] UI에 AI 처리 코드를 넣지 않고 후속 컴포넌트 연결점을 제공한다
- [ ] 관련 검증 결과와 미검증 환경을 기록한다.

## Test Requirements

### Normal Cases

- 선택한 Qt Kit에서 qmake 빌드·실행·창 리사이즈 확인

### Error Cases

- 요구 조건의 오류 입력/실패 경로에서 성공 상태로 오인하지 않는지 확인한다.

### Edge Cases

- 빈 입력, 반복 실행 및 해당 기능의 경계 조건을 검증하고 상태/자원 잔류가 없는지 확인한다.

## Files

### Existing

- 실제 소스 저장소는 이번 입력에 포함되지 않았다. 사양서의 기존 Python 파일 설명은 TICKET-001에서 실제 확인한다.

### To Create

- `gui/AudioTranscriber.pro` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/src/main.cpp` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/src/MainWindow.h` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/src/MainWindow.cpp` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/forms/MainWindow.ui` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/forms/SettingsDialog.ui` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/resources/resources.qrc` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)

## Open Questions

- 지원 OS와 Qt 최소 버전 및 Qt 5/6 동시 지원 여부를 확정한다.
