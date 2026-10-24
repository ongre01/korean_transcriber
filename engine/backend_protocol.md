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
- Bridge의 `EventWriter`는 각 이벤트를 쓰기 전에 `backend_protocol.py`의
  `parse_event_line()`과 `EventStreamValidator`로 검증한다. 계약을 벗어난 이벤트나
  종료 이벤트 뒤의 이벤트는 stdout에 쓰지 않고 예외가 되며, 아직 종료 이벤트가
  없으면 `error` 하나로 보고된다. 종료 이벤트를 보낸 뒤 발생한 늦은 실패는 stderr
  진단만 남기고 종료 코드를 이미 보낸 종료 이벤트에 맞춘다(`completed`면 0).
  `run_bridge()`가 종료 이벤트 없이 반환하면 `error`를 보내고 0이 아닌 코드로
  종료한다.
- 스크립트로 실행될 때 Bridge는 시작 직후 원래 stdout 파일 디스크립터를 복제해
  프로토콜 전용 스트림으로 쓰고, 디스크립터 1은 stderr로 돌린다. Python
  `print()`뿐 아니라 네이티브 라이브러리(OpenVINO, FFmpeg 등)나 자식 프로세스가
  디스크립터 1에 직접 쓰는 출력도 stdout에 섞이지 않는다.

## 이벤트

JSON의 `number`는 유한한 값이어야 하며 초 단위 시간은 0 이상이다.

### `state`

필수 필드는 `type: string`과 `value: string`이다.

```json
{"type":"state","value":"loading_model"}
```

`value`는 아래 값 중 하나다. 완료 상태는 별도의 `completed` 이벤트로 표현한다.

| `value` | 모드 | 의미 |
|---|---|---|
| `preparing` | 공통 | 인자·경로·장치 확인 |
| `decoding_audio` | 공통 | 입력 디코딩(다중 모드는 모든 입력의 분석용 디코딩) |
| `aligning_audio` | 다중 | 기준 입력 대비 오프셋·드리프트 추정과 수동 오프셋 적용 |
| `detecting_speech` | 공통 | 발화/무음 구간 감지(다중 모드는 정렬된 모든 입력의 VAD, 단일 입력과 다중 모드의 통합 WAV는 `--skip-silence`일 때만) |
| `assessing_channels` | 다중 | VAD 결과를 이용한 입력별 음질 점수 산정 |
| `selecting_channels` | 다중 | 구간별 채널 선택과 수동 override 적용 |
| `rendering_merged_audio` | 다중 | 통합 16 kHz mono WAV와 `alignment.json`/`channel_selection.json` 생성 |
| `loading_model` | 공통 | Whisper 모델 로딩 |
| `transcribing` | 공통 | Whisper 전사 |
| `diarization` | 공통 | 선택적 화자 분리 |
| `saving_result` | 공통 | TXT/SRT 저장 |

표의 순서는 권장 진행 순서이며 수신기는 상태 순서를 검증하지 않는다. 다중 전용
상태 4개는 단일 입력 실행에서 보내지 않는다. 다중 모드는 `preparing`,
`decoding_audio`, `aligning_audio`, `detecting_speech`, `assessing_channels`,
`selecting_channels`, `rendering_merged_audio` 순서로 전처리한 뒤 통합 WAV를 단일
입력처럼 `detecting_speech`(`--skip-silence`일 때만), `loading_model`,
`transcribing`, `diarization`(`--diarization`일 때만), `saving_result`로 처리한다.
따라서 다중 모드에서 `--skip-silence`를 쓰면 `detecting_speech`가 두 번 올 수 있다.

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

`value`는 실행 전체의 진행률이다. `null`은 모든 상태에서 허용한다. 다중 전처리
상태(`aligning_audio`, `assessing_channels`, `selecting_channels`,
`rendering_merged_audio`)는 단계별 소요 시간 예측이 불안정하므로 각 상태 직후
`null`을 보내는 것을 기본으로 한다. 정수를 보낼 때 시간 필드는 기준 입력
시간축의 초다. `100`은 `saving_result` 상태 뒤에만 보낼 수 있으며, 단계별 완료를
`100`으로 표현하지 않는다.

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
다중 입력 모드에서 전사되는 입력은 통합 WAV이므로 결과 이름은
`merged_16k_mono<output suffix>.txt/.srt`이고, `--output-dir`이 없으면 기준 입력의
부모 폴더에 통합 WAV·진단 파일과 함께 만든다. 세그먼트, TXT, SRT의 시간은 모두 기준
입력 시간축의 초다.

다중 입력 모드의 `completed`는 위 두 필드를 그대로 유지하고 다음 세 필드를
**모두 함께** 추가한다. 세 필드는 비어 있지 않은 절대 경로 `string`이며 `null`은
허용하지 않는다. 일부만 있으면 프로토콜 오류다. 단일 입력 모드는 세 필드를 보내지
않으며, 세 필드가 없는 `completed`는 기존 단일 모드 결과로 해석한다.

