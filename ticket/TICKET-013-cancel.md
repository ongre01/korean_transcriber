# TICKET-013: 전사 취소와 프로세스 수명 관리

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

전사 취소와 프로세스 수명 관리을 완료하여 1차 음성 녹음·전사 앱의 해당 기능을 검증 가능한 상태로 제공한다.

## Background

프로젝트 목표는 Qt Widgets/qmake/C++17 GUI에서 마이크 녹음 또는 기존 파일을 입력하고, 기존 Python STT와 선택적 화자 분리를 QProcess로 실행하여 결과를 표시·저장하는 것이다. AI 처리와 16 kHz Mono decode는 Python에서 유지한다. 녹음 중 Streaming STT는 1차 범위에 포함하지 않는다.

첨부 DB는 파일 경로와 심볼/시그니처 인덱스이며 소스 본문이나 테스트 결과를 포함하지 않는다. 아래 Existing은 인덱스에 확인된 경로이며 구현 완료를 의미하지 않는다. 실제 저장소에서 기존 구현을 먼저 읽고, 이미 완료된 부분은 재구현하지 않는다. 신규 경로는 제안이며 기존 구조가 있으면 해당 구조를 우선한다.

## Source

- `Qt_Widgets_음성_녹음_전사_UI_개발_사양서.md`: 20, 25절.
- `AGENTS.md`: Development Guide, 기본 개발 환경, 구현 원칙.
- `codegraph.db`: files/nodes 테이블의 `gui/mainwindow.cpp` 경로/심볼.

## Requirements

- 취소 시 terminate를 호출하고 비동기 타이머 만료 후 살아 있으면 kill을 호출한다.
- 프로세스 종료 확인 후 유효 입력이 있으면 InputReady, 없으면 Idle로 복구한다. 입력 음성 파일은 삭제하지 않는다.
- 취소와 완료 경합에서 종결 상태를 한 번만 적용하고 다음 작업에 이전 이벤트가 유입되지 않도록 한다.
- 창 종료 시 진행 중 프로세스와 녹음 리소스가 남지 않도록 관련 소유 객체와 연계한다.

## Implementation Scope

- 위 Requirements와 해당 기능 연결에 필요한 최소 변경만 수행한다.
- 아래 Files의 제안 경로를 실제 저장소 구조에 맞게 적용하고 필요한 검증을 수행한다.
- 선행 티켓의 완료 조건과 계약을 확인한 후 변경한다. 기존 CLI 및 관련 정상 동작을 보존한다.

## Out of Scope

- 다른 티켓이 담당하는 기능의 재구현.
- Streaming STT, 실시간 화자 분리, 파형/재생/Timeline, 화자 이름 편집, 검색, 회의 요약, LLM, PDF/DOCX, 최근 목록, Drag & Drop.
- Python AI 엔진의 C++ 이식 및 Python embedding.

## Dependencies

- TICKET-005
- TICKET-007

## Acceptance Criteria

- [ ] terminate에 응답하는 대역과 무시하는 대역 모두 취소 후 프로세스가 종료된다.
- [ ] 취소 후 입력 파일 내용이 변경되지 않고 재전사 가능하다.
- [ ] 늦은 completed가 취소 결과를 완료로 바꾸지 않는다.

## Test Requirements

### Normal Cases

- 실행 중 취소와 재시작.

### Error Cases

- terminate 무응답→kill.

### Edge Cases

- 시작 직후 취소, 완료와 동시 취소, 반복 취소, 창 종료.

## Files

### Existing

- `gui/mainwindow.cpp` — DB 인덱스에서 확인; 소스 본문은 별도 확인 필요.

### To Create

- `tests/gui/test_backend_cancel.cpp` — 제안 경로.

## Open Questions

- 강제 종료 대기 시간 및 부분 출력 파일 보존 정책은 사양서에 없어 구현 시 명시한다.
