# TICKET-015: 설정 저장과 기본 숨김 고급 설정

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P1

## Goal

설정 저장과 기본 숨김 고급 설정을 완료하여 1차 음성 녹음·전사 앱의 해당 기능을 검증 가능한 상태로 제공한다.

## Background

프로젝트 목표는 Qt Widgets/qmake/C++17 GUI에서 마이크 녹음 또는 기존 파일을 입력하고, 기존 Python STT와 선택적 화자 분리를 QProcess로 실행하여 결과를 표시·저장하는 것이다. AI 처리와 16 kHz Mono decode는 Python에서 유지한다. 녹음 중 Streaming STT는 1차 범위에 포함하지 않는다.

첨부 DB는 파일 경로와 심볼/시그니처 인덱스이며 소스 본문이나 테스트 결과를 포함하지 않는다. 아래 Existing은 인덱스에 확인된 경로이며 구현 완료를 의미하지 않는다. 실제 저장소에서 기존 구현을 먼저 읽고, 이미 완료된 부분은 재구현하지 않는다. 신규 경로는 제안이며 기존 구조가 있으면 해당 구조를 우선한다.

## Source

- `Qt_Widgets_음성_녹음_전사_UI_개발_사양서.md`: 14, 24절.
- `AGENTS.md`: Development Guide, 기본 개발 환경, 구현 원칙.
- `codegraph.db`: files/nodes 테이블의 `gui/mainwindow.cpp` 경로/심볼.

## Requirements

- QSettings로 Python Path/Last Input Directory/Output Directory/Selected Microphone/Selected Device/Diarization Enable/Speaker Count/Whisper Model을 저장·복원한다.
- 고급 설정은 기본 숨김으로 두고 모델, window/overlap, hotwords/prompt, segmentation/embedding, cluster threshold/minimum duration 옵션을 기존 엔진에 매핑한다.
- 실제 엔진의 옵션 범위를 확인하여 값 검증을 수행한다. 존재하지 않는 마이크와 잘못된 경로는 사용자에게 알려 재선택할 수 있게 한다.

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
- TICKET-010
- TICKET-011
- TICKET-014

## Acceptance Criteria

- [ ] 재시작 후 명시된 설정이 복원되고 다음 작업 인자에 반영된다.
- [ ] 고급 설정은 기본 화면에서 숨겨지고 사용자가 열 수 있다.
- [ ] 잘못된 window/overlap 등의 설정으로 작업을 시작할 수 없다.

## Test Requirements

### Normal Cases

- 설정 저장/재실행/옵션 전달.

### Error Cases

- Python/모델 경로 없음, 잘못된 수치.

### Edge Cases

- 첫 실행, 사라진 마이크, 설정 키 누락.

## Files

### Existing

- `gui/mainwindow.cpp` — DB 인덱스에서 확인; 소스 본문은 별도 확인 필요.

### To Create

- `gui/settings/Settings.h` — 제안 경로.
- `gui/settings/Settings.cpp` — 제안 경로.
- `gui/settings/SettingsDialog.h` — 제안 경로.
- `gui/settings/SettingsDialog.cpp` — 제안 경로.

## Open Questions

- 화자 분리 초기 체크 여부는 사양서에 확정되지 않았다.
- 고급 설정을 1차에서 어디까지 활성화할지는 14절 문구에 따라 확인하되 기본 숨김 및 저장 구조는 제공한다.
