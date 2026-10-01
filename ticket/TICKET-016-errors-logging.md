# TICKET-016: 오류 안내와 일별 상세 로그

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P1

## Goal

오류 안내와 일별 상세 로그을 완료하여 1차 음성 녹음·전사 앱의 해당 기능을 검증 가능한 상태로 제공한다.

## Background

프로젝트 목표는 Qt Widgets/qmake/C++17 GUI에서 마이크 녹음 또는 기존 파일을 입력하고, 기존 Python STT와 선택적 화자 분리를 QProcess로 실행하여 결과를 표시·저장하는 것이다. AI 처리와 16 kHz Mono decode는 Python에서 유지한다. 녹음 중 Streaming STT는 1차 범위에 포함하지 않는다.

첨부 DB는 파일 경로와 심볼/시그니처 인덱스이며 소스 본문이나 테스트 결과를 포함하지 않는다. 아래 Existing은 인덱스에 확인된 경로이며 구현 완료를 의미하지 않는다. 실제 저장소에서 기존 구현을 먼저 읽고, 이미 완료된 부분은 재구현하지 않는다. 신규 경로는 제안이며 기존 구조가 있으면 해당 구조를 우선한다.

## Source

- `Qt_Widgets_음성_녹음_전사_UI_개발_사양서.md`: 21, 22절.
- `AGENTS.md`: Development Guide, 기본 개발 환경, 구현 원칙.
- `codegraph.db`: files/nodes 테이블의 `gui/mainwindow.cpp` 경로/심볼.

## Requirements

- 녹음/파일/Backend 오류를 사용자 메시지와 상세 로그로 분리하고 traceback은 로그 보기에서 확인하게 한다.
- logs/app_YYYYMMDD.log에 시작, 장치, 녹음 시작/중지, 입력, Python 명령, 단계, 모델, 전사/화자 분리, 출력, 오류를 기록한다.
- stderr 및 프로세스 오류 상세를 보존하고 오류 후 다시 조작 가능한 상태로 복구한다.
- 실제 기존 fallback을 확인하고 NPU 불가가 정상 fallback인지 실패인지에 따라 메시지를 구분한다.

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
- TICKET-009
- TICKET-015

## Acceptance Criteria

- [ ] 21절 오류 항목 각각에 대응하는 안내 또는 기존 fallback 후 상태 안내가 있다.
- [ ] 오류 안내에 traceback 전체가 노출되지 않으며 상세 로그 보기에서는 확인 가능하다.
- [ ] 날짜별 로그에 명시된 이벤트가 기록되고 GUI가 비정상 종료되지 않는다.

## Test Requirements

### Normal Cases

- 성공 작업 로그와 로그 보기.

### Error Cases

- 각 오류 범주 대역 주입 및 로그 쓰기 실패.

### Edge Cases

- 날짜 변경, 긴 stderr, 연속 실패 후 정상 작업.

## Files

### Existing

- `gui/mainwindow.cpp` — DB 인덱스에서 확인; 소스 본문은 별도 확인 필요.

### To Create

- `gui/logging/AppLogger.h` — 제안 경로.
- `gui/logging/AppLogger.cpp` — 제안 경로.
- `gui/logging/LogDialog.h` — 제안 경로.
- `gui/logging/LogDialog.cpp` — 제안 경로.

## Open Questions

- 로그 보관 기간은 사양서에 없어 자동 삭제 기능은 추가하지 않는다.
