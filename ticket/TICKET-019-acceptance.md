# TICKET-019: 1차 완료 기준 통합 검증

## Metadata

- Type: Validation
- Size: Medium
- Status: TODO
- Priority: P0

## Goal

1차 완료 기준 통합 검증을 완료하여 독립 검증 가능한 결과를 제공한다.

## Background

PROJECT_GOAL.md의 FR-019을 수행하며 사양서 1차 기능 흐름을 구성한다.

## Source

- `Qt Widgets 기반 음성 녹음·전사 UI 개발 사양서.md` — 27, 29–30절
- `PROJECT_GOAL.md` — Functional Requirements > FR-019

## Requirements

- 사양서 30절의 15개 완료 기준에 실제 결과와 테스트 환경을 기록한다
- 파일 전사·녹음 전사·화자 ON/OFF·취소 후 재시도·Backend 실패 후 복구를 검증한다
- 확정한 OS/Qt 환경의 qmake 빌드·실행 절차와 Python/모델 준비 방법을 기록한다
- 사용 가능한 CPU/NPU 등 실제 장치 결과와 미검증 항목을 구분한다

## Implementation Scope

- 아래 Files의 제안 대상을 실제 저장소 구조에 맞게 생성/연결한다.
- 요구 항목에 필요한 최소 supporting change만 수행하고 기존 Python 엔진 변경 근거를 기록한다.

## Out of Scope

- Streaming STT, 회의 요약, AI 엔진 C++ 이식, 사양서 28절 후속 기능.
- 다른 티켓의 독립 기능을 함께 구현하지 않는다.

## Dependencies

- TICKET-014
- TICKET-018

## Acceptance Criteria

- [ ] 사양서 30절의 15개 완료 기준에 실제 결과와 테스트 환경을 기록한다
- [ ] 파일 전사·녹음 전사·화자 ON/OFF·취소 후 재시도·Backend 실패 후 복구를 검증한다
- [ ] 확정한 OS/Qt 환경의 qmake 빌드·실행 절차와 Python/모델 준비 방법을 기록한다
- [ ] 사용 가능한 CPU/NPU 등 실제 장치 결과와 미검증 항목을 구분한다
- [ ] 관련 검증 결과와 미검증 환경을 기록한다.

## Test Requirements

### Normal Cases

- 정상 짧은 음성·긴 음성·손상 파일·마이크 없음·모델 없음·취소 E2E

### Error Cases

- 처리 중 UI 입력/창 이동 응답 확인

### Edge Cases

- 빈 입력, 반복 실행 및 해당 기능의 경계 조건을 검증하고 상태/자원 잔류가 없는지 확인한다.

## Files

### Existing

- 실제 소스 저장소는 이번 입력에 포함되지 않았다. 사양서의 기존 Python 파일 설명은 TICKET-001에서 실제 확인한다.

### To Create

- `docs/ACCEPTANCE_RESULTS.md` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)
- `docs/BUILD_RUN_GUIDE.md` (신규 또는 후속 수정 제안; 실제 저장소 확인 후 결정)

## Open Questions

- GPU/NPU 미보유 환경의 성공 실행을 검증한 것으로 보고하지 않는다. 자동 모델 다운로드·설치 패키지는 새 필수 기능으로 추가하지 않는다.
