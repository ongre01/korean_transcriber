# MM-DEV-016 다중 마이크 수용 검증

- 기준 사양: `docs/다중_마이크_녹음_통합_전사_기능_개발_사양서.md` 11절
- 대상 시나리오: MM-01 ~ MM-09
- 자동 검증 기준일: 2026-10-26
- 기준 실행 환경: Windows 11 `10.0.26300`, CPython `3.12.10` 64-bit, NumPy `2.5.4`

## 1. 범위와 판정 원칙

이 문서는 MM-01~MM-09의 수용 기준을 현재 자동 테스트와 연결하고, 같은 결과를 다시 얻기 위한 명령과 수동 GUI 확인 절차를 정의한다.

자동 테스트의 합성 신호와 임시 WAV는 고정 seed를 사용한다. 테스트는 정렬, 음질 점수, 채널 선택, 기준 시간축, 파일 게시 및 JSON Lines 계약을 검증한다. 다만 다음 한계를 구분해야 한다.

- 30분 드리프트 fixture는 메모리와 실행 시간을 줄이기 위해 만든 **400 Hz 분석 신호**다. 장치 클록의 affine 보정 수학과 진단 계약을 검증하지만, 30분짜리 16 kHz WAV의 codec·리샘플 음질을 측정하는 시험은 아니다.
- Bridge 통합 테스트의 `MergedAudioEngine`은 실제 OpenVINO Whisper 모델 대신 결정적인 fake transcriber를 사용한다. 통합 WAV 전달, 상태 순서, 타임스탬프, TXT/SRT 및 JSON 계약은 검증하지만 실제 한국어 인식 정확도, NPU 성능 또는 CER/WER은 검증하지 않는다.
- 실제 모델 품질 비교는 MM-10 범위이며 MM-DEV-016의 자동 성공 조건에 포함하지 않는다.
- Qt 테스트에서 `QSKIP`가 발생하면 성공으로 간주하지 않고 해당 환경의 검증 미완료로 기록한다.

## 2. 결정적 fixture와 현재 측정값

### MM-01 ±0.5초 초기 오프셋

`engine/tests/test_multimic_align.py`의 `cropped_recordings()`는 다음 조건으로 입력을 만든다.

- 분석 샘플레이트: 16,000 Hz
- master 길이: 16초, `speech_like_master(seed=1234)`
- reference: master의 3초 지점부터 9초
- positive 입력: reference보다 0.5초 뒤 구간, gain `0.65`, Gaussian noise seed `100`, 표준편차 `0.003`
- negative 입력: reference보다 0.5초 앞 구간, polarity/gain `-0.80`, Gaussian noise seed `101`, 표준편차 `0.003`
- 수용 기준: 각각의 목표 오프셋과 추정값 차이 20 ms 이하

현재 기준 환경의 측정값은 다음과 같다.

| 항목 | 목표 | 측정 | 오차/보조 지표 |
|---|---:|---:|---:|
| positive placement | +500 ms | +500.0 ms (`+8000` samples) | 0.0 ms |
| negative placement | -500 ms | -500.0 ms (`-8000` samples) | 0.0 ms |
| positive confidence | > 0.5 | 0.9717411709 | 통과 |
| negative confidence | > 0.5 | 0.9805663208 | 통과 |
| positive 정렬 후 상관계수 | > 0.95 | 0.9993176357 | 통과 |

### MM-02 30분 선형 드리프트

`engine/tests/test_multimic_align.py`의 `long_drift_recordings()` 기본 조건은 다음과 같다.

- 분석 샘플레이트: 400 Hz
- 길이: 1,805초(30분 5초)
- random seed: `20261023`
- 목표 초기 오프셋: +250 ms
- 목표 장치 클록 차이: +200 ppm, 목표 time scale `1.0002`
- target 생성: 기준 신호 위치를 affine mapping한 뒤 `numpy.interp`로 보간
- 수용 기준: 초기 오프셋 오차 20 ms 이하, 보정 후 RMS 오차 20 ms 이하, 최대 잔차는 `AlignmentConfig.drift_residual_tolerance_ms` 이하

현재 기준 환경의 측정값은 다음과 같다.

