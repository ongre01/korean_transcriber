# Korean Transcriber — 개발 티켓 MM-DEV-014

**단계:** M3

### MM-DEV-014 [Backend/GUI] 중단·오류·임시 파일 정리 보장

- **우선순위:** P0
- **선행 작업:** MM-DEV-012, MM-DEV-013
- **검증 시나리오:** MM-07, MM-09
- **목적:** 전처리 또는 전사 중 취소·실패해도 입력 파일과 UI 상태를 보존한다.

**구현 작업**

- [ ] 기존 `BackendProcess`의 terminate→유예 후 kill 정책을 다중 처리에도 적용한다.
- [ ] 중단/오류 시 불완전 임시 생성물을 정리하고 확정된 원본 파일은 남긴다.
- [ ] 동기화 실패, 디코드 실패, 변환 실패, 모델 오류를 구분한다.
- [ ] 종료 이벤트·프로세스 종료 상태의 불일치를 기존 프로토콜 규칙대로 오류 처리한다.

**주요 대상 파일**

- `gui/backend/BackendProcess.*`
- `engine/backend_bridge.py`
- `engine/multimic/pipeline.py`

**완료 조건 (Acceptance Criteria)**

- [ ] 정렬·합성·전사 각 단계에서 취소 후 GUI가 사용 가능한 상태로 돌아간다.
- [ ] 원본 파일이 삭제/수정되지 않고 미완성 결과물이 최종 결과로 노출되지 않는다.
- [ ] 사용자 오류 메시지와 상세 stderr 로그가 분리된다.
