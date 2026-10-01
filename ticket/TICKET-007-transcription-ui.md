# TICKET-007: 전사 시작과 결과 화면 연결

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

전사 시작과 결과 화면 연결을 완료하여 1차 음성 녹음·전사 앱의 해당 기능을 검증 가능한 상태로 제공한다.

## Background

프로젝트 목표는 Qt Widgets/qmake/C++17 GUI에서 마이크 녹음 또는 기존 파일을 입력하고, 기존 Python STT와 선택적 화자 분리를 QProcess로 실행하여 결과를 표시·저장하는 것이다. AI 처리와 16 kHz Mono decode는 Python에서 유지한다. 녹음 중 Streaming STT는 1차 범위에 포함하지 않는다.

첨부 DB는 파일 경로와 심볼/시그니처 인덱스이며 소스 본문이나 테스트 결과를 포함하지 않는다. 아래 Existing은 인덱스에 확인된 경로이며 구현 완료를 의미하지 않는다. 실제 저장소에서 기존 구현을 먼저 읽고, 이미 완료된 부분은 재구현하지 않는다. 신규 경로는 제안이며 기존 구조가 있으면 해당 구조를 우선한다.

## Source

- `Qt_Widgets_음성_녹음_전사_UI_개발_사양서.md`: 12, 13, 15, 16, 28절.
- `AGENTS.md`: Development Guide, 기본 개발 환경, 구현 원칙.
- `codegraph.db`: files/nodes 테이블의 `gui/mainwindow.cpp`, `gui/mainwindow.h` 경로/심볼.

## Requirements

- 현재 파일과 Device 기본 AUTO를 옵션 객체로 전달하고 Processing 상태로 전환한다.
- segment를 시간순으로 관리하고 QTextEdit 또는 QPlainTextEdit에 타임스탬프와 텍스트를 표시한다.
- 새 작업 시작 시 이전 결과를 구분/초기화하며 완료와 실패 상태를 반영한다.
- 기본 결과는 읽기 전용이며 긴 음성 AI 처리와 decode를 GUI에서 수행하지 않는다.

## Implementation Scope

- 위 Requirements와 해당 기능 연결에 필요한 최소 변경만 수행한다.
- 아래 Files의 제안 경로를 실제 저장소 구조에 맞게 적용하고 필요한 검증을 수행한다.
- 선행 티켓의 완료 조건과 계약을 확인한 후 변경한다. 기존 CLI 및 관련 정상 동작을 보존한다.

## Out of Scope

- 다른 티켓이 담당하는 기능의 재구현.
- Streaming STT, 실시간 화자 분리, 파형/재생/Timeline, 화자 이름 편집, 검색, 회의 요약, LLM, PDF/DOCX, 최근 목록, Drag & Drop.
- Python AI 엔진의 C++ 이식 및 Python embedding.

## Dependencies

- TICKET-002
- TICKET-005
- TICKET-006

## Acceptance Criteria

- [ ] 선택 파일 전사 결과가 시간과 함께 표시된다.
- [ ] 실행 중 UI가 응답하며 같은 작업이 중복 실행되지 않는다.
- [ ] 새 작업 결과가 이전 작업 결과와 섞이지 않는다.

## Test Requirements

### Normal Cases

- 파일→전사→완료 전체 흐름.

### Error Cases

- Backend 실패 후 재실행.

### Edge Cases

- 빈 결과, 긴 텍스트, HTML처럼 보이는 텍스트의 원문 표시.

## Files

### Existing

- `gui/mainwindow.cpp` — DB 인덱스에서 확인; 소스 본문은 별도 확인 필요.
- `gui/mainwindow.h` — DB 인덱스에서 확인; 소스 본문은 별도 확인 필요.

### To Create

None — 기존 파일 또는 선행 티켓에서 생성한 관련 파일을 수정한다.

## Open Questions

- 현재 UI가 어느 정도 구현되었는지는 DB만으로 확정할 수 없으므로 기존 동작 확인 후 부족한 부분만 변경한다.