| 항목 | 목표/한계 | 측정 |
|---|---:|---:|
| offset | 250 ms ± 20 ms | 250.0 ms, 오차 0.0 ms |
| estimated time scale | `1.0002` ± 30 ppm | `1.0001998878650011`, 오차 0.112135 ppm |
| drift | 200 ppm ± 30 ppm | 199.887865 ppm |
| drift span | 누적 오차 관찰 | 359.776169 ms |
| 보정 전 RMS 오차 | 진단값 | 121.596605 ms |
| 보정 후 RMS 오차 | ≤ 20 ms | 0.796888 ms |
| 최대 보정 후 오차 | ≤ 20 ms | 1.340031 ms |
| drift anchor 수 | ≥ 3 | 8 |
| 기준 시간축 overlap | 진단값 | 0.999860111 |
| 정렬 후 상관계수 | > 0.98 | 0.9985899503 |

비선형 누적 드리프트 0.80초 fixture는 자동 보정을 성공으로 표시하지 않아야 한다. `test_nonlinear_drift_requires_manual_review_and_is_not_warped`는 `status=manual_review`, `failure_code=unstable_drift`, `warning_code=manual_review_required`와 warp 거부를 검증한다.

## 3. MM-01~MM-09 추적표

| ID | 결정적 데이터와 핵심 수용 assertion | 자동 검증 위치 | 구현 경로 | 판정 범위 |
|---|---|---|---|---|
| MM-01 | 동일 합성 발화에 ±0.5초 placement, gain/noise/polarity 차이. 양쪽 오차 ≤20 ms, confidence >0.5, 정렬 후 상관 >0.95 | `test_multimic_align.py::InitialOffsetTests::test_estimates_both_half_second_placement_offsets` | `multimic/align.py::estimate_initial_offsets`, `multimic/pipeline.py::place_signal_on_timeline` | 자동 |
| MM-02 | 1,805초·400 Hz·+250 ms·+200 ppm. affine 보정, 잔차·anchor·기준 시간축을 검증하고 비선형 드리프트는 수동 검토로 전환 | `test_multimic_align.py::DriftAlignmentTests::test_thirty_minute_linear_drift_is_corrected_and_diagnosed`, `test_nonlinear_drift_requires_manual_review_and_is_not_warped` | `multimic/align.py::estimate_time_alignments`, `multimic/pipeline.py::warp_signal_to_reference_timeline` | 자동; 실제 16 kHz 장시간 파일 품질 제외 |
| MM-03 | A의 2~3초 구간에만 broadband noise. B의 SNR/점수가 더 높고 sample 2500의 선택 채널은 B. 작은 교대 점수에서는 switch 0회와 `hysteresis_hold` | `test_multimic_quality.py::test_local_broadband_noise_lowers_the_affected_channel_score`, `test_multimic_select.py::test_real_quality_pipeline_avoids_local_broadband_noise`, `test_hysteresis_rejects_small_alternating_score_changes` | `multimic/quality.py::analyze_aligned_quality`, `multimic/select.py::select_channels` | 자동 |
| MM-04 | B의 2~3초 구간을 hard clipping. clipped sample/ratio/penalty가 양수이고 `clipping_suspected`가 기록되며 B 점수가 낮아지고 해당 구간에서 clean A가 선택됨. 큰 RMS만으로 선택하지 않음 | `test_multimic_quality.py::test_hard_clipping_has_an_explicit_penalty_and_lowers_score`, `test_multimic_select.py::test_local_clipping_selects_the_clean_channel`, `test_multimic_quality.py::test_louder_but_clipped_noisy_input_does_not_win_by_rms` | `multimic/quality.py::_score_window_input`, `multimic/select.py::select_channels` | 자동 |
| MM-05 | A에만 있는 발화가 union candidate에 포함되고 minimum hold를 우회해 A가 선택되며 merged samples가 A와 동일하고 길이가 유지됨 | `test_multimic_quality.py::test_single_input_speech_survives_union`, `test_multimic_select.py::test_real_vad_pipeline_preserves_single_input_speech`, `test_multimic_render.py::test_speech_heard_by_one_microphone_survives_rendering` | `multimic/quality.py::analyze_aligned_speech`, `multimic/select.py::select_channels`, `multimic/pipeline.py::render_merged_audio` | 자동, 실제 Whisper 의미 정확도 제외 |
| MM-06 | 늦게 시작하거나 일찍 끝난 입력의 padding을 녹음된 무음과 구분. unavailable 구간에는 점수를 주지 않고 기준 파일 길이의 16 kHz mono WAV와 연속 segment JSON 생성 | `test_multimic_pipeline.py::test_timeline_padding_is_never_marked_as_recorded_silence`, `test_multimic_quality.py::test_prepared_quality_uses_the_reference_timeline`, `test_multimic_render.py::test_offset_recordings_render_reference_timeline_and_keep_sources` | `multimic/pipeline.py::warp_prepared_inputs`, `write_multi_audio_outputs` | 자동 |
| MM-07 | 다른 seed의 무관한 세 번째 녹음은 offset 없이 낮은 confidence의 파일 실패. Bridge는 모델 load·결과 생성 전에 차단하고 세 원본 fingerprint를 보존하며, 수동 offset 또는 명시적 제외 뒤에만 진행 | `test_multimic_align.py::test_unrelated_recording_is_a_file_level_failure`, `test_backend_bridge_multi_pipeline.py::test_unrelated_third_recording_blocks_merge_and_keeps_sources`, `test_unresolved_alignment_is_a_user_error_with_stderr_detail`, `gui/tests/tst_multiaudioinputmodel.cpp::warningAndFailureRequireManualCorrectionOrExclusion` | `multimic/align.py`, `backend_bridge.py::_run_multi_input`, `MultiAudioInputModel::isAlignmentResolved` | 엔진·모델 자동, 전체 MainWindow 흐름은 절차 확인 |
| MM-08 | `--input` 단일 모드는 기존 상태 순서, TXT/SRT-only completed 계약, 기본 인자와 Qt parser를 유지하고 다중 결과 필드를 노출하지 않음 | `test_backend_bridge_multi_pipeline.py::SingleInputRegressionTests`, `test_backend_bridge_protocol.py::test_single_input_run_keeps_existing_completed_contract`, `test_backend_protocol.py::test_success_fixture`, `gui/tests/tst_backend_protocol.cpp::successFixture`, `gui/tests/tst_backendprocess.cpp::optionsBuildMutuallyExclusiveInputArguments` | `backend_bridge.py::_run_single_input`, `backend_protocol.py`, `TranscribeOptions.h`, `BackendEvent.h` | 계약 자동, 실제 모델/마이크는 절차 확인 |
| MM-09 | 정렬·render·전사·diarization·저장 취소는 exit 130/error terminal이며 completed가 아님. 원본 fingerprint와 이전 결과 보존, partial 제거/다음 실행 정리, Qt 이벤트 루프 응답성 및 terminate→kill 검증 | `test_backend_bridge_cancellation.py::MultiInputCancellationTests`, `BridgeProcessCancellationTests`, `gui/tests/tst_backendprocess.cpp::{cancelRequestLetsTheBackendCleanUp,unresponsiveBackendIsKilledAfterGracePeriod,lateCompletedAfterCancelIsIgnored}`, `gui/tests/tst_multiaudioinputdialog.cpp::{cancelRestoresPriorResultsAndKeepsEventLoopResponsive,qualityAnalysisCancelKeepsPlanAndEventLoopResponsive}` | `result_staging.py::ResultStaging`, `backend_bridge.py::CancellationWatcher`, `BackendProcess::cancel`, `MainWindow::processingCancelled` | Backend/대화상자 자동, MainWindow 최종 상태는 절차 확인 |

