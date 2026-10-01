# Qt Widgets 기반 음성 녹음·전사 UI 개발 사양서

**문서 버전:** 0.1  
**UI Framework:** Qt Widgets  
**Build System:** qmake  
**Backend:** 기존 Python 음성 전사/화자 분리 모듈  
**주요 기능:** 실시간 음성 녹음 / 기존 음성 파일 입력 / 음성 전사 / 화자 분리 / 결과 확인 및 저장

---

# 1. 개요

## 1.1 목적

현재 Python 기반으로 구현된 음성 전사 및 화자 분리 기능을 일반 사용자가 GUI를 통해 사용할 수 있도록 Qt Widgets 기반 Desktop 애플리케이션을 개발한다.

사용자는 다음 두 가지 방법으로 음성을 입력할 수 있다.

1. PC의 마이크를 이용한 실시간 녹음
2. 기존에 녹음된 음성 파일 선택

입력된 음성은 기존 Python 음성 처리 엔진으로 전달하며, Whisper 기반 음성 전사와 선택적인 화자 분리를 수행한다.

1차 버전에서는 기존 프로젝트 구조를 최대한 유지하기 위해 다음 흐름을 사용한다.

```text
실시간 녹음
    ↓
WAV 파일 생성
    ↓
녹음 종료
    ↓
기존 Python 전사 엔진 실행
    ↓
Whisper STT
    ↓
화자 분리(Optional)
    ↓
전사 결과 표시
```

따라서 1차 버전에서의 "실시간 녹음"은 **마이크의 음성을 실시간으로 녹음하는 기능**을 의미한다.

녹음 중 동시에 Whisper 전사를 수행하는 Streaming STT 기능은 2차 개발 범위로 한다.

---

# 2. 기존 프로젝트 분석

현재 프로젝트에는 주요하게 다음 Python 모듈이 존재한다.

| 파일 | 역할 |
|---|---|
| `transcribe_npu.py` | 음성 파일 전사 메인 처리 |
| `diarization.py` | 화자 분리 |
| `download_models.py` | Whisper 및 화자 분리 모델 다운로드 |
| `run_compare.py` | 전사 실행/비교 관련 보조 실행 |

`transcribe_npu.py`에서는 다음과 같은 구조가 확인된다.

```text
음성 파일
 ↓
decode_audio_16k_mono()
 ↓
16 kHz Mono Audio
 ↓
WhisperPipeline
 ↓
transcribe_windows()
 ↓
Segment 생성
 ↓
diarize_audio()      [Optional]
 ↓
assign_speakers()
 ↓
save_outputs()
```

기존 프로젝트의 주요 특성은 다음과 같다.

- 내부 처리 Sample Rate: 16 kHz
- OpenVINO 사용
- OpenVINO GenAI `WhisperPipeline` 사용
- NPU/CPU 등의 Device 선택 구조 존재
- 긴 음성을 Window 단위로 처리
- 화자 분리 기능 존재
- 화자별 Segment 생성 가능
- 텍스트 결과 저장 기능 존재
- PyAV 기반 음성 파일 Decode 구조 존재

따라서 Qt 애플리케이션에서 AI 기능을 새로 구현하지 않고 기존 Python Backend를 재사용한다.

---

# 3. 개발 목표

1차 개발 목표는 다음과 같다.

- Qt Widgets 기반 Desktop GUI 제공
- qmake 기반 빌드
- 마이크 선택
- 실시간 음성 녹음
- 녹음 시간 표시
- 녹음 음량(Level) 표시
- 녹음 파일 저장
- 기존 음성 파일 선택
- 전사 시작/취소
- 처리 진행 상태 표시
- Whisper STT 실행
- 화자 분리 ON/OFF
- 전사 결과 화면 출력
- 화자별 전사 결과 표시
- 결과 TXT 저장
- 기존 Python Backend 재사용

---

# 4. 전체 시스템 구조

