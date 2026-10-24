# Backend Bridge 이벤트 및 인자 계약

이 문서는 Qt GUI와 `backend_bridge.py` 사이의 버전 1 계약이다. 기존
`transcribe_npu.py`의 사람용 CLI 출력 계약은 변경하지 않는다.

## 전송 및 스트림 규칙

- Bridge의 **stdout은 프로토콜 전용**이다. UTF-8(BOM 없음) JSON 객체 하나를
  한 줄에 쓰고 매 이벤트마다 flush한다. 빈 줄과 JSON 이외의 텍스트는 허용하지
  않는다.
- 로그, traceback, 모델 진단과 기존 엔진의 사람용 출력은 **stderr 전용**이다.
- 수신기는 알 수 없는 `type`, 알 수 없는 `state.value`, 필수 필드 누락 및 잘못된
  타입을 프로토콜 오류로 처리한다. 정의되지 않은 추가 필드는 이후 호환성을 위해
  무시할 수 있다.
- 성공 스트림은 `completed`를 정확히 한 번 마지막 이벤트로 내보내고 프로세스가
  코드 0으로 종료되어야 한다. 코드 0인데 `completed`가 없거나, `completed` 뒤에
  다른 이벤트가 있거나, `completed` 뒤 비정상 종료하면 실패다.
- 실패 스트림은 `error`를 정확히 한 번 마지막 이벤트로 내보내고 0이 아닌 코드로
  종료한다. `error` 뒤의 `completed` 및 다른 이벤트는 금지한다. 프로세스 시작
  실패나 강제 종료처럼 이벤트를 쓸 수 없는 실패는 Qt 프로세스 계층이 별도로
  실패 처리한다.
- 세그먼트가 0개인 성공도 허용한다. 이 경우 `segment` 없이 `completed`로 끝난다.

## 이벤트

JSON의 `number`는 유한한 값이어야 하며 초 단위 시간은 0 이상이다.

### `state`

필수 필드는 `type: string`과 `value: string`이다.

```json
{"type":"state","value":"loading_model"}
```

`value`는 `preparing`, `loading_model`, `decoding_audio`, `transcribing`,
`diarization`, `saving_result` 중 하나다. 완료 상태는 별도의 `completed` 이벤트로
표현한다.

### `progress`

필수 필드는 `type: string`과 `value: integer|null`이다. 정수는 0부터 100까지다.

```json
{"type":"progress","value":35,"processed_seconds":70.0,"total_seconds":200.0}
```

`processed_seconds`와 `total_seconds`는 선택적 `number`이며 둘을 함께 보내야 한다.
`processed_seconds <= total_seconds`여야 한다. 처리량을 아직 산정할 수 없는 모델
로딩 등의 단계는 다음처럼 `null`로 보내며 시간 필드를 보내지 않는다. Qt는 이를
indeterminate progress bar로 표시한다.

```json
{"type":"progress","value":null}
```

### `segment`

필수 필드는 `type: string`, `start: number`, `end: number`,
`speaker: integer|null`, `text: string`이다. `end >= start`이며 `text`는 비어 있지
않아야 한다.

```json
{"type":"segment","start":12.3,"end":16.8,"speaker":1,"text":"오늘 회의를 시작하겠습니다."}
```

wire의 화자 번호는 **1부터 시작하는 양의 정수**다. 기존 Python `Segment.speaker`
및 `speaker_label()`의 내부 값은 0부터 시작하는 `int | None`이므로 Bridge는
`n -> n + 1`, `None -> null`로 내보낸다. Qt의 `TranscriptSegment::speaker`는
`std::optional<int>`이며 동일하게 one-based 값 또는 `std::nullopt`를 저장한다.

### `completed`

필수 필드는 `type: string`, `text_file: string`, `srt_file: string`이고 두 경로는
비어 있지 않아야 한다. 이 이벤트는 두 파일의 저장이 성공한 뒤에만 보낸다.

