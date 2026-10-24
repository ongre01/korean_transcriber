# Korean Transcriber — 개발 티켓 MM-DEV-013

**단계:** M3

### MM-DEV-013 [Backend] 다중 전처리 파이프라인과 Whisper/화자 분리 연결

- **우선순위:** P0
- **선행 작업:** MM-DEV-003, MM-DEV-005, MM-DEV-010, MM-DEV-012
- **검증 시나리오:** MM-08, MM-10
- **목적:** 통합 WAV를 기존 전사·화자 분리 엔진으로 전달하고 기존 결과 형식을 유지한다.

**구현 작업**

- [ ] 매니페스트 입력에서 정렬→VAD→품질 분석→채널 선택→통합 WAV→Whisper 순서로 오케스트레이션한다.
- [ ] 기존 `transcribe_npu.py`와 선택적 `diarization.py` 기능을 재사용한다.
- [ ] 다중 입력의 기준 시간축을 TXT/SRT/segment에 일관되게 적용한다.
- [ ] 전처리 실패 시 사용자용 오류와 stderr 진단을 구분해 반환한다.
- [ ] 단일 파일과 다중 파일 실행 경로를 분리하되 결과 계약은 공통으로 둔다.

**주요 대상 파일**

- `engine/multimic/pipeline.py`
- `engine/backend_bridge.py`
- `engine/transcribe_npu.py (재사용)`
- `engine/diarization.py (재사용)`

**완료 조건 (Acceptance Criteria)**

- [ ] 정상 다중 입력에서 TXT/SRT가 생성된다.
- [ ] 화자 분리 ON/OFF가 기존 정책에 따라 동작한다.
- [ ] 기존 단일 파일 모드도 기존 결과 및 종료 계약을 유지한다.
