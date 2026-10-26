# MM-DEV-017 실제 한국어 회의 녹음 CER/WER 비교 평가

- 기준 사양: `docs/다중_마이크_녹음_통합_전사_기능_개발_사양서.md` 11절 MM-10
- 선행: MM-DEV-015(다중 모드 GUI 연동), MM-DEV-016(MM-01~MM-09 수용 검증)
- 작성 기준일: 2026-10-26

## 1. 요약

| 항목 | 상태 | 근거 |
|---|---|---|
| 평가 도구(실행·채점·보고) | 구현 완료 | `engine/run_multimic_evaluation.py`, `engine/evaluation/*`, 테스트 30건 통과 |
| 동일 Whisper/화자 분리 설정 | 보장 | 모든 조건이 같은 bridge 인자 목록을 사용하고 `run.json`에 기록 |
| 정답 전사가 있는 실제 회의 CER/WER | **미측정** | 저장소에 사용 허가·정답 전사가 확인된 회의 데이터가 없다 |
| 샘플 녹음(1시간, 2장치) 정답 불필요 지표 | 실측 | 8절 |
| `quality.py` 가중치 보정 | **변경하지 않음** | 보정 근거가 되는 정답 라벨이 없다(7절) |

현재 결론: **다중 입력 통합이 단일 파일 전사보다 정확한지는 아직 판단할 수 없다.** 판단에 필요한 것은 (1) 녹음 참여자의 평가 사용 허가 기록과 (2) 정답 전사다. 정답 전사가 준비되면 Whisper를 다시 돌리지 않고 `score` 명령으로 기존 실행 결과를 채점한다(5절).

## 2. 판정 원칙 (완료 조건 대응)

| 완료 조건 | 구현 방식 |
|---|---|
| 측정 데이터/설정/조건/결과가 재현 가능 | 실행마다 새 폴더를 만들고 덮어쓰지 않는다. `run.json`에 데이터셋 원문, 입력 SHA-256·길이·형식, 전체 bridge 명령, 코드 commit·변경 여부, 패키지 버전, 모델 파일 목록, 핫워드/초기 프롬프트 SHA-256을 기록한다. 조건별 JSON Lines 원문(`events.jsonl`)과 stderr를 보존한다. |
| 개선이 없거나 악화된 사례까지 보고 | 판정은 bootstrap 95% 구간이 0을 포함하면 `no_significant_difference`로 표시한다. 다중 입력이 더 나쁜 발화 목록(`most_degraded`)과 태그별 CER을 항상 보고서에 넣는다. 실패한 조건도 오류 메시지와 함께 보고서에 남긴다. |
| 실측하지 않은 논문 수치를 V1 개선량으로 주장하지 않음 | 보고서·문서의 모든 수치는 해당 실행의 측정값이다. arXiv:2312.09746의 40.1% 상대 감소는 oracle segmentation과 ROVER를 포함한 학습 기반 시스템 결과이며 V1 결과로 인용하지 않는다. |

## 3. 평가 데이터 준비

### 3.1 허가와 보관

- 녹음 참여자 전원의 평가 목적 사용 동의를 받은 회의만 쓴다. 데이터셋 파일의 `authorization.approved_by`(승인자)와 `authorization.basis`(동의 근거·기록 위치)를 채운다.
- `approved_by`가 비어 있으면 실행은 되지만 보고서에 `unverified`로 표시되고, 결과를 팀 밖에 공유하지 않는다.
- 녹음·정답 전사·실행 결과는 저장소 루트 `artifacts/`(`.gitignore` 대상)에 둔다. 데이터셋 정의 파일에는 경로와 설정만 둔다.

### 3.2 녹음 조건

- 같은 회의를 2개 이상 장치로 동시에 녹음한다. 장치별 위치(발화자와의 거리, 벽·천장 근접 여부)와 장치 종류를 `description`에 기록한다.
- 반향/오버랩/서로 다른 거리 진단을 하려면 회의에 다음이 포함되는 것이 좋다: 한 장치에서 먼 자리의 발화, 두 사람 이상의 동시 발화, 반향이 큰 공간 또는 구간.

### 3.3 데이터셋 파일