```json
{"type":"completed","text_file":"C:/Results/meeting.txt","srt_file":"C:/Results/meeting.srt"}
```

Bridge는 경로를 `Path.resolve()`한 절대 경로로 직렬화한다. Qt는 프로세스 working
directory가 아닌 이 절대 경로를 그대로 해석한다. `--output-dir`이 없으면 입력
파일의 부모 폴더, 있으면 해당 폴더에 `<입력 stem><output suffix>.txt/.srt`를 만든다.

### `error`

필수 필드는 `type: string`과 비어 있지 않은 `message: string`이다. 사용자에게
보일 수 있는 간결한 메시지를 사용하며 traceback과 상세 진단은 stderr로 보낸다.

```json
{"type":"error","message":"Whisper model could not be loaded."}
```

## Bridge 인자 계약

경로 인자는 UTF-8 문자열이며 Bridge가 `expanduser().resolve()`한다. Qt는 Python
실행 파일, 스크립트 경로와 아래 인자를 각각 `QProcess::setProgram()` 및
`setArguments()`에 전달하고 shell quoting을 직접 만들지 않는다.

`--input`과 `--multi-input-manifest` 중 하나만 반드시 지정한다. 다중 입력
매니페스트는 UTF-8 JSON 파일(BOM 허용)이며, Bridge는 모델이나 오디오 처리 모듈을 불러오기
전에 파일과 스키마를 검증한다. 매니페스트 파일 안의 상대 입력 경로는 매니페스트
파일의 부모 폴더를 기준으로 해석한다.

| 인자 | 필수/기본값 | 계약 및 기존 엔진 매핑 |
|---|---|---|
| `--input PATH` | 다중 매니페스트와 택일 | 존재하는 단일 입력 파일. 기존 CLI의 positional `input`. |
| `--multi-input-manifest JSON_PATH` | 단일 입력과 택일 | 아래 버전 1 스키마를 따르는 UTF-8 JSON 파일. JSON 파일 경로와 각 입력 경로는 Bridge가 개별 경로로 처리하며 shell 명령 문자열을 만들지 않는다. |
| `--device AUTO\|NPU\|CPU\|GPU` | `AUTO` | `AUTO`는 가용 장치에서 NPU, GPU, CPU 순으로 하나를 선택한 뒤 기존 엔진에 concrete device를 전달한다. 명시 장치는 사용할 수 없으면 오류다. |
| `--diarization` | off | 기존 엔진의 `--diarize`에 대응한다. |
| `--num-speakers auto\|2\|3\|4\|5` | `auto` | `auto -> -1`, 숫자는 같은 정수로 기존 `num_speakers`에 전달한다. diarization이 꺼져 있으면 무시한다. |
| `--output-dir PATH` | 입력 부모 폴더 | 결과 폴더. |
| `--output-suffix TEXT` | 빈 문자열 | 입력 stem 뒤에 붙는다. |
| `--model-dir PATH` | 필수 | 기존 Whisper OpenVINO 모델 폴더. |
| `--model-label TEXT` | 빈 문자열 | 로그/캐시 구분용 모델 레이블. |
| `--beams INTEGER` | `1` | 1 이상. |
| `--language TEXT` | `<\|ko\|>` | Whisper language token. |
| `--window-seconds NUMBER` | `120` | 30 이상. |
| `--overlap-seconds NUMBER` | `4` | 0 이상, window의 절반 미만. |
| `--hotwords-file PATH` | 엔진 기본 파일 | 선택적 hotwords 파일. |
| `--initial-prompt-file PATH` | 엔진 기본 파일 | 선택적 initial prompt 파일. |
| `--diarization-segmentation-model PATH` | 엔진 기본 모델 | 화자 분리 segmentation 모델. |
| `--diarization-embedding-model PATH` | 엔진 기본 모델 | 화자 분리 embedding 모델. |
| `--speaker-threshold NUMBER` | `0.5` | `(0, 1]`; Auto 화자 수 clustering threshold. |
| `--diarization-device NPU\|CPU\|GPU` | `NPU` | segmentation 장치. |
| `--diarization-no-fallback` | off | 지정하면 segmentation의 CPU fallback을 금지한다. |

