# Korean Transcriber — 개발 티켓 MM-DEV-012

**단계:** M3

### MM-DEV-012 [Protocol] JSON Lines 상태·결과 필드 확장 및 계약 동기화

- **우선순위:** P0
- **선행 작업:** MM-DEV-003, MM-DEV-010
- **검증 시나리오:** MM-08, MM-09
- **목적:** 기존 Qt/Python 통신 계약을 깨지 않고 다중 마이크 처리 진행상태를 전달한다.

**구현 작업**

- [ ] 상태값 `aligning_audio`, `assessing_channels`, `selecting_channels`, `rendering_merged_audio`를 정의한다.
- [ ] 완료 이벤트에서 기존 `text_file`/`srt_file`을 유지하면서 다중 모드에 `merged_audio_file`/`alignment_file`/`selection_file`을 확장한다.
- [ ] 단계별 불확정 진행률에는 `progress.value=null`을 허용한다.
- [ ] `engine/backend_protocol.py`, `engine/backend_protocol.md`, C++ `BackendEvent.h`와 프로토콜 테스트를 동시에 갱신한다.
- [ ] stdout은 오직 UTF-8 JSON Lines, stderr는 진단 전용, 단일 종료 이벤트 보장을 유지한다.

**주요 대상 파일**

- `engine/backend_protocol.py`
- `engine/backend_protocol.md`
- `engine/backend_bridge.py`
- `gui/backend/BackendEvent.h`
- `engine/tests/* (관련 계약 테스트)`

**완료 조건 (Acceptance Criteria)**

- [ ] 기존 이벤트 파서와 다중 모드 이벤트가 함께 통과한다.
- [ ] completed/error가 각 실행에서 정확히 한 번, 마지막에 발생한다.
- [ ] stdout에 비JSON 로그가 포함되지 않는다.
- [ ] 이벤트 스키마·문서·Qt 검증 코드가 일치한다.
