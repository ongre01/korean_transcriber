# TICKET-002: 기본 화면과 애플리케이션 상태 제어

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

기본 화면과 애플리케이션 상태 제어을 완료하여 1차 음성 녹음·전사 앱의 해당 기능을 검증 가능한 상태로 제공한다.

## Background

프로젝트 목표는 Qt Widgets/qmake/C++17 GUI에서 마이크 녹음 또는 기존 파일을 입력하고, 기존 Python STT와 선택적 화자 분리를 QProcess로 실행하여 결과를 표시·저장하는 것이다. AI 처리와 16 kHz Mono decode는 Python에서 유지한다. 녹음 중 Streaming STT는 1차 범위에 포함하지 않는다.

첨부 DB는 파일 경로와 심볼/시그니처 인덱스이며 소스 본문이나 테스트 결과를 포함하지 않는다. 아래 Existing은 인덱스에 확인된 경로이며 구현 완료를 의미하지 않는다. 실제 저장소에서 기존 구현을 먼저 읽고, 이미 완료된 부분은 재구현하지 않는다. 신규 경로는 제안이며 기존 구조가 있으면 해당 구조를 우선한다.

## Source

- `Qt_Widgets_음성_녹음_전사_UI_개발_사양서.md`: 5, 17, 18, 24절.
- `AGENTS.md`: Development Guide, 기본 개발 환경, 구현 원칙.
- `codegraph.db`: files/nodes 테이블의 `gui/mainwindow.cpp`, `gui/mainwindow.h` 경로/심볼.

## Requirements

- 입력 방식, 마이크, 녹음 시간/레벨, 파일 정보, 처리 옵션, 진행 표시, 결과, 저장/폴더 버튼 영역을 구성한다.
- Idle/Recording/InputReady/Processing/Completed/Error 상태와 중앙 버튼 정책을 둔다.
- Idle에서 전사/중지/취소 비활성, Recording에서 파일선택/전사 비활성, Processing에서 취소만 작업 제어로 활성화한다.

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

## Acceptance Criteria

- [ ] 모의 상태 전환에서 사양서 18절의 버튼 정책이 모두 일치한다.
- [ ] 입력 존재 여부에 따라 완료/오류 후 다시 입력 선택 또는 전사를 시작할 수 있다.

## Test Requirements

### Normal Cases

- Idle→InputReady→Processing→Completed.

### Error Cases

- Processing→Error 후 재시도.

### Edge Cases

- 연속 클릭이 중복 작업을 생성하지 않는다.

## Files

### Existing

- `gui/mainwindow.cpp` — DB 인덱스에서 확인; 소스 본문은 별도 확인 필요.
- `gui/mainwindow.h` — DB 인덱스에서 확인; 소스 본문은 별도 확인 필요.

### To Create

- `gui/app/AppState.h` — 제안 경로.

## Open Questions

- Completed/Error의 세부 버튼 정책은 입력 파일 유효성을 기준으로 정하는 제안이다.
