# Korean Transcriber — 개발 티켓 MM-DEV-006

**단계:** M1

### MM-DEV-006 [GUI] 시간 정렬 분석·수동 오프셋·제외 처리

- **우선순위:** P0
- **선행 작업:** MM-DEV-001, MM-DEV-004, MM-DEV-005
- **검증 시나리오:** MM-07, MM-09
- **목적:** 사용자가 자동 정렬 결과를 검토하고 실패한 입력을 안전하게 수정할 수 있게 한다.

**구현 작업**

- [ ] `시간 정렬 분석` 비동기 실행 버튼을 추가한다.
- [ ] 마이크별 offset, drift, confidence, 성공/경고/실패 상태를 표시한다.
- [ ] 낮은 신뢰도의 파일은 수동 오프셋 수정 또는 제외를 제공한다.
- [ ] 유효한 공통 발화가 없거나 정렬 실패인 파일은 명시적 조치 전 자동 통합을 차단한다.

**주요 대상 파일**

- `gui/input/MultiAudioInputDialog.*`
- `gui/input/MultiAudioInputModel.*`
- `gui/mainwindow.*`

**완료 조건 (Acceptance Criteria)**

- [ ] 실패한 파일을 사용자가 알아볼 수 있고 조정/제외할 수 있다.
- [ ] 수동 보정 결과가 이후 분석 단계에 반영된다.
- [ ] 분석 중 Qt UI 조작 및 취소가 가능하다.