## 4. Python 자동 검증 명령

모든 명령은 저장소 루트에서 PowerShell로 실행한다. 시스템 `python`이 아니라 `engine/.venv`를 사용한다. 기준 환경의 시스템 Python에는 NumPy가 없어 합성 테스트 import가 실패했으므로, 다른 interpreter로 바꾸려면 먼저 동일한 의존성을 준비하고 보고서에 경로와 버전을 남긴다.

### 표준 aggregate 실행

```powershell
New-Item -ItemType Directory -Force -Path 'artifacts\mm-dev-016' | Out-Null
& 'engine\.venv\Scripts\python.exe' 'engine\run_multimic_acceptance.py' `
    --report 'artifacts\mm-dev-016\acceptance.json'
```

명령 형식은 다음과 같다.

```text
engine\.venv\Scripts\python.exe engine\run_multimic_acceptance.py --report <path>
```

aggregate runner는 MM-01~MM-09별 결과, 실행 환경, fixture 조건, MM-01/MM-02 측정값 및 하위 테스트 ID·상태·실패 상세를 보고서에 남기는 표준 진입점이다. exit code `0`, 최상위 `status: "passed"`, 모든 시나리오의 `status: "passed"`가 함께 충족되어야 자동 검증 성공이다. exit code `1`은 하나 이상의 시나리오 실패, exit code `2`는 runner 구성 또는 실행 오류다. 2026-10-26 기준 표준 aggregate 실행은 26 tests 모두 통과했고 보고서 `status`는 `passed`였다.

### 기존 세부 suite 직접 실행

다중 마이크 알고리즘 전체:

```powershell
& 'engine\.venv\Scripts\python.exe' -m unittest discover `
    -s 'engine\tests' -p 'test_multimic_*.py' -v 2>&1 |
    Tee-Object -FilePath 'artifacts\mm-dev-016\engine-multimic.log'
```