```json
{
  "version": 1,
  "dataset_id": "meeting_2026XXXX",
  "description": "회의실 A, mic 1: 테이블 중앙, mic 2: 발표자 근처(약 1 m)",
  "authorization": {"approved_by": "승인자", "basis": "동의서 보관 위치/문서 번호"},
  "inputs": [
    {"path": "../../../artifacts/data/meeting/mic1.wav", "label": "테이블 중앙"},
    {"path": "../../../artifacts/data/meeting/mic2.m4a", "label": "발표자 근처"}
  ],
  "reference_index": 0,
  "reference_transcript": "../../../artifacts/data/meeting/reference.json",
  "alignment": {"manual_offsets_ms": {}},
  "selection": {"window_seconds": 3.0, "hop_seconds": 1.0},
  "settings": {
    "device": "GPU",
    "model_dir": "../../models/whisper-large-v3-turbo-int8",
    "diarization": true,
    "num_speakers": "auto",
    "diarization_device": "CPU"
  }
}
```

- 상대 경로는 데이터셋 파일이 있는 폴더 기준이다.
- `settings`에 없는 항목은 Qt GUI가 보내는 기본값(`TranscribeOptions.h`)을 쓴다. 예: `skip_silence=true`, `beams=1`, 120초 창/4초 중첩. 설정은 bridge의 인자 검증기로 미리 검사한다.
- 화자 배정 오류를 측정하려면 `diarization: true`가 필요하다.
- 예시 실행 파일: `engine/evaluation/datasets/sample_20261026.json`.

### 3.4 정답 전사(reference transcript) 형식

```json
{
  "version": 1,
  "scored_regions": [{"start": 600.0, "end": 660.0}, {"start": 1800.0, "end": 1860.0}],
  "utterances": [
    {"id": "u0001", "start": 601.2, "end": 604.8, "speaker": "A", "text": "예산 안건부터 보겠습니다", "tags": []},
    {"id": "u0002", "start": 604.5, "end": 606.0, "speaker": "B", "text": "네 좋습니다", "tags": ["far_field"]}
  ]
}
```

- 시간은 **기준 녹음(`reference_index`) 시간축**의 초다. 다중 입력 결과도 이 시간축을 쓴다.
- `scored_regions`(선택): 정답을 만든 발췌 구간. 없으면 녹음 전체를 채점한다. 각 발화는 한 구간 안에 있어야 하고, 전사 세그먼트는 중간 시각이 구간 안에 있을 때 채점된다. 다른 녹음의 단일 전사는 다중 입력 실행의 정렬 결과(`alignment.json`)로 기준 시간축에 옮겨 판단한다.
- `speaker`: 실제 화자 라벨(임의 문자열). 진단 라벨과 이름이 달라도 된다(최적 대응으로 비교).
- `tags`(선택): 실패 진단용 태그. 권장 어휘: `far_field`(해당 화자와 기준 장치가 먼 발화), `near_field`, `reverb`, `noise`, `laughter`, `crosstalk`. `overlap`(다른 화자와 시간 겹침)은 자동으로 붙는다.
- 전사 규칙: 들린 대로 적고, 숫자·영문 표기는 회의 전체에서 한 방식으로 통일한다. 필러("음", "어")는 포함 여부를 정하고 일관되게 적용한다.
- 구간 선택: 보고서 7.1절이 제안하는 **무작위 표본 구간**으로 수준을 추정하고, **불일치 상위 구간**은 진단용으로만 추가한다. 불일치 상위 구간만 쓰면 조건 간 차이가 과대평가된다.
- 기계 전사를 초안으로 고치면 그 조건 쪽으로 정답이 기울 수 있다. 초안을 쓴다면 어느 조건의 초안인지 `description`에 기록하고, 가능하면 처음부터 받아 적는다.
- 검사: `engine\.venv\Scripts\python.exe engine\run_multimic_evaluation.py check-reference --reference <파일>`

## 4. 비교 조건과 지표

### 4.1 조건

| 조건 | 입력 | 의미 |
|---|---|---|
| `single_<i>` | 녹음 i 하나(`--input`) | 각 장치만 썼을 때 |
| `multi` | 전체 매니페스트(`--multi-input-manifest`) | V1 정렬·채널 선택 후 통합 WAV 전사 |
| 단일 최적(oracle) | 채점 후 CER이 가장 낮은 `single_<i>` | 사용자가 미리 고를 수 없는, 다중 입력에 가장 불리한 기준선 |
| 단일 기준 녹음 | `single_<reference_index>` | 기준 장치 하나만 썼을 때 |