```text
┌────────────────────────────────────────────┐
│              Qt Application                │
│                                            │
│  ┌───────────────┐   ┌─────────────────┐  │
│  │ MainWindow    │   │ SettingsDialog  │  │
│  └───────┬───────┘   └─────────────────┘  │
│          │                                 │
│  ┌───────▼────────┐                        │
│  │ AudioRecorder  │                        │
│  └───────┬────────┘                        │
│          │ WAV                             │
│          ▼                                 │
│  ┌────────────────┐                        │
│  │BackendProcess  │                        │
│  │   (QProcess)   │                        │
│  └───────┬────────┘                        │
└──────────┼─────────────────────────────────┘
           │
           │ JSON / stdout
           ▼
┌────────────────────────────────────────────┐
│              Python Backend                │
│                                            │
│ backend_bridge.py                          │
│          │                                 │
│          ▼                                 │
│ transcribe_npu.py                          │
│          │                                 │
│   ┌──────┴──────┐                          │
│   ▼             ▼                          │
│ Whisper      diarization.py                │
│ OpenVINO        │                          │
│                 ▼                          │
│            Speaker Detection               │
└────────────────────────────────────────────┘
```

Qt와 Python 간 연결은 `QProcess`를 사용한다.

Python Interpreter를 Qt 프로그램 내부에 직접 Embedded 하는 방식은 1차 개발 범위에서는 사용하지 않는다.

---

# 5. UI 구성

## 5.1 Main Window

기본 화면은 다음 구조로 구성한다.

```text
┌─────────────────────────────────────────────────────────┐
│ AI Audio Transcriber                              ─ □ X │
├─────────────────────────────────────────────────────────┤
│ 입력 방식                                               │
│                                                         │
│  [ ● 실시간 녹음 ]     [ 기존 파일 열기 ]              │
│                                                         │
│  Microphone : [ Microphone Array             ▼ ]       │
│                                                         │
│                 00:03:21                                │
│                                                         │
│  Input Level  ████████████░░░░░░░░                     │
│                                                         │
│        [ ● 녹음 시작 ]       [ ■ 녹음 중지 ]           │
├─────────────────────────────────────────────────────────┤
│ 입력 파일                                               │
│ C:\Recordings\meeting_20261001_081500.wav               │
│                                         [ 파일 선택 ]    │
├─────────────────────────────────────────────────────────┤
│ 처리 옵션                                               │
│                                                         │
│ ☑ 화자 분리                                             │
│ 화자 수 : [ Auto ▼ ]                                    │
│ Device  : [ AUTO / NPU / CPU ▼ ]                       │
│                                                         │
│                [ 전사 시작 ] [ 취소 ]                   │
│                                                         │
│ 처리 중...  ███████████████░░░░░   68%                 │
├─────────────────────────────────────────────────────────┤
│ 전사 결과                                               │
│                                                         │
│ [00:00:02] Speaker 1                                    │
│ 안녕하세요. 오늘 회의를 시작하겠습니다.                 │
│                                                         │
│ [00:00:08] Speaker 2                                    │
│ 네. 먼저 개발 일정부터 이야기하겠습니다.               │
│                                                         │
│                                                         │
├─────────────────────────────────────────────────────────┤
│ [결과 저장] [결과 폴더 열기]       Ready               │
└─────────────────────────────────────────────────────────┘
```

---

# 6. 입력 방식

## 6.1 실시간 녹음

Qt Multimedia를 사용한다.

qmake 설정:

```qmake
QT += core gui widgets multimedia
```

Qt 버전에 따라 내부 Audio API를 추상화한다.

```text
Qt 6
QAudioSource

Qt 5
QAudioInput
```

애플리케이션 내부에서는 Qt 버전과 관계없이 다음 클래스로 캡슐화한다.

```cpp
class AudioRecorder : public QObject
{
    Q_OBJECT

public:
    bool startRecording();
    void stopRecording();

    bool isRecording() const;
    QString outputFile() const;

signals:
    void recordingStarted();
    void recordingStopped(QString file);
    void recordingLevelChanged(float level);
    void recordingTimeChanged(qint64 milliseconds);
    void errorOccurred(QString message);
};
```

