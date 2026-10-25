# Korean Transcriber — 개발 티켓 MM-DEV-015

**단계:** M3

### MM-DEV-015 [GUI] 다중 모드 QProcess 옵션·상태·결과 통합

- **우선순위:** P0
- **선행 작업:** MM-DEV-011, MM-DEV-013, MM-DEV-014
- **검증 시나리오:** MM-08, MM-09
- **목적:** Qt 화면에서 다중 입력 실행·진행 상태·결과 파일을 하나의 작업 흐름으로 제공한다.

**구현 작업**

- [ ] `TranscribeOptions`에 다중 매니페스트 경로/처리 설정을 추가한다.
- [ ] `BackendProcess`는 `QProcess::setProgram()/setArguments()`로 인자를 개별 전달한다.
- [ ] 다중 상태와 불확정 진행률을 올바르게 표시하고 취소 버튼 상태를 기존 앱 정책에 따라 제어한다.
- [ ] 완료 시 TXT/SRT와 merged WAV/정렬·선택 진단 파일을 결과 화면/폴더와 연계한다.
- [ ] `AudioTranscriber.pro`에 새 GUI 소스·헤더를 등록한다.

**주요 대상 파일**

- `gui/backend/TranscribeOptions.h`
- `gui/backend/BackendProcess.*`
- `gui/backend/BackendEvent.h`
- `gui/mainwindow.*`
- `gui/AudioTranscriber.pro`

**완료 조건 (Acceptance Criteria)**

- [ ] 다중 파일 선택부터 통합 전사 결과 확인까지 UI에서 완료된다.
- [ ] UI는 전처리/전사 도중 블로킹되지 않는다.
- [ ] 기존 단일 파일/실시간 녹음/설정 화면을 회귀 테스트한다.
