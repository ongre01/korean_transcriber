# TICKET-008: PCM WAV 저장 모듈

## Metadata

- Type: Feature
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

PCM WAV 저장 모듈을 완료하여 1차 음성 녹음·전사 앱의 해당 기능을 검증 가능한 상태로 제공한다.

## Background

프로젝트 목표는 Qt Widgets/qmake/C++17 GUI에서 마이크 녹음 또는 기존 파일을 입력하고, 기존 Python STT와 선택적 화자 분리를 QProcess로 실행하여 결과를 표시·저장하는 것이다. AI 처리와 16 kHz Mono decode는 Python에서 유지한다. 녹음 중 Streaming STT는 1차 범위에 포함하지 않는다.

첨부 DB는 파일 경로와 심볼/시그니처 인덱스이며 소스 본문이나 테스트 결과를 포함하지 않는다. 아래 Existing은 인덱스에 확인된 경로이며 구현 완료를 의미하지 않는다. 실제 저장소에서 기존 구현을 먼저 읽고, 이미 완료된 부분은 재구현하지 않는다. 신규 경로는 제안이며 기존 구조가 있으면 해당 구조를 우선한다.

## Source

- `Qt_Widgets_음성_녹음_전사_UI_개발_사양서.md`: 7, 8절.
- `AGENTS.md`: Development Guide, 기본 개발 환경, 구현 원칙.

## Requirements

- 실제 PCM 형식에 맞는 WAV 헤더를 작성하고 녹음 종료 시 RIFF/data 길이를 갱신한다.
- Mono Int16을 우선하되 장치가 지원하는 형식을 사용할 때 헤더와 바이트 스트림이 일치하도록 한다.
- 파일 생성/쓰기/마무리 오류를 호출자에 반환하고 실패 파일을 성공 파일로 보고하지 않는다.

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

- [ ] 샘플 PCM을 저장한 WAV의 채널/샘플레이트/비트수/데이터 길이가 실제 데이터와 일치한다.
- [ ] Python decode_audio_16k_mono 경로에서 저장 WAV를 decode할 수 있다.

## Test Requirements

### Normal Cases

- 44.1/48 kHz PCM 저장 후 재열기.

### Error Cases

- 쓰기 권한 없음, 쓰기 중 실패.

### Edge Cases

- 데이터 없음, RIFF 크기 한계에 대한 명시적 거부 또는 정책.

## Files

### Existing

None — 이 티켓의 전용 모듈은 첨부 인덱스에서 확인되지 않음. 구현 전에 실제 저장소를 확인한다.

### To Create

- `gui/audio/WavWriter.h` — 제안 경로.
- `gui/audio/WavWriter.cpp` — 제안 경로.
- `tests/gui/test_wav_writer.cpp` — 제안 경로.

## Open Questions

- 지원 PCM 형식과 RIFF 한계 정책은 장치 및 제품 요구에 맞춰 명시한다.