---

# 7. 녹음 파일 형식

기본 녹음 형식은 WAV PCM으로 한다.

권장 기본 설정:

```text
Container     WAV
Codec         PCM
Channel       Mono
Sample Format Int16
```

마이크가 16 kHz 입력을 직접 지원하지 않을 수 있으므로 녹음 단계에서 반드시 16 kHz를 강제하지 않는다.

예:

```text
Microphone
 ↓
44100 Hz / 48000 Hz PCM
 ↓
WAV 저장
 ↓
transcribe_npu.py
 ↓
decode_audio_16k_mono()
 ↓
16000 Hz Mono 변환
```

기존 Python Backend에서 이미 `decode_audio_16k_mono()`가 존재하므로 Sample Rate 변환은 Backend에서 담당한다.

---

# 8. 녹음 파일 관리

녹음 파일 기본 이름:

```text
recording_YYYYMMDD_HHMMSS.wav
```

예:

```text
recording_20261001_081530.wav
```

기본 저장 위치:

```text
<프로그램 데이터 경로>/recordings/
```

예:

```text
recordings/
 ├─ recording_20261001_081530.wav
 ├─ recording_20261001_093201.wav
 └─ recording_20261002_101422.wav
```

녹음 종료 시 생성된 파일을 자동으로 현재 입력 파일로 설정한다.

```text
녹음 시작
 ↓
녹음
 ↓
녹음 중지
 ↓
WAV 생성
 ↓
CurrentInputFile 설정
 ↓
전사 시작 버튼 활성화
```

---

# 9. 기존 음성 파일 입력

`QFileDialog`를 사용한다.

```cpp
QFileDialog::getOpenFileName()
```

초기 지원 확장자는 다음과 같이 한다.

```text
*.wav
*.mp3
*.m4a
*.aac
*.flac
*.ogg
*.mp4
```

실제 Decode 가능 여부는 기존 Python Backend의 PyAV 처리 결과를 기준으로 판단한다.

파일 선택 후 다음 정보를 UI에 표시한다.

```text
파일 이름
파일 경로
재생 시간
파일 크기
```

---

# 10. Python Backend 연동

## 10.1 연동 방식

Qt의 `QProcess`를 사용한다.

```text
Qt GUI
 ↓
QProcess
 ↓
Python
 ↓
backend_bridge.py
 ↓
transcribe_npu.py
```

Qt Main Thread에서 Python 작업 완료를 기다리는 Blocking 방식은 사용하지 않는다.

`QProcess`의 Signal을 이용한다.

```cpp
connect(process,
        &QProcess::readyReadStandardOutput,
        ...);

connect(process,
        &QProcess::readyReadStandardError,
        ...);

connect(process,
        qOverload<int, QProcess::ExitStatus>(
            &QProcess::finished),
        ...);
```

---

# 11. Backend Bridge

기존 `transcribe_npu.py`의 콘솔 출력을 GUI에서 직접 Parsing하는 방식은 유지보수가 어렵기 때문에 GUI용 Bridge를 추가한다.

신규 파일:

```text
backend_bridge.py
```

역할:

```text
Qt Command
 ↓
backend_bridge.py
 ↓
기존 transcribe_npu 기능 호출
 ↓
JSON Event 출력
```

예:

```json
{"type":"state","value":"loading_model"}
```

```json
{"type":"progress","value":35}
```

```json
{
    "type":"segment",
    "start":12.3,
    "end":16.8,
    "speaker":1,
    "text":"오늘 회의를 시작하겠습니다."
}
```

완료:

```json
{
    "type":"completed",
    "text_file":"result.txt",
    "srt_file":"result.srt"
}
```

오류:

```json
{
    "type":"error",
    "message":"Whisper model could not be loaded."
}
```

JSON은 한 줄에 하나의 이벤트를 출력하는 JSON Lines 형식을 사용한다.

---

# 12. BackendProcess 클래스

Qt에서 Python Process를 관리한다.

