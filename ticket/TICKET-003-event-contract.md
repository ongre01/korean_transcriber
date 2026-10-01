# TICKET-003: 전사 데이터와 Bridge 이벤트 계약

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

전사 데이터와 Bridge 이벤트 계약을 완료하여 1차 음성 녹음·전사 앱의 해당 기능을 검증 가능한 상태로 제공한다.

## Background

프로젝트 목표는 Qt Widgets/qmake/C++17 GUI에서 마이크 녹음 또는 기존 파일을 입력하고, 기존 Python STT와 선택적 화자 분리를 QProcess로 실행하여 결과를 표시·저장하는 것이다. AI 처리와 16 kHz Mono decode는 Python에서 유지한다. 녹음 중 Streaming STT는 1차 범위에 포함하지 않는다.

첨부 DB는 파일 경로와 심볼/시그니처 인덱스이며 소스 본문이나 테스트 결과를 포함하지 않는다. 아래 Existing은 인덱스에 확인된 경로이며 구현 완료를 의미하지 않는다. 실제 저장소에서 기존 구현을 먼저 읽고, 이미 완료된 부분은 재구현하지 않는다. 신규 경로는 제안이며 기존 구조가 있으면 해당 구조를 우선한다.

## Source

- `Qt_Widgets_음성_녹음_전사_UI_개발_사양서.md`: 11, 12, 13, 15, 19절.
- `AGENTS.md`: Development Guide, 기본 개발 환경, 구현 원칙.
- `codegraph.db`: files/nodes 테이블의 `engine/transcribe_npu.py` 경로/심볼.

## Requirements

- state/progress/segment/completed/error를 UTF-8 JSON Lines로 정의하고 필수 필드, 타입, 정상 종료 조건을 명시한다.
- segment의 start/end/speaker/text 및 화자 미지정 표현을 정의한다. DB의 speaker_label 시그니처는 int|None을 허용하므로 사양서 int와의 매핑을 명시한다.
- Device=AUTO/NPU/CPU/GPU, 화자 수 Auto/2/3/4/5, 출력 경로 및 모델 옵션의 인자 계약을 정의한다.
- stdout는 프로토콜 전용, stderr는 진단 전용으로 분리하고 진행률이 불명확한 단계의 표현을 정한다.

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

## Acceptance Criteria

- [ ] 동일한 정상/오류 이벤트 fixture를 Python과 Qt가 같은 의미로 해석할 수 있다.
- [ ] Auto와 미지정 화자의 매핑 및 completed 출력 경로 해석 기준이 문서와 타입에 명시된다.

## Test Requirements

### Normal Cases

- 한글 segment와 완료 이벤트 fixture.

### Error Cases

- 필수 필드 누락/잘못된 타입을 거부.

### Edge Cases

- 화자 미지정, 빈 segment 목록, 진행률 미확정.

## Files

### Existing

- `engine/transcribe_npu.py` — DB 인덱스에서 확인; 소스 본문은 별도 확인 필요.

### To Create

- `gui/backend/TranscriptSegment.h` — 제안 경로.
- `gui/backend/TranscribeOptions.h` — 제안 경로.
- `engine/backend_protocol.md` — 제안 경로.

## Open Questions

- 진행 시간 필드는 사양서 예시에 없으므로 19절의 처리 시간 표시를 위해 계약 확장이 필요하다.
- 실제 CLI 옵션과 종료 코드는 소스 확인 후 확정한다.
