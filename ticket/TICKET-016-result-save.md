# TICKET-016: TXT 저장과 결과 폴더 열기

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

TXT 저장과 결과 폴더 열기을 완료하여 독립 검증 가능한 결과를 제공한다.

## Background

PROJECT_GOAL.md의 FR-016을 수행하며 사양서 1차 기능 흐름을 구성한다.

## Source

- `Qt Widgets 기반 음성 녹음·전사 UI 개발 사양서.md` — 5, 11, 27, 30절
- `PROJECT_GOAL.md` — Functional Requirements > FR-016

## Requirements

- 완료 결과를 UTF-8 TXT로 저장할 경로를 선택하게 한다
- 시간·Speaker·문장을 화면 의미와 일치하게 저장한다
- 완료 파일 경로와 출력 디렉토리를 관리하고 결과 폴더 열기를 제공한다
- 쓰기 실패·잘못된 완료 경로를 사용자 오류로 전달한다

## Implementation Scope

- 아래 Files의 제안 대상을 실제 저장소 구조에 맞게 생성/연결한다.
- 요구 항목에 필요한 최소 supporting change만 수행하고 기존 Python 엔진 변경 근거를 기록한다.

## Out of Scope

- Streaming STT, 회의 요약, AI 엔진 C++ 이식, 사양서 28절 후속 기능.
- 다른 티켓의 독립 기능을 함께 구현하지 않는다.

## Dependencies

- TICKET-006
- TICKET-007
- TICKET-013

## Acceptance Criteria

- [ ] 완료 결과를 UTF-8 TXT로 저장할 경로를 선택하게 한다
- [ ] 시간·Speaker·문장을 화면 의미와 일치하게 저장한다
- [ ] 완료 파일 경로와 출력 디렉토리를 관리하고 결과 폴더 열기를 제공한다
- [ ] 쓰기 실패·잘못된 완료 경로를 사용자 오류로 전달한다
- [ ] 관련 검증 결과와 미검증 환경을 기록한다.

## Test Requirements

### Normal Cases

- 한국어와 화자 ON/OFF TXT 확인

### Error Cases

- 쓰기 거부·폴더 없음·기존 파일 덮어쓰기 취소 검증

### Edge Cases

- 빈 입력, 반복 실행 및 해당 기능의 경계 조건을 검증하고 상태/자원 잔류가 없는지 확인한다.

## Files

### Existing

- 실제 소스 저장소는 이번 입력에 포함되지 않았다. 사양서의 기존 Python 파일 설명은 TICKET-001에서 실제 확인한다.

### To Create

- `gui/src/result/TranscriptExporter.h` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/src/result/TranscriptExporter.cpp` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `gui/src/MainWindow.cpp` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)

## Open Questions

- completed의 srt_file은 기존 Backend 산출물 정보를 수용한다. SRT 전용 Export UI는 필수 범위에 추가하지 않는다.