### 다중 입력 매니페스트 버전 1

```json
{
  "version": 1,
  "inputs": [
    {"path": "D:/meeting/mic_A.wav", "label": "테이블 중앙"},
    {"path": "D:/meeting/mic_B.m4a", "label": "발표자 근처"}
  ],
  "reference_index": 0,
  "alignment": {
    "mode": "auto",
    "manual_offsets_ms": {"1": -125}
  },
  "selection": {
    "mode": "automatic",
    "window_seconds": 3.0,
    "hop_seconds": 1.0
  }
}
```

- `version`은 JSON 정수 `1`이어야 한다. `true`나 `1.0`은 버전으로 허용하지
  않는다.
- `inputs`는 두 개 이상의 항목을 가진 배열이다. 백엔드는 입력 개수를 8개로
  제한하지 않으며 8개는 GUI의 초기 권장 상한일 뿐이다.
- 각 입력의 `path`와 `label`은 공백이 아닌 문자열이다. `path`는 존재하는 일반
  파일이어야 하고, 해석된 같은 파일을 중복 지정할 수 없다. 순서가 이후 모든
  zero-based 입력 인덱스의 기준이다.
- `reference_index`는 `inputs` 안의 항목을 가리키는 zero-based 정수다. 범위를
  벗어난 값과 `true`는 허용하지 않는다.
- `alignment.mode`는 버전 1에서 `auto`다. `manual_offsets_ms`는 입력 인덱스의
  정규 십진 문자열(`"0"`, `"1"`, ...)을 키로 하고 signed 64-bit 정수
  밀리초를 값으로 갖는 객체다. 수동 오프셋은 음수일 수 있지만 키는 반드시 현재
  입력 범위 안에 있어야 한다. 양수는 기준 파일보다 늦게 시작한 녹음이다.
  수동 오프셋이 있는 입력은 자동 추정 상태와 관계없이 그 오프셋만으로(드리프트
  보정 없이) 기준 시간축에 배치되며, 기준 입력의 수동 오프셋은 0만 허용한다.
- `selection.mode`는 버전 1에서 `automatic`이다. `window_seconds`와
  `hop_seconds`는 유한한 양수이며 `hop_seconds <= window_seconds`여야 한다.
- `selection.overrides`는 선택 필드이며 생략하면 빈 배열이다. 각 항목
  `{"start_sample": 0, "end_sample": 48000, "input_index": 1}`은 기준 시간축의
  16 kHz 분석 샘플 구간 `[start_sample, end_sample)`에서 사용자가 강제로 고른
  입력을 뜻한다. 샘플 인덱스는 0 이상 정수이고 `end_sample > start_sample`,
  `input_index`는 `inputs` 범위 안이어야 하며 구간끼리 겹칠 수 없다. 기준
  녹음 길이를 넘는 구간은 디코딩 뒤 분석 단계에서 계약 오류로 거부한다. 자동
  채널 선택 계획을 만든 뒤 이 구간에만 사용자의 선택을 적용하므로 수동 선택이
  다른 구간의 자동 선택을 바꾸지 않는다.
- `selection.overrides`를 제외하고 예시에 표시된 최상위 및 하위 필드는 모두
  필수다. 버전 1에서 정의하지 않은 추가 필드와 같은 JSON 객체 안의 중복 필드
  이름은 사용자 설정 오타나 모호한 해석을 막기 위해 거부한다.
- JSON의 `NaN`, `Infinity`, `-Infinity`, 잘못된 필드 타입, 없는 입력 파일은
  사용자용 인자 오류로 거부한다.

이 단계의 계약은 매니페스트 전달과 검증을 정의한다. 정렬, 채널 선택, 통합 WAV
생성과 Whisper 연결은 다중 입력 전처리 실행 경로에서 이 검증 결과를 소비한다.
그 실행 경로가 연결되기 전에는 정상 매니페스트도 단일 입력으로 대체 실행하지
않으며, Bridge는 `error` 이벤트와 종료 코드 1로 기능 미연결을 명시한다.

