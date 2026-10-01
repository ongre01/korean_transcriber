# TICKET-012: 진행률과 처리 단계 표시

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

진행률과 처리 단계 표시을 완료하여 1차 음성 녹음·전사 앱의 해당 기능을 검증 가능한 상태로 제공한다.

## Background

프로젝트 목표는 Qt Widgets/qmake/C++17 GUI에서 마이크 녹음 또는 기존 파일을 입력하고, 기존 Python STT와 선택적 화자 분리를 QProcess로 실행하여 결과를 표시·저장하는 것이다. AI 처리와 16 kHz Mono decode는 Python에서 유지한다. 녹음 중 Streaming STT는 1차 범위에 포함하지 않는다.

첨부 DB는 파일 경로와 심볼/시그니처 인덱스이며 소스 본문이나 테스트 결과를 포함하지 않는다. 아래 Existing은 인덱스에 확인된 경로이며 구현 완료를 의미하지 않는다. 실제 저장소에서 기존 구현을 먼저 읽고, 이미 완료된 부분은 재구현하지 않는다. 신규 경로는 제안이며 기존 구조가 있으면 해당 구조를 우선한다.

## Source

- `Qt_Widgets_음성_녹음_전사_UI_개발_사양서.md`: 19절.
- `AGENTS.md`: Development Guide, 기본 개발 환경, 구현 원칙.
- `codegraph.db`: files/nodes 테이블의 `gui/mainwindow.cpp`, `engine/transcribe_npu.py` 경로/심볼.

## Requirements

- Preparing/Loading Model/Decoding Audio/Transcribing/Diarization/Saving Result/Completed 단계를 UI에 반영한다.
- 기존 print_progress/transcribe_windows 구현을 확인하여 진행 이벤트를 제공하고 콘솔 문자열을 Qt에서 분석하지 않는다.
- 신뢰할 진행 수치가 없으면 불확정 표시를 사용한다. 처리 시간/전체 시간은 엔진이 제공한 값으로 표시한다.

## Implementation Scope

- 위 Requirements와 해당 기능 연결에 필요한 최소 변경만 수행한다.
- 아래 Files의 제안 경로를 실제 저장소 구조에 맞게 적용하고 필요한 검증을 수행한다.
- 선행 티켓의 완료 조건과 계약을 확인한 후 변경한다. 기존 CLI 및 관련 정상 동작을 보존한다.

## Out of Scope

- 다른 티켓이 담당하는 기능의 재구현.
- Streaming STT, 실시간 화자 분리, 파형/재생/Timeline, 화자 이름 편집, 검색, 회의 요약, LLM, PDF/DOCX, 최근 목록, Drag & Drop.
- Python AI 엔진의 C++ 이식 및 Python embedding.

## Dependencies

- TICKET-003
- TICKET-007
- TICKET-011

## Acceptance Criteria

- [ ] 단계와 0~100 진행률이 이벤트에 따라 갱신된다.
- [ ] 100% 및 Completed는 저장 성공 이후에만 표시된다.
- [ ] 진행 정보를 계산할 수 없는 단계는 임의 백분율을 표시하지 않는다.

## Test Requirements

### Normal Cases

- 단계/시간/진행률 fixture.

### Error Cases

- 범위 밖 값 및 실패 직전 진행 이벤트.

### Edge Cases

- 전체 시간 0, 지연 이벤트, 새 작업 시 초기화.

## Files

### Existing

- `gui/mainwindow.cpp` — DB 인덱스에서 확인; 소스 본문은 별도 확인 필요.
- `engine/transcribe_npu.py` — DB 인덱스에서 확인; 소스 본문은 별도 확인 필요.

### To Create

None — 기존 파일 또는 선행 티켓에서 생성한 관련 파일을 수정한다.

## Open Questions

- 실제 진행 콜백 제공 방식은 원본 코드 확인이 필요하다.