| 필드 | 파일 |
|---|---|
| `merged_audio_file` | 채널 선택 결과로 만든 `merged_16k_mono.wav` |
| `alignment_file` | 입력별 offset/drift/confidence를 담은 `alignment.json` |
| `selection_file` | 구간별 선택 입력·점수·사유를 담은 `channel_selection.json` |

```json
{"type":"completed","text_file":"C:/Results/meeting.txt","srt_file":"C:/Results/meeting.srt","merged_audio_file":"C:/Results/merged_16k_mono.wav","alignment_file":"C:/Results/alignment.json","selection_file":"C:/Results/channel_selection.json"}
```

### `warning`

필수 필드는 `type: string`과 비어 있지 않은 `message: string`이다. 실행을 계속하는
사용자용 경고이며 종료 이벤트가 아니다. 종료 이벤트 전 어느 시점에나 올 수 있다.

```json
{"type":"warning","message":"음성 구간이 감지되지 않아 Whisper 전사와 화자 분리를 건너뜁니다."}
```

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
| `--output-dir PATH` | 입력 부모 폴더(다중 모드는 기준 입력의 부모 폴더) | 결과 폴더. 다중 모드는 통합 WAV와 진단 JSON도 이 폴더에 만든다. |
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

### 다중 입력 실행 경로

Bridge는 검증된 매니페스트를 `multimic.run_multi_audio_preprocessing()`에 넘겨
디코딩 → 정렬(수동 오프셋 적용) → VAD → 음질 분석 → 채널 선택(수동 override
적용) → 통합 WAV·진단 JSON 생성 순서로 처리하고, 각 단계 시작 시 해당 `state`와
`progress.value=null`을 보낸다. 채널 선택 계획은 아래 음질 분석 단발 명령과 같은
`multimic.plan_multi_audio_selection()`으로 만들므로, 같은 매니페스트로 GUI가 검토한
계획과 전사에 쓰이는 계획이 같다. 모델 폴더·장치·hotwords/초기 프롬프트 확인은 긴
전처리 전에 `preparing`에서 끝낸다.

통합 WAV(`merged_16k_mono.wav`)는 기준 입력의 첫 샘플부터 기준 입력과 같은 길이로
만들어진다. Bridge는 staging 폴더(아래 "결과 파일 게시")에 있는 통합 WAV를 기존
엔진의 `decode_audio_16k_mono()`로 다시 읽어 단일 입력과 같은 Whisper·화자 분리·저장
단계에 넘기며, 디코딩된 길이가 기준 시간축과 다르면 오류로 중단한다. 화자 분리
ON/OFF, `--skip-silence`, hotwords와 초기 프롬프트 정책은 단일 입력과 같다. 어떤
입력도 단독으로 전사하지 않는다.

채널 선택 경고가 있거나 선택된 입력에 녹음이 없는 구간을 무음으로 채운 경우
`warning`으로 알리고 상세 내용은 `channel_selection.json`에 남긴다. 실패는 다음과
같이 사용자용 `error` 메시지와 stderr 진단으로 나뉜다. 원본 입력 파일은 읽기만
한다.

| 실패 | `error` 메시지 | 종료 코드 | stderr |
|---|---|---|---|
| 입력 디코딩·검증 실패 | `Multi-input audio could not be prepared.` 뒤에 입력 레이블별 사유 | 1 | 입력별 실패 JSON(`errors[]`)과 traceback |
| 수동 오프셋 없이 자동 정렬 실패 | `Time alignment is unresolved for <레이블> (<사유>)...` | 1 | 미해결 입력의 정렬 진단 JSON과 traceback |
| 기준 녹음 길이를 넘는 `selection.overrides` | `Invalid bridge arguments: multi-input manifest selection.overrides[i] ends after the reference timeline (...)` | 2 | traceback |
| 그 밖의 단계 오류 | 실행 중이던 단계의 접두어(예: `Channel selection failed: ...`) | 1 | traceback |

통합 WAV와 진단 JSON은 TXT/SRT와 같은 결과 세트로 게시된다. Whisper·화자 분리·저장
단계에서 실패하거나 취소되어도 세 파일은 최종 이름으로 나타나지 않는다.

## 결과 파일 게시

한 실행의 결과 파일(단일 모드: TXT/SRT, 다중 모드: `alignment.json`,
`channel_selection.json`, `merged_16k_mono.wav`, SRT, TXT)은 출력 폴더 안의 실행 전용
staging 폴더(`.transcribe_XXXXXXXX.partial` 또는 `.multimic_XXXXXXXX.partial`)에 먼저
쓴다. 마지막 파일까지 완성된 뒤 위 순서대로 최종 이름으로 옮기고 그 뒤에만
`completed`를 보낸다(`engine/result_staging.py`).