### 다중 입력 음질 분석 단발 명령

`python multimic_quality.py --multi-input-manifest JSON_PATH`는 위 매니페스트를
같은 검증기로 읽어 준비, 자동 정렬과 수동 오프셋 적용, 발화·음질 분석, 수동
override를 포함한 채널 선택까지만 실행한다. 오디오 합성이나 원본 쓰기는 하지
않는다. JSON Lines 이벤트 대신 stdout에 strict JSON 객체 하나만 쓰고, 진단과
traceback은 stderr로 보낸다. Qt는 `QProcess`로 실행하며 취소는 프로세스 종료다.

성공(종료 코드 0) 응답의 주요 필드는 다음과 같다.

| 필드 | 내용 |
|---|---|
| `version`, `ok` | `1`, `true` |
| `sources[]` | `input_index`, `path`, `label` |
| `alignment.inputs[]` | `input_index`, `source`(`reference`/`automatic`/`manual`), `status`, `offset_ms`, `time_scale`, `drift_ppm`, `confidence` — 실제 적용된 정렬 |
| `quality.inputs[]` | 입력별 전체 품질 요약(`quality_score`, `score_coverage_ratio` 등) |
| `selection` | `sample_rate`, `timeline_sample_count`, `review_row_seconds`, `switch_count`, `manual_override_count`, `segments[]`, `rows[]`, `warnings[]`, `reason_descriptions` |
| `selection.segments[]` | 기준 시간축을 빈틈없이 나눈 최종 채널 선택 구간(`channel_selection.json`의 구간과 같은 형식). `start_sample`, `end_sample`, `start`, `end`, `selected_input_index`, `automatic_input_index`, `selection_source`(`automatic`/`manual_override`), `quality_score`, `confidence`, `reason`, `reason_codes` |
| `selection.rows[]` | 검토용 행. 한 마이크가 계속 우세하면 최종 구간이 녹음 대부분을 덮을 수 있으므로, 각 최종 구간을 기준 시간축의 고정 격자(`review_row_seconds`, 5초)에서 나눈다. 구간 시작·끝에서 1초 미만인 조각은 만들지 않으며, 격자가 고정되어 재분석 뒤에도 행 경계가 유지된다. `start_sample`, `end_sample`, `start`, `end`, `segment_index`, `selected_input_index`, `automatic_input_index`, `selection_source`, 자동 선택 근거 `automatic`(`input_index`, 이 행의 `quality_score`, 자동 구간 전체의 `confidence`·`reason`·`reason_codes`, 그 자동 구간의 `segment_start_sample`·`segment_end_sample`), 입력별 근거 `inputs[]`(`input_index`, 이 행의 `quality_score`, `valid_ratio`) |

GUI의 수동 채널 선택은 행 단위로 `selection.overrides`에 담아 보낸다.

실패 응답은 `ok:false`와 `kind`, `code`, `message`, `errors[]`, `warnings[]`를
가진다. `kind`는 `preparation`(입력별 디코딩 실패, 종료 코드 1), `alignment`
(`alignment_unresolved`: 자동 정렬에 실패했고 수동 오프셋도 없는 입력 목록,
종료 코드 1), `contract`(인자·매니페스트·기준 시간축을 벗어난 override, 종료
코드 2), `dependency`(1), `cancelled`(130), `unexpected`(1) 중 하나다.

Bridge 자체의 인자 오류는 `error` 이벤트 후 종료 코드 2, 입력 오류는 2, 모델
경로 오류는 3, 실행 중 오류는 1을 사용한다. 사용자 취소로 정상적인 이벤트를
마칠 수 없을 때는 기존 관례대로 130을 허용한다.

공용 fixture는 `tests/fixtures/backend_protocol/`에 있으며 Python과 Qt 계약 테스트가
같은 파일을 읽는다.
