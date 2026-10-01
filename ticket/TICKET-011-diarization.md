# TICKET-011: 화자 분리 옵션과 결과 연동

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

화자 분리 옵션과 결과 연동을 완료하여 1차 음성 녹음·전사 앱의 해당 기능을 검증 가능한 상태로 제공한다.

## Background

프로젝트 목표는 Qt Widgets/qmake/C++17 GUI에서 마이크 녹음 또는 기존 파일을 입력하고, 기존 Python STT와 선택적 화자 분리를 QProcess로 실행하여 결과를 표시·저장하는 것이다. AI 처리와 16 kHz Mono decode는 Python에서 유지한다. 녹음 중 Streaming STT는 1차 범위에 포함하지 않는다.

첨부 DB는 파일 경로와 심볼/시그니처 인덱스이며 소스 본문이나 테스트 결과를 포함하지 않는다. 아래 Existing은 인덱스에 확인된 경로이며 구현 완료를 의미하지 않는다. 실제 저장소에서 기존 구현을 먼저 읽고, 이미 완료된 부분은 재구현하지 않는다. 신규 경로는 제안이며 기존 구조가 있으면 해당 구조를 우선한다.

## Source

- `Qt_Widgets_음성_녹음_전사_UI_개발_사양서.md`: 13, 15, 16, 28절.
- `AGENTS.md`: Development Guide, 기본 개발 환경, 구현 원칙.
- `codegraph.db`: files/nodes 테이블의 `engine/diarization.py`, `engine/transcribe_npu.py`, `gui/mainwindow.cpp` 경로/심볼.

## Requirements

- 화자 분리 ON/OFF와 화자 수 Auto/2/3/4/5를 제공한다. OFF에서는 화자 수를 비활성화한다.
- 기존 diarize_audio/assign_speakers를 Bridge에서 재사용하고 Auto를 실제 엔진 값에 매핑한다.
- 분리 후 확정된 화자 정보를 segment에 전달한다. 기존 출력 이후 재전송이 필요하면 결과 중복을 막는 계약을 먼저 명시한다.
- Speaker 1/2/...를 표시하고 OFF/미지정 화자의 표시 규칙을 적용한다. Device fallback은 기존 엔진 정책을 유지한다.

## Implementation Scope

- 위 Requirements와 해당 기능 연결에 필요한 최소 변경만 수행한다.
- 아래 Files의 제안 경로를 실제 저장소 구조에 맞게 적용하고 필요한 검증을 수행한다.
- 선행 티켓의 완료 조건과 계약을 확인한 후 변경한다. 기존 CLI 및 관련 정상 동작을 보존한다.

## Out of Scope

- 다른 티켓이 담당하는 기능의 재구현.
- Streaming STT, 실시간 화자 분리, 파형/재생/Timeline, 화자 이름 편집, 검색, 회의 요약, LLM, PDF/DOCX, 최근 목록, Drag & Drop.
- Python AI 엔진의 C++ 이식 및 Python embedding.

## Dependencies

- TICKET-004
- TICKET-007

## Acceptance Criteria

- [ ] OFF 실행은 diarization을 호출하지 않고 화자 라벨을 표시하지 않는다.
- [ ] ON 실행은 화자별 문장을 구분하며 결과가 중복되지 않는다.
- [ ] 모델이 없을 때 오류와 재시도 가능한 상태를 제공한다.

## Test Requirements

### Normal Cases

- Auto 및 고정 2인 fixture.

### Error Cases

- segmentation/embedding 모델 없음.

### Edge Cases

- 단일 화자, 미지정 화자, 빈 segment, 선택 Device 사용 불가.

## Files

### Existing

- `engine/diarization.py` — DB 인덱스에서 확인; 소스 본문은 별도 확인 필요.
- `engine/transcribe_npu.py` — DB 인덱스에서 확인; 소스 본문은 별도 확인 필요.
- `gui/mainwindow.cpp` — DB 인덱스에서 확인; 소스 본문은 별도 확인 필요.

### To Create

None — 기존 파일 또는 선행 티켓에서 생성한 관련 파일을 수정한다.

## Open Questions

- DB 시그니처의 num_speakers=-1은 Auto 매핑 후보이며 실제 소스를 확인 후 확정한다.