```cpp
class BackendProcess : public QObject
{
    Q_OBJECT

public:
    void start(const TranscribeOptions &options);
    void cancel();

signals:
    void started();
    void progressChanged(int progress);
    void stateChanged(QString state);

    void segmentReceived(
        double start,
        double end,
        int speaker,
        QString text);

    void completed();
    void errorOccurred(QString message);
};
```

Backend 실행 예:

```text
python backend_bridge.py
    --input meeting.wav
    --device NPU
    --diarization
    --num-speakers auto
```

---

# 13. 전사 옵션

GUI에서 기본적으로 다음 설정을 제공한다.

## Device

```text
AUTO
NPU
CPU
GPU
```

기본값:

```text
AUTO
```

Backend에서 선택한 Device를 사용할 수 없는 경우 기존 fallback 정책을 적용한다.

---

## 화자 분리

```text
[ ] 화자 분리
```

활성화하면 `diarization.py`를 사용한다.

화자 수:

```text
Auto
2
3
4
5
```

기본값:

```text
Auto
```

---

# 14. Advanced Settings

고급 설정 화면에서 기존 프로젝트 기능을 노출할 수 있도록 한다.

```text
Whisper Model

Window Seconds
Overlap Seconds

Hotwords File
Initial Prompt File

Segmentation Model
Embedding Model

Cluster Threshold
Minimum Speaker Duration
```

1차 UI에서는 Advanced Settings를 기본적으로 숨긴다.

---

# 15. 전사 결과

전사 결과는 Segment 단위로 관리한다.

```cpp
struct TranscriptSegment
{
    double startTime;
    double endTime;

    int speaker;

    QString text;
};
```

화자 분리를 사용하지 않을 경우:

```text
[00:00:00]
안녕하세요.

[00:00:05]
오늘 회의를 시작하겠습니다.
```

화자 분리를 사용하는 경우:

```text
[00:00:00] Speaker 1
안녕하세요.

[00:00:05] Speaker 2
네. 오늘 안건부터 확인하겠습니다.
```

---

# 16. 결과 화면

결과 화면은 `QPlainTextEdit` 또는 `QTextEdit`를 사용한다.

권장:

```text
QTextEdit
```

향후 화자별 색상, 검색, Highlight 기능 확장이 가능하기 때문이다.

화자 이름 기본값:

```text
Speaker 1
Speaker 2
Speaker 3
```

향후 사용자가 이름을 변경할 수 있도록 확장 가능하다.

예:

```text
Speaker 1 → 김대리
Speaker 2 → 이과장
```

---

# 17. 애플리케이션 상태

애플리케이션은 다음 상태를 가진다.

```cpp
enum class AppState
{
    Idle,

    Recording,

    InputReady,

    Processing,

    Completed,

    Error
};
```

상태 흐름:

```text
Idle
 │
 ├─ 파일 선택 ──────────► InputReady
 │
 └─ 녹음 시작
         │
         ▼
     Recording
         │
       Stop
         ▼
     InputReady
         │
      전사 시작
         ▼
     Processing
         │
         ├─ Success ────► Completed
         │
         └─ Failure ────► Error
```

---

# 18. 버튼 활성화 정책

### Idle

```text
녹음 시작     Enabled
파일 선택     Enabled
녹음 중지     Disabled
전사 시작     Disabled
취소           Disabled
```

### Recording

```text
녹음 시작     Disabled
파일 선택     Disabled
녹음 중지     Enabled
전사 시작     Disabled
```

### InputReady

```text
녹음 시작     Enabled
파일 선택     Enabled
전사 시작     Enabled
```

### Processing

```text
녹음 시작     Disabled
파일 선택     Disabled
전사 시작     Disabled
취소           Enabled
```

---

# 19. 처리 진행 표시

기존 `transcribe_npu.py`에는 처리 Window와 Progress를 출력하는 구조가 있으므로 GUI Backend에서도 이를 활용한다.

진행 상태를 다음 단계로 구분한다.

```text
Preparing
Loading Model
Decoding Audio
Transcribing
Diarization
Saving Result
Completed
```

