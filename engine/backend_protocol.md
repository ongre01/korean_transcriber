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

| 인자 | 필수/기본값 | 계약 및 기존 엔진 매핑 |
|---|---|---|
| `--input PATH` | 필수 | 존재하는 입력 파일. 기존 CLI의 positional `input`. |
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

Bridge 자체의 인자 오류는 `error` 이벤트 후 종료 코드 2, 입력 오류는 2, 모델
경로 오류는 3, 실행 중 오류는 1을 사용한다. 사용자 취소로 정상적인 이벤트를
마칠 수 없을 때는 기존 관례대로 130을 허용한다.

공용 fixture는 `tests/fixtures/backend_protocol/`에 있으며 Python과 Qt 계약 테스트가
같은 파일을 읽는다.