조건은 같은 Python, 같은 bridge, 같은 인자 목록으로 순서대로 실행한다. 차이는 입력 인자(`--input`/`--multi-input-manifest`)와 출력 폴더뿐이다. 실행 순서는 `run.json`의 `condition_order`에 남는다(모델 캐시가 처리 시간에 영향을 줄 수 있음).

### 4.2 지표 정의

| 지표 | 정의 |
|---|---|
| CER | NFKC·소문자화 후 문자/숫자 외 기호를 제거하고 공백을 뺀 문자열의 (S+D+I)/N. 한국어 주 지표 |
| WER | 같은 정규화 후 공백으로 나눈 어절의 (S+D+I)/N. 띄어쓰기 차이도 오류가 된다 |
| 누락 발화 | 발화 글자 중 정답과 일치한 비율이 `--missed-threshold`(기본 0.5) 미만인 발화 수 |
| 화자 배정 오류(문자) | 정렬된 문자 쌍 중, 진단 화자→정답 화자 최적 1:1 대응(Hungarian) 후 화자가 다른 비율. 화자 없는 세그먼트는 오류 |
| 화자 배정 오류(발화) | 누락되지 않은 발화 중 대응된 화자의 다수결이 정답 화자와 다른 발화 수 |
| 처리 시간 / RTF | bridge 프로세스 실행 시간, 입력 길이 대비 비율. 단계별 시간은 `state` 이벤트 도착 시각 차 |
| 전환 수 | `channel_selection.json`의 `switch_count`(선택 입력이 바뀐 횟수) |
| CER 차이 95% CI | 발화를 복원 추출(2,000회, seed 20261026)해 다시 계산한 CER 차이의 2.5–97.5 백분위 |
| 발화 단위 oracle 입력 | 그 발화에서 단일 전사 오류가 가장 적은 녹음(차점과 1자 이상 차이일 때만) |
| 선택 일치율 | oracle 입력이 정해진 발화 중 V1이 그 시간에 가장 오래 선택한 입력과 같은 비율 |
| 조건 간 불일치 | 두 조건 전사 사이 문자 편집 거리. **정확도가 아니다** |

전사 전체를 시간 순서로 이어 붙여 전역 정렬하므로 타임스탬프 오차는 인식 오류로 세지 않는다. 발화별 오류는 전역 정렬을 발화 단위로 나눈 근사값이며, 삽입 오류는 바로 앞 발화에 귀속한다.

## 5. 실행 방법

저장소 루트에서 PowerShell로 실행한다. 실행 결과는 `artifacts\mm-dev-017\<dataset_id>\<run_id>\`에 생긴다.

```powershell
# 1) 모든 조건 실행 + 채점 (정답 전사가 없으면 정확도는 미측정으로 표시)
& 'engine\.venv\Scripts\python.exe' 'engine\run_multimic_evaluation.py' run `
    --dataset 'engine\evaluation\datasets\sample_20261026.json'

# 일부 조건만: --condition single_0 --condition multi
# 실행 폴더 이름 지정: --run-id 20261026_baseline

# 2) 정답 전사를 나중에 준비한 경우: Whisper 재실행 없이 다시 채점
& 'engine\.venv\Scripts\python.exe' 'engine\run_multimic_evaluation.py' score `
    --run-dir 'artifacts\mm-dev-017\sample_20261026\20261026_baseline' `
    --reference 'artifacts\data\sample_20261026\reference.json'
```

종료 코드: `0` 모든 조건 완료, `1` 하나 이상 조건 실패(보고서는 생성), `2` 데이터셋·인자 오류.