2026-10-26 기준 위 suite는 `122 tests`, `OK`, 4.953초였다. 시간은 성능 수용 기준이 아니며 환경과 verbosity에 따라 달라질 수 있다.

Bridge/프로토콜/취소 회귀:

```powershell
& 'engine\.venv\Scripts\python.exe' -m unittest discover `
    -s 'engine\tests' -p 'test_backend*.py' -v 2>&1 |
    Tee-Object -FilePath 'artifacts\mm-dev-016\engine-backend.log'

& 'engine\.venv\Scripts\python.exe' -m unittest `
    'engine.tests.test_result_staging' -v 2>&1 |
    Tee-Object -FilePath 'artifacts\mm-dev-016\engine-staging.log'
```

MM-01/MM-02만 빠르게 다시 실행하려면 테스트 디렉터리에서 모듈 이름으로 실행한다.

```powershell
Push-Location 'engine\tests'
try {
    & '..\.venv\Scripts\python.exe' -m unittest -v `
        'test_multimic_align.InitialOffsetTests.test_estimates_both_half_second_placement_offsets' `
        'test_multimic_align.DriftAlignmentTests.test_thirty_minute_linear_drift_is_corrected_and_diagnosed' `
        'test_multimic_align.DriftAlignmentTests.test_nonlinear_drift_requires_manual_review_and_is_not_warped'
} finally {
    Pop-Location
}
```

## 5. Qt qmake 테스트

Qt의 MSVC 명령 프롬프트처럼 `qmake`, `nmake` 및 Qt runtime이 PATH에 있는 환경에서 실행한다. 아래 명령은 각 `.pro`를 독립 shadow build하고 QtTest text 로그를 보존한다.

```powershell
$repo = (Resolve-Path '.').Path
$buildRoot = Join-Path $repo 'build\mm-dev-016-qt'
$logRoot = Join-Path $repo 'artifacts\mm-dev-016\qt'
New-Item -ItemType Directory -Force -Path $buildRoot, $logRoot | Out-Null

$qtTests = @(
    'backend_protocol_tests',
    'backend_process_tests',
    'engine_paths_tests',
    'multi_audio_input_model_tests',
    'multi_audio_selection_model_tests',
    'multi_audio_input_dialog_tests'
)

