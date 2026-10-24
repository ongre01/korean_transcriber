# Korean Transcriber — 개발 티켓 MM-DEV-010

**단계:** M2

### MM-DEV-010 [Backend] 선택 구간 합성 WAV 및 진단 JSON 저장

- **우선순위:** P0
- **선행 작업:** MM-DEV-005, MM-DEV-009
- **검증 시나리오:** MM-05, MM-06
- **목적:** 선택 결과를 기존 Whisper가 소비할 단일 오디오 및 추적 가능한 진단 파일로 만든다.

**구현 작업**

- [ ] 기준 시간축으로 `merged_16k_mono.wav`를 생성한다.
- [ ] 전환부는 기본적으로 평균/무조건 crossfade를 사용하지 않고, 샘플 정렬이 검증된 경우만 안전한 crossfade를 허용한다.
- [ ] `alignment.json`에 offset/drift/confidence를 저장한다.
- [ ] `channel_selection.json`에 start/end/selected_input_index/quality_score/reason/confidence를 저장한다.
- [ ] 원본 경로 등 진단정보는 개인정보 정책에 맞게 관리하고, 임시·최종 파일을 분리한다.

**주요 대상 파일**

- `engine/multimic/pipeline.py (신규)`
- `engine/multimic/select.py`

**완료 조건 (Acceptance Criteria)**

- [ ] 결과 WAV가 16 kHz mono이며 기존 전사 엔진에서 열 수 있다.
- [ ] 출력 길이·구간 타임스탬프가 기준 시간축과 일치한다.
- [ ] 선택 이력에 모든 적용 구간과 사유가 남는다.
- [ ] 원본은 변경하지 않는다.
