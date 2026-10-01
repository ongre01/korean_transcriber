# TICKET-001: 기존 GUI 보존 및 qmake 빌드 구성

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

기존 GUI 보존 및 qmake 빌드 구성을 완료하여 1차 음성 녹음·전사 앱의 해당 기능을 검증 가능한 상태로 제공한다.

## Background

프로젝트 목표는 Qt Widgets/qmake/C++17 GUI에서 마이크 녹음 또는 기존 파일을 입력하고, 기존 Python STT와 선택적 화자 분리를 QProcess로 실행하여 결과를 표시·저장하는 것이다. AI 처리와 16 kHz Mono decode는 Python에서 유지한다. 녹음 중 Streaming STT는 1차 범위에 포함하지 않는다.

첨부 DB는 파일 경로와 심볼/시그니처 인덱스이며 소스 본문이나 테스트 결과를 포함하지 않는다. 아래 Existing은 인덱스에 확인된 경로이며 구현 완료를 의미하지 않는다. 실제 저장소에서 기존 구현을 먼저 읽고, 이미 완료된 부분은 재구현하지 않는다. 신규 경로는 제안이며 기존 구조가 있으면 해당 구조를 우선한다.

## Source

- `Qt_Widgets_음성_녹음_전사_UI_개발_사양서.md`: 23, 24, 31절.
- `AGENTS.md`: Development Guide, 기본 개발 환경, 구현 원칙.
- `codegraph.db`: files/nodes 테이블의 `gui/main.cpp`, `gui/mainwindow.cpp`, `gui/mainwindow.h` 경로/심볼.

## Requirements

- 실제 저장소의 .pro, .ui, 리소스를 먼저 확인하고 기존 MainWindow를 확장한다. 사양서의 src/MainWindow.cpp 예시로 불필요한 이동이나 중복 클래스를 만들지 않는다.
- Qt Widgets, Multimedia, C++17, TARGET=AudioTranscriber를 qmake에 설정한다.
- 실제 Qt 버전과 대상 OS를 기록하고 해당 버전에 맞는 오디오 구현을 선택한다.

## Implementation Scope

- 위 Requirements와 해당 기능 연결에 필요한 최소 변경만 수행한다.
- 아래 Files의 제안 경로를 실제 저장소 구조에 맞게 적용하고 필요한 검증을 수행한다.
- 선행 티켓의 완료 조건과 계약을 확인한 후 변경한다. 기존 CLI 및 관련 정상 동작을 보존한다.

## Out of Scope

- 다른 티켓이 담당하는 기능의 재구현.
- Streaming STT, 실시간 화자 분리, 파형/재생/Timeline, 화자 이름 편집, 검색, 회의 요약, LLM, PDF/DOCX, 최근 목록, Drag & Drop.
- Python AI 엔진의 C++ 이식 및 Python embedding.

## Dependencies

None

## Acceptance Criteria

- [ ] 선택한 Qt 환경에서 qmake 빌드 후 기존 창이 실행된다.
- [ ] 기존 Python 엔진 파일과 CLI 진입점이 보존된다.

## Test Requirements

### Normal Cases

- clean build 및 창 실행.

### Error Cases

- Multimedia 미설치 시 의존성 원인이 빌드 기록에 드러난다.

### Edge Cases

- 기존 폼과 신규 클래스의 중복 등록이 없다.

## Files

### Existing

- `gui/main.cpp` — DB 인덱스에서 확인; 소스 본문은 별도 확인 필요.
- `gui/mainwindow.cpp` — DB 인덱스에서 확인; 소스 본문은 별도 확인 필요.
- `gui/mainwindow.h` — DB 인덱스에서 확인; 소스 본문은 별도 확인 필요.

### To Create

- `gui/AudioTranscriber.pro` — 제안 경로.

## Open Questions

- 대상 OS와 Qt 버전은 첨부에 확정되어 있지 않다. Qt 5/6 양쪽 지원이 필수인지 구현 전에 결정한다.
