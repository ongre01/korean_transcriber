# TICKET-017: 1차 완료 기준 통합 검증

## Metadata

- Type: Test
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

1차 완료 기준 통합 검증을 완료하여 1차 음성 녹음·전사 앱의 해당 기능을 검증 가능한 상태로 제공한다.

## Background

프로젝트 목표는 Qt Widgets/qmake/C++17 GUI에서 마이크 녹음 또는 기존 파일을 입력하고, 기존 Python STT와 선택적 화자 분리를 QProcess로 실행하여 결과를 표시·저장하는 것이다. AI 처리와 16 kHz Mono decode는 Python에서 유지한다. 녹음 중 Streaming STT는 1차 범위에 포함하지 않는다.

첨부 DB는 파일 경로와 심볼/시그니처 인덱스이며 소스 본문이나 테스트 결과를 포함하지 않는다. 아래 Existing은 인덱스에 확인된 경로이며 구현 완료를 의미하지 않는다. 실제 저장소에서 기존 구현을 먼저 읽고, 이미 완료된 부분은 재구현하지 않는다. 신규 경로는 제안이며 기존 구조가 있으면 해당 구조를 우선한다.

## Source

- `Qt_Widgets_음성_녹음_전사_UI_개발_사양서.md`: 26, 28, 30, 31절.
- `AGENTS.md`: Development Guide, 기본 개발 환경, 구현 원칙.

## Requirements

- 사양서 30절의 15개 완료 기준을 각각 테스트 절차, 입력, 기대 결과, 실행 환경, 실제 결과와 연결한다.
- 파일 입력 및 녹음 입력, 화자 ON/OFF, UI 응답, 진행/취소, 저장, Backend 오류 흐름을 검증한다.
- 실제 마이크/AI 모델/Device가 필요한 검증과 대역 테스트를 구분하고 환경이 없는 항목은 미실행으로 기록한다.
- 기존 Python CLI 및 qmake clean build 회귀를 확인한다.

## Implementation Scope

- 위 Requirements와 해당 기능 연결에 필요한 최소 변경만 수행한다.
- 아래 Files의 제안 경로를 실제 저장소 구조에 맞게 적용하고 필요한 검증을 수행한다.
- 선행 티켓의 완료 조건과 계약을 확인한 후 변경한다. 기존 CLI 및 관련 정상 동작을 보존한다.

## Out of Scope

- 다른 티켓이 담당하는 기능의 재구현.
- Streaming STT, 실시간 화자 분리, 파형/재생/Timeline, 화자 이름 편집, 검색, 회의 요약, LLM, PDF/DOCX, 최근 목록, Drag & Drop.
- Python AI 엔진의 C++ 이식 및 Python embedding.

## Dependencies

- TICKET-012
- TICKET-013
- TICKET-014
- TICKET-015
- TICKET-016

## Acceptance Criteria

- [ ] 15개 완료 기준 전부에 검증 기록이 있다. 미실행을 통과로 기록하지 않는다.
- [ ] 지원 환경의 녹음→전사와 파일→화자 분리→TXT 저장이 통과한다.
- [ ] 실패/취소 후 재실행 및 기존 CLI 회귀 결과가 기록된다.

## Test Requirements

### Normal Cases

- 실제 짧은 WAV/MP3/M4A 및 마이크 전사.

### Error Cases

- 모델/모듈/Python 없음, 손상 파일, NPU 불가, 저장 실패.

### Edge Cases

- 한글 경로, 연속 작업, 긴 음성, 빠른 취소.

## Files

### Existing

None — 이 티켓의 전용 모듈은 첨부 인덱스에서 확인되지 않음. 구현 전에 실제 저장소를 확인한다.

### To Create

- `tests/acceptance/first_release.md` — 제안 경로.
- `tests/fixtures/README.md` — 제안 경로.

## Open Questions

- 대상 OS/Qt/실제 NPU 환경은 TICKET-001의 확정 범위를 따른다.