UI:

```text
Transcribing...

████████████████░░░░░░
67%

02:14 / 03:20
```

---

# 20. 작업 취소

사용자가 전사 중 `취소` 버튼을 누르면:

```cpp
QProcess::terminate()
```

우선 정상 종료를 시도한다.

일정 시간 내 종료되지 않을 경우:

```cpp
QProcess::kill()
```

취소 후 상태:

```text
Processing
   ↓
Idle 또는 InputReady
```

입력 음성 파일은 삭제하지 않는다.

---

# 21. 오류 처리

다음 오류를 UI에서 처리한다.

### 녹음 오류

```text
마이크 없음
마이크 접근 권한 없음
Audio Device 초기화 실패
파일 생성 실패
```

### 파일 오류

```text
파일 없음
지원하지 않는 파일
Decode 실패
손상된 Audio
```

### AI Backend 오류

```text
Python 실행 실패
Python Module 없음
Model 없음
OpenVINO 초기화 실패
NPU 없음
Whisper Pipeline 생성 실패
Diarization Model 없음
```

사용자에게 Python Traceback 전체를 바로 표시하지 않는다.

예:

```text
음성 인식 모델을 불러올 수 없습니다.

상세 로그는 로그 보기에서 확인할 수 있습니다.
```

---

# 22. 로그

로그 파일:

```text
logs/
 └─ app_20261001.log
```

로그 항목:

```text
Application Start
Selected Audio Device
Recording Start
Recording Stop
Input File
Python Command
Backend State
Model Loading
Transcription Start
Diarization Start
Output File
Backend Error
```
---

# 23. qmake 프로젝트 설정

기본 `.pro` 파일:

```qmake
QT += core gui widgets multimedia

CONFIG += c++17

TEMPLATE = app
TARGET = AudioTranscriber

SOURCES += \
    src/main.cpp \
    src/MainWindow.cpp \
    src/audio/AudioRecorder.cpp \
    src/audio/WavWriter.cpp \
    src/backend/BackendProcess.cpp \
    src/settings/Settings.cpp

HEADERS += \
    src/MainWindow.h \
    src/audio/AudioRecorder.h \
    src/audio/WavWriter.h \
    src/backend/BackendProcess.h \
    src/backend/TranscriptSegment.h \
    src/backend/TranscribeOptions.h \
    src/settings/Settings.h

FORMS += \
    forms/MainWindow.ui \
    forms/SettingsDialog.ui

RESOURCES += \
    resources/resources.qrc
```

---

# 24. 주요 클래스

```text
MainWindow
 │
 ├─ AudioRecorder
 │
 ├─ BackendProcess
 │
 └─ Settings
```

## MainWindow

담당:

```text
UI Event
Application State
입력 파일 관리
전사 결과 표시
```

UI와 음성/AI 처리 로직은 직접 구현하지 않는다.

---

## AudioRecorder

담당:

```text
마이크 장치 Enumeration
마이크 선택
Audio Capture
WAV 생성
Recording Duration
Input Level 계산
```

---

## BackendProcess

담당:

```text
Python 실행
Argument 생성
stdout 수신
JSON Parsing
Progress 처리
Segment 전달
Process 취소
Process 오류 처리
```

---

## Settings

`QSettings`를 사용한다.

저장 항목:

```text
Python Path
Last Input Directory
Output Directory
Selected Microphone
Selected Device
Diarization Enable
Speaker Count
Whisper Model
```

---

# 25. 스레드 정책

UI Main Thread에서는 다음 작업을 수행하지 않는다.

```text
Audio AI Processing
Whisper 실행
Diarization
대용량 음성 Decode
```

Python AI 처리는 별도 Process에서 실행되므로 Qt UI는 Blocking 되지 않는다.

Audio Capture는 Qt Multimedia의 비동기 이벤트 구조를 이용한다.

따라서 초기 버전에서는 별도의 복잡한 `QThread` 구조를 만들지 않는다.

---

# 26. 1차 개발 범위

## 필수