| 산출물 | 내용 |
|---|---|
| `run.json` | 데이터셋 원문, 허가 상태, 환경, 입력 지문, 공통 bridge 인자, 조건별 요약 |
| `multi_input_manifest.json` | 다중 조건에 넘긴 매니페스트 |
| `conditions/<조건>/events.jsonl` | bridge stdout 원문 |
| `conditions/<조건>/stderr.log` | 진단 로그 |
| `conditions/<조건>/condition.json` | 명령, 종료 코드, 단계 시간, 세그먼트 |
| `conditions/<조건>/output/` | TXT/SRT, 다중 조건의 `merged_16k_mono.wav`·`alignment.json`·`channel_selection.json` |
| `report.json` / `report.md` | 채점 결과, 비교, 진단, 보정, 해석 제한 |

stdout 줄이 UTF-8 JSON 객체가 아니면 그 줄은 원문만 보존하고 채점하지 않으며 조건을 실패로 표시한다.

자동 테스트:

```powershell
& 'engine\.venv\Scripts\python.exe' -m unittest discover -s 'engine\tests' -p 'test_evaluation_*.py' -v
```

## 6. 실패 구간 진단 방법

보고서 `accuracy.diagnostics`와 `report.md` 2~3절을 다음 순서로 본다.

1. **태그별 CER**: `overlap`, `far_field`, `reverb` 등 태그 집합마다 조건별 CER을 비교한다. 다중 입력이 특정 태그에서만 나쁘면 그 조건이 V1의 약점이다.
2. **악화 발화 목록**: 다중 입력이 단일 최적보다 오류가 많은 발화를 오류 차이 순으로 본다. 각 발화의 `selection`(가장 오래 선택된 입력, 입력별 선택 시간, 발화 내 전환 수, 선택 사유, 선택 경고)을 함께 확인한다.
3. **자동 태그 해석**
   - `switch_inside`: 발화 중간에 채널이 바뀌었다. 전환 경계 보정(`boundary_search_seconds`)이나 최소 유지 시간이 부족한지 본다.
   - `selection_warning`: 선택 신뢰도가 낮거나 모든 입력 품질이 낮은 구간이다.
   - `overlap`: 한 구간에 한 채널만 고르는 V1 구조상 다른 화자의 발화를 잃을 수 있다(사양서 13절).
4. **선택 일치율과 발화 단위 oracle CER**: 일치율이 낮으면 품질 점수가 Whisper가 잘 알아듣는 채널을 고르지 못한다는 뜻이다. 발화 단위 oracle CER은 "발화마다 단일 전사 중 최선을 골랐을 때"의 추정치로, 채널 선택 방식이 도달할 수 있는 수준의 참고값이다(같은 오디오 문맥이 아니므로 정확한 상한은 아니다).

## 7. V1 가중치 보정 절차

현재 `QualityScoringConfig` 기본값(`validity 0.15, speech 0.30, snr 0.55, clipping 0.65, invalid 0.35, gap 0.25, noise 0.35`)은 초기 휴리스틱이며 실측 보정된 값이 아니다(`quality.py` 문서 문자열).

보정은 정답 전사가 있을 때만 수행한다.

1. `score`가 발화별 oracle 입력을 정한다.
2. `channel_selection.json`에 저장된 창별 특징(유효 비율, 발화 존재, SNR 점수, 클리핑·무효·끊김·잡음 패널티)을 후보 가중치로 다시 점수화한다. 실행에 쓴 가중치로 저장된 점수가 재현되는지 먼저 검사한다(`formula_check_max_abs_error`).
3. 발화마다 겹치는 창의 평균 점수가 가장 높은 입력이 oracle 입력과 같은 비율(일치율)을 격자 탐색(5개 가중치 × 3개 값 = 243개 후보, `scoring.WEIGHT_GRID`)으로 비교한다.
4. 판정: 라벨 발화 30개 미만이면 `insufficient_data`, 개선 2%p 미만이면 `keep_run_weights`, 그 이상이면 `candidate_for_next_run`.
5. `candidate_for_next_run`이어도 이 값은 같은 데이터로 맞추고 평가한 proxy다. 후보 가중치를 `quality.py`에 임시 적용해 **다른 회의** 또는 같은 회의의 다른 구간으로 다시 실행하고 CER로 확인한 뒤에만 기본값을 바꾼다. 히스테리시스·경계 보정은 proxy에 반영되지 않는다.

이번 작업에서는 정답 라벨이 없어 1단계부터 수행할 수 없으므로 `quality.py`를 변경하지 않았다.