foreach ($name in $qtTests) {
    $build = Join-Path $buildRoot $name
    New-Item -ItemType Directory -Force -Path $build | Out-Null
    Push-Location $build
    try {
        & qmake (Join-Path $repo "gui\tests\$name.pro")
        if ($LASTEXITCODE -ne 0) { throw "qmake failed: $name" }
        & nmake
        if ($LASTEXITCODE -ne 0) { throw "nmake failed: $name" }
        $exe = Join-Path $build "release\$name.exe"
        & $exe -o "$(Join-Path $logRoot "$name.txt"),txt"
        if ($LASTEXITCODE -ne 0) { throw "QtTest failed: $name" }
    } finally {
        Pop-Location
    }
}
```

QtTest의 `PASS`는 자동 검증 성공, `FAIL!`/비정상 종료는 실패다. `SKIP`은 Python 실행 파일 또는 GUI runtime 등 검증 환경이 부족한 상태이므로 보고서에 `incomplete`로 남기고, 필수 의존성을 준비한 후 다시 실행한다.

## 6. MainWindow 절차 검증

현재 자동 Qt 테스트는 `BackendProcess`, 다중 입력 model/dialog 및 프로토콜을 검증하지만 `MainWindow::processingCompleted`, `processingFailed`, `processingCancelled`의 최종 화면 상태를 직접 구동하지 않는다. 다음 절차 결과를 `artifacts/mm-dev-016/mainwindow-check.md`에 실행 환경, 입력 파일 SHA-256, 시각, 관찰 결과와 함께 기록한다.

### 단일 입력 회귀

1. 2초 이상의 정상 WAV를 단일 파일로 선택한다.
2. 상태가 `InputReady`이고 전사 시작이 활성화되는지 확인한다.
3. 전사를 시작해 `Processing`과 취소 버튼 활성화를 확인한 뒤 즉시 취소한다.
4. 상태가 `InputReady`로 돌아오고 같은 입력 경로가 유지되며 `Completed` 또는 새 최종 결과 파일이 표시되지 않는지 확인한다.
5. 같은 파일로 다시 실행한다. 실제 모델이 준비된 환경에서는 TXT/SRT가 모두 존재할 때만 `Completed`인지 확인한다.
6. 빈 모델 디렉터리처럼 재현 가능한 모델 load 실패를 유도한 실행에서는 `Error`가 표시되고 `Completed`가 표시되지 않으며, 상세 Traceback은 사용자 대화상자가 아니라 로그에만 있는지 확인한다.

### 다중 입력·비정상 입력

1. 서로 관련된 두 녹음과 무관한 세 번째 녹음을 추가해 시간 정렬 분석을 실행한다.
2. 무관한 파일에 낮은 신뢰도/실패 이유가 표시되고, 수동 offset 입력 또는 명시적 제외 전에는 적용·전사가 차단되는지 확인한다.
3. 해당 파일을 제외한 뒤 정렬 해결 상태와 음질 분석 가능 상태를 확인한다.
4. 정렬 또는 음질 분석 중 창 이동, 표 선택 등 UI 이벤트가 처리되는지 확인하고 취소한다. 취소 뒤 기존 입력과 직전의 확정 분석 결과가 유지되는지 확인한다.
5. 다중 전사를 시작한 뒤 취소한다. `InputReady` 복귀, 원본 SHA-256 불변, 새 completed 표시 없음, 최종 이름의 불완전 WAV/JSON/TXT/SRT 없음과 `.partial` 정리를 확인한다.

### 녹음·설정 최소 회귀

1. 사용 가능한 마이크에서 짧게 녹음하고 중지해 WAV가 생성되고 `InputReady`가 되는지 확인한다.
2. 해당 녹음으로 전사를 시작했다가 취소해 녹음 WAV가 삭제되지 않는지 확인한다.
3. Python/model/output 설정을 저장하고 창을 다시 연 뒤 값과 단일 입력 기본 모드가 유지되는지 확인한다.

실제 마이크나 Whisper/OpenVINO 모델이 없는 환경에서는 해당 항목을 `미실행`으로 기록한다. 이를 fake-engine 자동 테스트 통과로 대체하거나 실제 인식 품질이 검증됐다고 표현하지 않는다.

## 7. 결과와 로그 해석

- **Pass:** aggregate process exit code가 `0`이고 보고서의 최상위 및 MM-01~MM-09 상태가 모두 `passed`이며, Qt 필수 테스트에 FAIL/SKIP이 없고 MainWindow 절차 항목이 통과했다.
- **Fail:** unittest `FAILED`/`ERROR`, QtTest `FAIL!`, aggregate non-zero exit, 측정 한계 초과, 정상 완료가 아닌 실행에서 completed 표시, 원본 변경, 불완전 최종 파일 노출 중 하나라도 발생했다.
- **Incomplete:** Qt/Python 의존성 부족으로 import 실패 또는 SKIP, 실제 마이크/모델이 필요한 절차 미실행, 보고서나 로그 누락. Incomplete는 Pass가 아니다.
- MM-02 선형 fixture에서 `manual_review_required`가 나오면 실패다. 반대로 비선형 fixture에서 자동 성공을 반환해도 실패다.
- MM-07의 무관 입력과 MM-09의 취소는 의도된 비정상 경로다. 마지막 이벤트가 `error`/cancel이고 completed가 없으며 파일·상태 보존 assertion이 통과해야 시나리오 자체는 Pass다.
- stdout은 UTF-8 JSON Lines 전용이다. 사람이 읽는 진단과 Traceback은 stderr/log에만 있어야 한다. 사용자 오류 메시지에 Traceback이나 민감한 원본 경로가 노출되면 실패다.
- 실행 시간은 참고값이다. 오프셋, drift, 잔차, 출력 길이, 파일 fingerprint, 이벤트 순서 및 종료 상태가 수용 판정값이다.

보존할 최소 산출물은 aggregate JSON 보고서, Python suite 로그, 각 QtTest 로그, MainWindow 절차 기록이다. 실패 시에는 같은 fixture seed와 명령을 유지하고 stdout/stderr를 분리해 첨부한다.
