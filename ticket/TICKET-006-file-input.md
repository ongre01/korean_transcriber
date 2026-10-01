# TICKET-006: 기존 음성 파일 선택과 메타데이터

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

기존 음성 파일 선택과 메타데이터을 완료하여 1차 음성 녹음·전사 앱의 해당 기능을 검증 가능한 상태로 제공한다.

## Background

프로젝트 목표는 Qt Widgets/qmake/C++17 GUI에서 마이크 녹음 또는 기존 파일을 입력하고, 기존 Python STT와 선택적 화자 분리를 QProcess로 실행하여 결과를 표시·저장하는 것이다. AI 처리와 16 kHz Mono decode는 Python에서 유지한다. 녹음 중 Streaming STT는 1차 범위에 포함하지 않는다.

첨부 DB는 파일 경로와 심볼/시그니처 인덱스이며 소스 본문이나 테스트 결과를 포함하지 않는다. 아래 Existing은 인덱스에 확인된 경로이며 구현 완료를 의미하지 않는다. 실제 저장소에서 기존 구현을 먼저 읽고, 이미 완료된 부분은 재구현하지 않는다. 신규 경로는 제안이며 기존 구조가 있으면 해당 구조를 우선한다.

## Source

- `Qt_Widgets_음성_녹음_전사_UI_개발_사양서.md`: 9, 25절.
- `AGENTS.md`: Development Guide, 기본 개발 환경, 구현 원칙.
- `codegraph.db`: files/nodes 테이블의 `gui/mainwindow.cpp` 경로/심볼.

## Requirements

- QFileDialog에서 wav/mp3/m4a/aac/flac/ogg/mp4 필터를 제공한다. 확장자만으로 decode 성공을 보장하지 않는다.
- 파일 이름/경로/크기/재생 시간을 표시하고 유효한 선택을 CurrentInputFile에 반영한다.
- 재생 시간 조회는 Python PyAV 또는 비동기 조회를 사용하여 GUI를 막지 않는다. 전사 이벤트와 혼동하지 않는 메타데이터 경로를 구현한다.

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

## Acceptance Criteria

- [ ] 선택한 파일 정보가 표시되고 InputReady에서 전사 버튼이 활성화된다.
- [ ] 대화상자 취소 시 기존 입력이 유지된다.
- [ ] 실제 decode 실패는 사용자 오류로 이어지고 GUI가 종료되지 않는다.

## Test Requirements

### Normal Cases

- WAV/MP3/M4A 실제 fixture.

### Error Cases

- 삭제된 파일, 손상된 파일.

### Edge Cases

- 공백/한글 경로, 재생시간 조회 실패, 조회 중 새 파일 선택.

## Files

### Existing

- `gui/mainwindow.cpp` — DB 인덱스에서 확인; 소스 본문은 별도 확인 필요.

### To Create

- `gui/input/AudioFileInfo.h` — 제안 경로.
- `gui/input/AudioFileInfo.cpp` — 제안 경로.

## Open Questions

- 메타데이터 조회 계약은 TICKET-003과 호환되는 추가 명령으로 정하는 제안이다.
