# TICKET-014: TXT 결과 저장 및 결과 폴더 열기

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

TXT 결과 저장 및 결과 폴더 열기을 완료하여 1차 음성 녹음·전사 앱의 해당 기능을 검증 가능한 상태로 제공한다.

## Background

프로젝트 목표는 Qt Widgets/qmake/C++17 GUI에서 마이크 녹음 또는 기존 파일을 입력하고, 기존 Python STT와 선택적 화자 분리를 QProcess로 실행하여 결과를 표시·저장하는 것이다. AI 처리와 16 kHz Mono decode는 Python에서 유지한다. 녹음 중 Streaming STT는 1차 범위에 포함하지 않는다.

첨부 DB는 파일 경로와 심볼/시그니처 인덱스이며 소스 본문이나 테스트 결과를 포함하지 않는다. 아래 Existing은 인덱스에 확인된 경로이며 구현 완료를 의미하지 않는다. 실제 저장소에서 기존 구현을 먼저 읽고, 이미 완료된 부분은 재구현하지 않는다. 신규 경로는 제안이며 기존 구조가 있으면 해당 구조를 우선한다.

## Source

- `Qt_Widgets_음성_녹음_전사_UI_개발_사양서.md`: 5, 11, 15, 26절.
- `AGENTS.md`: Development Guide, 기본 개발 환경, 구현 원칙.
- `codegraph.db`: files/nodes 테이블의 `engine/transcribe_npu.py`, `gui/mainwindow.cpp` 경로/심볼.

## Requirements

- completed의 text_file을 기준으로 TXT 저장과 출력 경로를 UI에 연결한다. 사용자가 저장 위치를 선택할 수 있게 한다.
- 타임스탬프/화자/한글 텍스트를 보존하고 파일 실패 시 성공 메시지를 표시하지 않는다.
- 결과 폴더 열기 버튼으로 실제 출력 디렉터리를 연다. srt_file은 기존 엔진 산출물 전달만 유지하고 추가 Export 기능은 넣지 않는다.

## Implementation Scope

- 위 Requirements와 해당 기능 연결에 필요한 최소 변경만 수행한다.
- 아래 Files의 제안 경로를 실제 저장소 구조에 맞게 적용하고 필요한 검증을 수행한다.
- 선행 티켓의 완료 조건과 계약을 확인한 후 변경한다. 기존 CLI 및 관련 정상 동작을 보존한다.

## Out of Scope

- 다른 티켓이 담당하는 기능의 재구현.
- Streaming STT, 실시간 화자 분리, 파형/재생/Timeline, 화자 이름 편집, 검색, 회의 요약, LLM, PDF/DOCX, 최근 목록, Drag & Drop.
- Python AI 엔진의 C++ 이식 및 Python embedding.

## Dependencies

- TICKET-007
- TICKET-011

## Acceptance Criteria

- [ ] 저장한 UTF-8 TXT에 화면 결과와 동일한 텍스트와 화자 구분이 있다.
- [ ] 성공한 결과의 실제 폴더가 열린다.
- [ ] 결과 없는 상태에서 저장이 비활성화되고 저장 실패는 오류로 표시된다.

## Test Requirements

### Normal Cases

- 한글/다중 화자 TXT 저장.

### Error Cases

- 권한 없음, 결과 파일 없음.

### Edge Cases

- 저장 취소, 기존 파일 덮어쓰기 확인, 공백 경로.

## Files

### Existing

- `engine/transcribe_npu.py` — DB 인덱스에서 확인; 소스 본문은 별도 확인 필요.
- `gui/mainwindow.cpp` — DB 인덱스에서 확인; 소스 본문은 별도 확인 필요.

### To Create

None — 기존 파일 또는 선행 티켓에서 생성한 관련 파일을 수정한다.

## Open Questions

- 사양서는 SRT 완료 이벤트를 예시로 제시하지만 1차 필수 저장은 TXT다. SRT UI는 범위에서 제외한다.