- Qt Widgets UI
- qmake
- 마이크 Device 선택
- 실시간 녹음
- 녹음 시간 표시
- 녹음 Level 표시
- WAV 저장
- 기존 녹음 파일 선택
- Python Backend 실행
- STT
- 화자 분리 ON/OFF
- 진행 상태 표시
- 처리 취소
- 전사 결과 표시
- TXT 결과 저장
- 오류 처리

---

# 27. 2차 개발 후보

다음 기능은 1차 개발 이후 추가한다.

```text
녹음 중 Streaming STT

실시간 Whisper Segment 출력

실시간 Speaker Diarization

Audio Waveform 표시

녹음 파일 재생

Timeline 기반 Transcript

Speaker 이름 변경

Transcript 직접 편집

검색

회의 요약

회의 제목 자동 생성

핵심 내용 추출

Action Item 추출

Markdown 회의록 생성

PDF / DOCX Export

최근 작업 목록

Drag & Drop 파일 입력
```

특히 향후 회의록 프로그램으로 확장할 경우 구조는 다음과 같이 확장한다.

```text
Recording
    ↓
STT
    ↓
Speaker Diarization
    ↓
Transcript
    ↓
LLM
 ┌──┼─────────┐
 ▼  ▼         ▼
요약 결정사항 Action Items
    │
    ▼
Meeting Minutes
```

---

# 28. 구현 우선순위

### Phase 1 — Backend GUI 연결

```text
Qt MainWindow
파일 선택
QProcess
Python 전사 실행
결과 표시
```

이 단계에서 먼저 기존 `transcribe_npu.py`와 Qt의 연결을 검증한다.

### Phase 2 — 녹음

```text
AudioRecorder
Microphone
WAV 저장
녹음 시간
Level Meter
```

### Phase 3 — 화자 분리

```text
Diarization Option
Speaker Segment
Speaker UI
```

### Phase 4 — 안정화

```text
Progress
Cancel
Error Handling
Settings
Logging
```

---

# 30. 완료 기준

다음 항목을 모두 만족하면 1차 버전 개발 완료로 정의한다.

1. 애플리케이션 실행 후 마이크 목록을 확인할 수 있다.
2. 마이크를 선택하고 녹음을 시작할 수 있다.
3. 녹음 중 시간이 증가한다.
4. 마이크 입력 Level을 확인할 수 있다.
5. 녹음을 종료하면 WAV 파일이 생성된다.
6. 생성된 WAV 파일을 즉시 전사할 수 있다.
7. 기존 WAV/MP3/M4A 등의 녹음 파일을 선택할 수 있다.
8. 전사 실행 중 UI가 멈추지 않는다.
9. 처리 진행 상태를 확인할 수 있다.
10. 전사 작업을 취소할 수 있다.
11. 전사 결과를 화면에서 확인할 수 있다.
12. 화자 분리를 활성화할 수 있다.
13. 화자별 전사 문장을 구분할 수 있다.
14. 결과를 파일로 저장할 수 있다.
15. NPU 사용 불가 등 Backend 오류가 발생해도 GUI가 비정상 종료되지 않는다.

---

# 31. 핵심 설계 원칙

이번 UI 개발에서는 **기존 Python AI 코드를 Qt/C++로 옮기지 않는다.**

구조를 다음과 같이 명확하게 분리한다.

```text
Qt/C++
 └─ UI
 └─ Recording
 └─ Application State
 └─ Backend Process Control

Python
 └─ Audio Decode
 └─ Whisper
 └─ OpenVINO
 └─ NPU
 └─ Speaker Diarization
 └─ Transcript 생성
```

Qt와 Python 사이에는 `backend_bridge.py`를 두고 JSON Lines 프로토콜을 사용한다.

이 구조를 사용하면 기존 `transcribe_npu.py`와 `diarization.py`의 변경을 최소화하면서 GUI를 추가할 수 있으며, 향후 Streaming STT나 AI 회의록 기능을 추가할 때도 UI와 음성 인식 엔진을 독립적으로 확장할 수 있다.