- 실패·취소된 실행은 어떤 결과 파일도 최종 이름으로 남기지 않는다. 같은 이름의 이전
  실행 결과는 내용 그대로 남는다.
- 옮기는 도중 실패하면(예: 다른 프로그램이 연 파일) 이미 옮긴 파일을 되돌리고 덮어쓴
  이전 파일을 복원한 뒤 `Result files could not be saved: ...`로 실패한다.
- staging 폴더는 실행이 끝나면(성공·실패·취소) 지운다. 프로세스가 강제 종료되어 남은
  staging 폴더는 소유 잠금 파일(`.owner.lock`)이 풀려 있으므로, 같은 출력 폴더를 쓰는
  다음 실행이 지운다. 실행 중인 다른 작업의 staging 폴더는 지우지 않는다.
- 입력 파일과 매니페스트는 읽기만 한다. 출력 이름이 입력 파일과 같으면 쓰기 전에
  거부한다.
- 결과 세트 게시 후 `completed`를 보내기 전에 취소되면 완성된 결과 세트는 남고 Qt는
  취소로 처리한다.

## 취소

Qt `BackendProcess`는 취소할 때 stdin에 `cancel` 한 줄을 쓰고 `QProcess::terminate()`를
호출한다. 유예 시간(기본 3초) 안에 끝나지 않으면 `QProcess::kill()`한다. Windows에서
`terminate()`는 콘솔 프로세스에 전달되지 않으므로(`WM_CLOSE`만 보냄) stdin 요청이
정리 기회를 주는 경로다. POSIX의 SIGTERM도 같은 취소로 처리한다.

- 스크립트로 실행된 Bridge(`run_as_script()`)는 stdin을 감시하다가 `cancel` 줄을 받으면
  주 스레드에 `KeyboardInterrupt`를 보낸다. 주 스레드는 staging을 정리하고 `error`
  (`Transcription was cancelled.`)와 종료 코드 130으로 끝난다.
- 네이티브 추론처럼 2초(`CANCEL_UNWIND_SECONDS`) 안에 Python으로 돌아오지 않으면 감시
  스레드가 staging을 직접 지우고 종료 이벤트 없이 종료 코드 130으로 끝낸다. 사용자
  취소이므로 종료 이벤트 누락이 허용된다.
- stdin 종료(EOF)나 다른 줄은 취소가 아니다. stdin을 닫은 채 실행해도 된다.
- Qt는 취소를 요청한 뒤의 stdout 이벤트를 무시하고, 프로세스가 멈춘 뒤 이벤트·종료
  코드와 관계없이 취소로 처리한다. 프로토콜 오류로 실행을 중단할 때도 같은
  cancel 요청→terminate→유예 후 kill 순서로 프로세스를 멈춘다.

## 실패 분류

Qt는 `error.message`를 그대로 보여 주지 않고 아래 접두어로 분류한 사용자 안내와
"상세 로그" 안내만 표시한다. 메시지 원문과 stderr(traceback, 진단 JSON)는 로그에만
남는다. 접두어는 이 표가 계약이며 바꿀 때 Qt `BackendFailure.h`와 함께 바꾼다.

| 분류 | `error.message` 접두어 |
|---|---|
| 동기화 실패 | `Time alignment is unresolved for`, `Multi-input time alignment failed` |
| 디코드 실패 | `Multi-input audio could not be prepared.`, `Multi-input audio decoding failed`, `Audio decoding failed` |
| 변환 실패(다중 전처리) | `Multi-input speech detection failed`, `Channel quality analysis failed`, `Channel selection failed`, `Merged audio could not be created`, `Merged audio could not be decoded`, `Merged audio does not match the reference recording timeline`, `Multi-input pre-processing failed` |
| 모델 오류 | `Model directory not found`, `Whisper model could not be loaded`, `Whisper generation configuration failed`, `Initial prompt validation failed` |
| 결과 저장 실패 | `Result files could not be saved` |

그 밖의 메시지(`Transcription failed`, 장치, 화자 분리, Python 모듈, 입력 파일 없음
등)는 기존 단일 모드의 키워드 분류를 따른다.

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

공용 fixture는 `engine/tests/fixtures/backend_protocol/`에 있으며 Python
(`engine/tests/test_backend_protocol.py`)과 Qt(`gui/tests/tst_backend_protocol.cpp`)
계약 테스트가 같은 파일을 읽는다. `success.jsonl`, `empty_segments.jsonl`,
`multi_input_success.jsonl`은 종료 코드 0, `error.jsonl`, `multi_input_error.jsonl`은
종료 코드 1로 스트림 검증을 통과해야 하며 `invalid_events.jsonl`의 각 줄은 두 파서
모두에서 거부되어야 한다. Python 계약 테스트는 이 문서의 상태 표와 Qt
`BackendEvent.h`의 상태·완료 필드 목록이 `backend_protocol.py`와 같은지도 검사한다.
