# Korean Transcriber — 개발 티켓 MM-DEV-003

**단계:** M1

### MM-DEV-003 [Bridge] 다중 입력 매니페스트 스키마 및 CLI 추가

- **우선순위:** P0
- **선행 작업:** MM-DEV-002
- **검증 시나리오:** MM-08
- **목적:** 기존 `--input`과 호환되면서 다중 입력 목록을 표준 방식으로 전달한다.

**구현 작업**

- [ ] `--multi-input-manifest <json>` 인자를 추가하고 기존 `--input`과 상호 배타 검증한다.
- [ ] 매니페스트의 `version`, `inputs[].path/label`, `reference_index`, `alignment`, `selection` 항목을 검증한다.
- [ ] `manual_offsets_ms`의 파일 인덱스 및 값 검증을 구현한다.
- [ ] 잘못된 JSON·경로·참조 인덱스·음수/비정상 창 값은 사용자용 오류로 거부한다.
- [ ] 기존 단일 입력 CLI 회귀 테스트를 유지한다.

**주요 대상 파일**

- `engine/backend_bridge.py`
- `engine/backend_protocol.md (CLI 계약)`

**완료 조건 (Acceptance Criteria)**

- [ ] 단일 파일 CLI 사용 시 동작이 바뀌지 않는다.
- [ ] 다중 매니페스트 정상·비정상 사례에서 일관된 검증 결과를 반환한다.
- [ ] 입력 경로를 shell 문자열로 합성하지 않는다.