## 8. 샘플 데이터 실측 결과

<!-- SAMPLE_RESULTS -->
**상태: 실행 중** (2026-10-26 20:54 KST 시작, run id `20261026_baseline`). 완료 후 이 절을 실측값으로 교체한다.

- 데이터: `sample/recording_20261026_1.wav`(기준, 약 3,627.65초)와 `sample/recording_20261026_2.m4a`(약 3,615.91초). 같은 공간을 두 장치로 녹음한 사용자 제공 파일이며 녹음 참여자의 평가 사용 동의 기록이 없어 허가 상태는 `unverified`다.
- 조건: `single_0`, `single_1`, `multi` 순서. 공통 설정: GPU, `whisper-large-v3-turbo-int8`, beam 1, 120초 창/4초 중첩, 무음 건너뛰기, 화자 분리(CPU, 화자 수 자동).
- 정답 전사가 없으므로 CER/WER·누락 발화·화자 배정 오류는 측정하지 않는다. 처리 시간, 전환 수, 입력별 선택 비율, 정렬 결과, 조건 간 전사 불일치, 정답 전사 구간 후보만 보고한다.

## 9. V2 필요 항목

아래는 사양서와 현재 V1 코드에서 확인한 사실을 근거로 한 **제안(의견)**이다. 우선순위는 정답 전사 평가 결과로 다시 정한다.

| 항목 | 근거(사실) | 제안 |
|---|---|---|
| 동시 발화 보존 | V1은 구간마다 입력 하나만 고른다. 사양서 13절도 오버랩 누락을 한계로 명시 | 채널별 전사 후 시간축 병합(ROVER 계열) 또는 음원 분리 검토. `overlap` 태그 CER로 필요성 판단 |
| 반향·거리 지표 | `QualityScoringConfig`에 명료도(clarity) 항목이 비활성 상태 | 반향·직접음 비율 추정 지표 또는 Whisper 신뢰도(평균 log-prob, no-speech 확률)를 후보 특징으로 추가하고 `far_field`/`reverb` 태그 CER로 검증 |
| 인식 신뢰도 기반 선택 | 현재 선택은 음향 특징만 사용 | 구간별로 후보 채널을 모두 전사해 신뢰도로 고르는 방식. 비용은 입력 수 × 단일 전사 시간(8절 처리 시간 참고) |
| 학습 기반 채널 선택 | 사양서 2절: CGCS/FGCS는 학습 모델 필요 | 정답 전사가 충분히 쌓인 뒤 학습 데이터·모델·의존성 계획 수립. 논문 수치는 기대 성능으로 쓰지 않음 |
| 가중치 일반화 검증 | 보정 proxy는 같은 데이터에서 맞추고 평가함 | 회의 2개 이상으로 교차 검증 후 기본값 변경 |
| 비선형 드리프트 | 현재 비선형 드리프트는 `manual_review`로 중단 | 구간별 드리프트 보정(사양서 5.3) |
| 다채널 파일 | 파일당 모노 하나로 처리(사양서 4절) | 원본 채널별 선택 |
| 다중 채널 화자 분리 | 화자 분리는 통합 WAV 한 채널만 사용 | 장치 간 에너지 비율 등 공간 단서를 화자 분리에 활용하는지 검토 |

## 10. 해석 제한

- 한 회의의 결과는 그 녹음 환경에 대한 측정값이다. 다른 회의실·장치 배치로 일반화하려면 데이터셋을 늘린다.
- 단일 최적(oracle)은 채점 후 고른 기준선이므로 실제 사용 조건보다 단일 파일 쪽에 유리하다.
- 숫자·영문 표기 차이는 정규화하지 않아 오류로 계산된다.
- 동시 발화의 순서 차이는 전역 정렬에서 오류로 계산될 수 있다.
- 처리 시간은 장치 상태·캐시·다른 프로세스 부하에 따라 달라지는 참고값이다.
- 조건 간 전사 불일치는 정확도 지표가 아니다.
- bootstrap 구간은 발화를 독립으로 가정한다. 인접 발화의 오류는 상관이 있으므로 실제 불확실성보다 구간이 좁을 수 있다. 회의가 여러 개면 회의 단위 결과를 따로 보고한다.
