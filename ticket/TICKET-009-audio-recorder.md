# TICKET-009: 마이크 선택과 비동기 녹음

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

마이크 선택과 비동기 녹음을 완료하여 1차 음성 녹음·전사 앱의 해당 기능을 검증 가능한 상태로 제공한다.

## Background

프로젝트 목표는 Qt Widgets/qmake/C++17 GUI에서 마이크 녹음 또는 기존 파일을 입력하고, 기존 Python STT와 선택적 화자 분리를 QProcess로 실행하여 결과를 표시·저장하는 것이다. AI 처리와 16 kHz Mono decode는 Python에서 유지한다. 녹음 중 Streaming STT는 1차 범위에 포함하지 않는다.

첨부 DB는 파일 경로와 심볼/시그니처 인덱스이며 소스 본문이나 테스트 결과를 포함하지 않는다. 아래 Existing은 인덱스에 확인된 경로이며 구현 완료를 의미하지 않는다. 실제 저장소에서 기존 구현을 먼저 읽고, 이미 완료된 부분은 재구현하지 않는다. 신규 경로는 제안이며 기존 구조가 있으면 해당 구조를 우선한다.

## Source

- `Qt_Widgets_음성_녹음_전사_UI_개발_사양서.md`: 6, 7, 24, 25절.
- `AGENTS.md`: Development Guide, 기본 개발 환경, 구현 원칙.

## Requirements

- 마이크 목록 조회/선택, Qt 버전에 맞는 Audio API 추상화, 비동기 capture와 WavWriter 연결을 구현한다.
- 지원 형식을 확인하고 16 kHz를 강제하지 않는다. 변환은 기존 Python decode에서 처리한다.
- start/stop/isRecording/outputFile 및 시작/종료/오류 신호를 제공한다. 정상 WAV 마무리 후 stopped(file)를 발행한다.
- 시간과 레벨을 실제 capture 형식에 맞게 계산하며 전체 녹음을 메모리에 모으지 않는다.

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
- TICKET-008

## Acceptance Criteria

- [ ] 선택한 마이크로 녹음한 WAV에 음성이 저장된다.
- [ ] 녹음 중 elapsed time과 정규화 레벨 신호가 갱신된다.
- [ ] 마이크 없음/권한 거부/초기화 실패에서 실패 신호가 발생한다.

## Test Requirements

### Normal Cases

- 마이크별 녹음/중지 및 WAV decode.

### Error Cases

- 권한 거부, 장치 제거, 파일 실패.

### Edge Cases

- 빠른 start/stop, 무음, Int16 극값 레벨 계산.

## Files

### Existing

None — 이 티켓의 전용 모듈은 첨부 인덱스에서 확인되지 않음. 구현 전에 실제 저장소를 확인한다.

### To Create

- `gui/audio/AudioRecorder.h` — 제안 경로.
- `gui/audio/AudioRecorder.cpp` — 제안 경로.

## Open Questions

- 대상 OS 권한 처리 및 Qt 버전은 TICKET-001에서 정한다.
