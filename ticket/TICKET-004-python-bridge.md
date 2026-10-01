# TICKET-004: 기존 STT를 재사용하는 Python Bridge

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

기존 STT를 재사용하는 Python Bridge을 완료하여 1차 음성 녹음·전사 앱의 해당 기능을 검증 가능한 상태로 제공한다.

## Background

프로젝트 목표는 Qt Widgets/qmake/C++17 GUI에서 마이크 녹음 또는 기존 파일을 입력하고, 기존 Python STT와 선택적 화자 분리를 QProcess로 실행하여 결과를 표시·저장하는 것이다. AI 처리와 16 kHz Mono decode는 Python에서 유지한다. 녹음 중 Streaming STT는 1차 범위에 포함하지 않는다.

첨부 DB는 파일 경로와 심볼/시그니처 인덱스이며 소스 본문이나 테스트 결과를 포함하지 않는다. 아래 Existing은 인덱스에 확인된 경로이며 구현 완료를 의미하지 않는다. 실제 저장소에서 기존 구현을 먼저 읽고, 이미 완료된 부분은 재구현하지 않는다. 신규 경로는 제안이며 기존 구조가 있으면 해당 구조를 우선한다.

## Source

- `Qt_Widgets_음성_녹음_전사_UI_개발_사양서.md`: 2, 10, 11, 31절.
- `AGENTS.md`: Development Guide, 기본 개발 환경, 구현 원칙.
- `codegraph.db`: files/nodes 테이블의 `engine/transcribe_npu.py` 경로/심볼.

## Requirements

- decode_audio_16k_mono/transcribe_windows/save_outputs 등의 실제 구현을 먼저 읽고 최소 변경으로 기존 STT를 호출한다.
- input/device/model/output 옵션을 계약에 맞게 받아 단계/진행/segment/완료 이벤트를 flush해서 출력한다.
- 사람용 콘솔 출력은 stdout 프로토콜에 섞지 않는다. 기존 CLI 실행은 보존한다.
- 모델/모듈/디코드/파이프라인 실패를 error 이벤트와 실패 종료로 전달하고 완료는 결과 저장 성공 후에만 발행한다.

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

## Acceptance Criteria

- [ ] 음성 fixture 입력으로 TXT 결과와 유효한 JSON Lines가 생성된다.
- [ ] 기존 CLI가 기존 입력으로 계속 실행되고 출력에 회귀가 없다.
- [ ] 각 실패 경로에서 completed가 발행되지 않는다.

## Test Requirements

### Normal Cases

- 엔진 대역으로 이벤트 순서와 결과 경로 검증, 모델 환경에서 실제 짧은 음성 전사.

### Error Cases

- 없는 입력/모델, decode 예외, 저장 실패.

### Edge Cases

- 한글·공백 경로, 무음/빈 결과.

## Files

### Existing

- `engine/transcribe_npu.py` — DB 인덱스에서 확인; 소스 본문은 별도 확인 필요.

### To Create

- `engine/backend_bridge.py` — 제안 경로.
- `tests/test_backend_bridge.py` — 제안 경로.

## Open Questions

- DB는 함수 존재와 시그니처만 제공한다. 콜백 제공 여부, 반환값, AUTO/fallback 정책은 실제 소스를 확인해야 한다.
