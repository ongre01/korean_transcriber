# TICKET-005: 비동기 QProcess 실행과 JSON Lines 수신

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

비동기 QProcess 실행과 JSON Lines 수신을 완료하여 1차 음성 녹음·전사 앱의 해당 기능을 검증 가능한 상태로 제공한다.

## Background

프로젝트 목표는 Qt Widgets/qmake/C++17 GUI에서 마이크 녹음 또는 기존 파일을 입력하고, 기존 Python STT와 선택적 화자 분리를 QProcess로 실행하여 결과를 표시·저장하는 것이다. AI 처리와 16 kHz Mono decode는 Python에서 유지한다. 녹음 중 Streaming STT는 1차 범위에 포함하지 않는다.

첨부 DB는 파일 경로와 심볼/시그니처 인덱스이며 소스 본문이나 테스트 결과를 포함하지 않는다. 아래 Existing은 인덱스에 확인된 경로이며 구현 완료를 의미하지 않는다. 실제 저장소에서 기존 구현을 먼저 읽고, 이미 완료된 부분은 재구현하지 않는다. 신규 경로는 제안이며 기존 구조가 있으면 해당 구조를 우선한다.

## Source

- `Qt_Widgets_음성_녹음_전사_UI_개발_사양서.md`: 10, 11, 12, 25절.
- `AGENTS.md`: Development Guide, 기본 개발 환경, 구현 원칙.

## Requirements

- QProcess 신호로 시작/stdout/stderr/종료를 관리하고 메인 스레드에서 waitForFinished 등 동기 대기를 하지 않는다.
- Python 프로그램 경로와 QStringList 인자를 분리하고 working directory를 명시한다.
- 바이트 버퍼로 분할 줄과 UTF-8 경계를 보존하고 JSON 파싱 후 typed signal을 발행한다.
- errorOccurred/비정상 exit/completed 누락을 작업 실패로 처리하고 완료 신호를 중복 발행하지 않는다.

## Implementation Scope

- 위 Requirements와 해당 기능 연결에 필요한 최소 변경만 수행한다.
- 아래 Files의 제안 경로를 실제 저장소 구조에 맞게 적용하고 필요한 검증을 수행한다.
- 선행 티켓의 완료 조건과 계약을 확인한 후 변경한다. 기존 CLI 및 관련 정상 동작을 보존한다.

## Out of Scope

- 다른 티켓이 담당하는 기능의 재구현.
- Streaming STT, 실시간 화자 분리, 파형/재생/Timeline, 화자 이름 편집, 검색, 회의 요약, LLM, PDF/DOCX, 최근 목록, Drag & Drop.
- Python AI 엔진의 C++ 이식 및 Python embedding.

## Dependencies

- TICKET-001
- TICKET-003
- TICKET-004

## Acceptance Criteria

- [ ] 모의 Bridge가 지연 출력 중에도 창이 조작 가능하다.
- [ ] 한 번에 여러 줄 및 여러 번에 나뉜 한 줄이 각각 정확히 한 번 전달된다.
- [ ] 실행 실패와 비정상 종료가 오류 신호로 전달된다.

## Test Requirements

### Normal Cases

- state/progress/segment/completed 연속 수신.

### Error Cases

- Python 경로 없음, malformed JSON, crash.

### Edge Cases

- UTF-8 문자 분할, 줄 끝 CRLF, 마지막 버퍼와 중복 완료.

## Files

### Existing

None — 이 티켓의 전용 모듈은 첨부 인덱스에서 확인되지 않음. 구현 전에 실제 저장소를 확인한다.

### To Create

- `gui/backend/BackendProcess.h` — 제안 경로.
- `gui/backend/BackendProcess.cpp` — 제안 경로.
- `tests/gui/test_backend_process.cpp` — 제안 경로.

## Open Questions

- 미지 이벤트 정책은 TICKET-003 계약을 따른다.
