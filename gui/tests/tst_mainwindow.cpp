#include "app/AppState.h"
#include "mainwindow.h"

#include <QCheckBox>
#include <QCloseEvent>
#include <QComboBox>
#include <QCoreApplication>
#include <QPushButton>
#include <QLabel>
#include <QFile>
#include <QProgressBar>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QTemporaryFile>
#include <QTest>
#include <QTextEdit>

class MainWindowTest : public QObject
{
    Q_OBJECT

private slots:
    void initTestCase();
    void buttonPolicy_data();
    void buttonPolicy();
    void normalTransitionAndDuplicateClickGuard();
    void errorAllowsRetry();
    void recordingClickGuard();
    void recordingIndicatorsStopUpdatingAfterRecording();
    void completedRecordingBecomesTranscribableInput();
    void failedRecordingKeepsPreviousInput();
    void selectedFileMetadataIsDisplayed();
    void cancelledSelectionKeepsCurrentInput();
    void decodeFailureKeepsPreviousInput();
    void newerSelectionWins();
    void newRunReplacesPreviousTranscript();
    void emptyResultDoesNotReusePreviousTranscript();
    void longTranscriptTextIsPreserved();
    void progressIndicatorsFollowBackendEvents();
    void failedProcessingDoesNotShowCompletionProgress();
    void diarizationOptionsAndTranscriptLabels();
    void diarizationModelFailureAllowsRetry();
    void cancellationWaitsForExitAndAllowsRestart();
    void closeDefersUntilBackendIsStopped();
};

namespace {
QString pythonProgram()
{
    const QString configured = QString::fromLocal8Bit(qgetenv("PYTHON")).trimmed();
    return configured.isEmpty() ? QStringLiteral("python") : configured;
}

QString createAudioFixture(const QTemporaryDir &directory, const QString &name)
{
    const QString path = directory.filePath(name);
    QFile file(path);
    if (!file.open(QIODevice::WriteOnly) || file.write("fixture") < 0) {
        return QString();
    }
    file.close();
    return path;
}

void configureMetadataMock(MainWindow &window)
{
    window.audioFileInfo()->setPythonProgram(pythonProgram());
    window.audioFileInfo()->setProbeScript(
        QFINDTESTDATA("fixtures/mock_audio_metadata.py"));
}

void configureBackendMock(MainWindow &window)
{
    const QString script = QFINDTESTDATA("fixtures/mock_backend_process.py");
    QVERIFY2(!script.isEmpty(), "The mock backend script was not found");
    window.backendProcess()->setPythonProgram(pythonProgram());
    window.backendProcess()->setBridgeScript(script);
    window.backendProcess()->setWorkingDirectory(QFileInfo(script).absolutePath());
}
} // namespace

void MainWindowTest::initTestCase()
{
    qRegisterMetaType<AppState>();
}

void MainWindowTest::buttonPolicy_data()
{
    QTest::addColumn<AppState>("state");
    QTest::addColumn<bool>("hasInput");
    QTest::addColumn<bool>("recordEnabled");
    QTest::addColumn<bool>("fileEnabled");
    QTest::addColumn<bool>("stopEnabled");
    QTest::addColumn<bool>("transcribeEnabled");
    QTest::addColumn<bool>("cancelEnabled");

    QTest::newRow("idle")
        << AppState::Idle << false << true << true << false << false << false;
    QTest::newRow("idle-with-input")
        << AppState::Idle << true << true << true << false << false << false;
    QTest::newRow("recording")
        << AppState::Recording << false << false << false << true << false << false;
    QTest::newRow("input-ready")
        << AppState::InputReady << true << true << true << false << true << false;
    QTest::newRow("input-ready-without-input")
        << AppState::InputReady << false << true << true << false << false << false;
    QTest::newRow("processing")
        << AppState::Processing << true << false << false << false << false << true;
    QTest::newRow("completed")
        << AppState::Completed << true << true << true << false << true << false;
    QTest::newRow("completed-without-input")
        << AppState::Completed << false << true << true << false << false << false;
    QTest::newRow("error")
        << AppState::Error << true << true << true << false << true << false;
    QTest::newRow("error-without-input")
        << AppState::Error << false << true << true << false << false << false;
}

void MainWindowTest::buttonPolicy()
{
    QFETCH(AppState, state);
    QFETCH(bool, hasInput);
    QFETCH(bool, recordEnabled);
    QFETCH(bool, fileEnabled);
    QFETCH(bool, stopEnabled);
    QFETCH(bool, transcribeEnabled);
    QFETCH(bool, cancelEnabled);

    QTemporaryFile inputFile;
    QVERIFY(inputFile.open());

    MainWindow window;
    if (hasInput) {
        window.setCurrentInputFile(inputFile.fileName());
    }
    window.setAppState(state);

    const auto *recordButton = window.findChild<QPushButton *>("recordStartButton");
    const auto *fileButton = window.findChild<QPushButton *>("fileSelectButton");
    const auto *stopButton = window.findChild<QPushButton *>("recordStopButton");
    const auto *transcribeButton = window.findChild<QPushButton *>("transcribeButton");
    const auto *cancelButton = window.findChild<QPushButton *>("cancelButton");

    QVERIFY(recordButton);
    QVERIFY(fileButton);
    QVERIFY(stopButton);
    QVERIFY(transcribeButton);
    QVERIFY(cancelButton);
    QCOMPARE(recordButton->isEnabled(), recordEnabled);
    QCOMPARE(fileButton->isEnabled(), fileEnabled);
    QCOMPARE(stopButton->isEnabled(), stopEnabled);
    QCOMPARE(transcribeButton->isEnabled(), transcribeEnabled);
    QCOMPARE(cancelButton->isEnabled(), cancelEnabled);
}

void MainWindowTest::normalTransitionAndDuplicateClickGuard()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString inputPath = createAudioFixture(directory, QStringLiteral("ui_success.wav"));
    QVERIFY(!inputPath.isEmpty());

    MainWindow window;
    configureBackendMock(window);
    QCOMPARE(window.appState(), AppState::Idle);

    window.setCurrentInputFile(inputPath);
    QCOMPARE(window.appState(), AppState::InputReady);

    auto *transcribeButton = window.findChild<QPushButton *>("transcribeButton");
    auto *resultTextEdit = window.findChild<QTextEdit *>("resultTextEdit");
    QVERIFY(transcribeButton);
    QVERIFY(resultTextEdit);
    QVERIFY(resultTextEdit->isReadOnly());
    QSignalSpy startSpy(&window, &MainWindow::transcriptionStartRequested);

    transcribeButton->click();
    QCOMPARE(window.appState(), AppState::Processing);
    QCOMPARE(startSpy.count(), 1);

    transcribeButton->click();
    QCOMPARE(window.appState(), AppState::Processing);
    QCOMPARE(startSpy.count(), 1);

    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::Completed, 5000);
    QVERIFY(transcribeButton->isEnabled());
    QCOMPARE(resultTextEdit->toPlainText(),
             QStringLiteral("[00:00:02]\n첫 번째 & 원문\n\n"
                            "[00:00:12]\n<b>두 번째</b>"));
}

void MainWindowTest::errorAllowsRetry()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString failurePath = createAudioFixture(
        directory, QStringLiteral("ui_failure.wav"));
    const QString successPath = createAudioFixture(
        directory, QStringLiteral("ui_success.wav"));
    QVERIFY(!failurePath.isEmpty());
    QVERIFY(!successPath.isEmpty());

    MainWindow window;
    configureBackendMock(window);
    window.setCurrentInputFile(failurePath);

    auto *transcribeButton = window.findChild<QPushButton *>("transcribeButton");
    QVERIFY(transcribeButton);
    QSignalSpy startSpy(&window, &MainWindow::transcriptionStartRequested);

    transcribeButton->click();
    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::Error, 5000);
    QVERIFY(transcribeButton->isEnabled());

    window.setCurrentInputFile(successPath);
    transcribeButton->click();
    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::Completed, 5000);
    QCOMPARE(startSpy.count(), 2);
}

void MainWindowTest::recordingClickGuard()
{
    MainWindow window;
    auto *recordButton = window.findChild<QPushButton *>("recordStartButton");
    auto *stopButton = window.findChild<QPushButton *>("recordStopButton");
    QVERIFY(recordButton);
    QVERIFY(stopButton);

    QSignalSpy startSpy(&window, &MainWindow::recordingStartRequested);
    recordButton->click();
    QCOMPARE(window.appState(), AppState::Recording);
    QCOMPARE(startSpy.count(), 1);

    recordButton->click();
    QCOMPARE(startSpy.count(), 1);

    stopButton->click();
    QCOMPARE(window.appState(), AppState::Idle);
}

void MainWindowTest::recordingIndicatorsStopUpdatingAfterRecording()
{
    MainWindow window;
    auto *timeLabel = window.findChild<QLabel *>("recordingTimeLabel");
    auto *levelBar = window.findChild<QProgressBar *>("inputLevelProgressBar");
    QVERIFY(timeLabel);
    QVERIFY(levelBar);

    window.setAppState(AppState::Recording);
    QVERIFY(QMetaObject::invokeMethod(
        &window, "recordingTimeChanged", Q_ARG(qint64, qint64(3723000))));
    QVERIFY(QMetaObject::invokeMethod(
        &window, "recordingLevelChanged", Q_ARG(float, 0.42f)));
    QCOMPARE(timeLabel->text(), QStringLiteral("01:02:03"));
    QCOMPARE(levelBar->value(), 42);

    window.setAppState(AppState::Idle);
    QVERIFY(QMetaObject::invokeMethod(
        &window, "recordingTimeChanged", Q_ARG(qint64, qint64(9999000))));
    QVERIFY(QMetaObject::invokeMethod(
        &window, "recordingLevelChanged", Q_ARG(float, 0.99f)));
    QCOMPARE(timeLabel->text(), QStringLiteral("01:02:03"));
    QCOMPARE(levelBar->value(), 42);
}

void MainWindowTest::completedRecordingBecomesTranscribableInput()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString recordedPath = createAudioFixture(
        directory, QStringLiteral("ui_success.wav"));
    QVERIFY(!recordedPath.isEmpty());

    MainWindow window;
    configureBackendMock(window);
    auto *transcribeButton = window.findChild<QPushButton *>("transcribeButton");
    auto *nameLabel = window.findChild<QLabel *>("fileNameValueLabel");
    auto *durationLabel = window.findChild<QLabel *>("fileDurationValueLabel");
    auto *levelBar = window.findChild<QProgressBar *>("inputLevelProgressBar");
    QVERIFY(transcribeButton);
    QVERIFY(nameLabel);
    QVERIFY(durationLabel);
    QVERIFY(levelBar);

    window.setAppState(AppState::Recording);
    QVERIFY(QMetaObject::invokeMethod(
        &window, "recordingTimeChanged", Q_ARG(qint64, qint64(3210))));
    QVERIFY(QMetaObject::invokeMethod(
        &window, "recordingLevelChanged", Q_ARG(float, 0.75f)));
    QVERIFY(QMetaObject::invokeMethod(
        &window, "recordingLevelChanged", Q_ARG(float, 0.0f)));
    QVERIFY(QMetaObject::invokeMethod(
        &window, "recordingFinished", Q_ARG(QString, recordedPath)));

    QCOMPARE(window.appState(), AppState::InputReady);
    QCOMPARE(QFileInfo(window.currentInputFile()).canonicalFilePath(),
             QFileInfo(recordedPath).canonicalFilePath());
    QCOMPARE(nameLabel->text(), QStringLiteral("ui_success.wav"));
    QCOMPARE(durationLabel->text(), QStringLiteral("00:00:03"));
    QCOMPARE(levelBar->value(), 0);
    QVERIFY(transcribeButton->isEnabled());

    transcribeButton->click();
    QCOMPARE(window.appState(), AppState::Processing);
    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::Completed, 5000);
}

void MainWindowTest::failedRecordingKeepsPreviousInput()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString previousPath = createAudioFixture(
        directory, QStringLiteral("previous.wav"));
    const QString stalePath = createAudioFixture(
        directory, QStringLiteral("stale.wav"));
    const QString failedPath = directory.filePath(QStringLiteral("failed.wav"));
    QVERIFY(!previousPath.isEmpty());
    QVERIFY(!stalePath.isEmpty());

    MainWindow window;
    window.setCurrentInputFile(previousPath);
    window.setAppState(AppState::Recording);
    QVERIFY(QMetaObject::invokeMethod(
        &window, "recordingFinished", Q_ARG(QString, failedPath)));

    QCOMPARE(window.appState(), AppState::Error);
    QCOMPARE(QFileInfo(window.currentInputFile()).canonicalFilePath(),
             QFileInfo(previousPath).canonicalFilePath());
    QVERIFY(!QFileInfo::exists(failedPath));

    window.setAppState(AppState::InputReady);
    QVERIFY(QMetaObject::invokeMethod(
        &window, "recordingFinished", Q_ARG(QString, stalePath)));
    QCOMPARE(window.currentInputFile(), QFileInfo(previousPath).absoluteFilePath());
}

void MainWindowTest::selectedFileMetadataIsDisplayed()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString inputPath = createAudioFixture(
        directory, QStringLiteral("회의 자료 01.wav"));
    QVERIFY(!inputPath.isEmpty());

    MainWindow window;
    configureMetadataMock(window);
    auto *transcribeButton = window.findChild<QPushButton *>("transcribeButton");
    auto *recordButton = window.findChild<QPushButton *>("recordStartButton");
    auto *fileButton = window.findChild<QPushButton *>("fileSelectButton");
    auto *nameLabel = window.findChild<QLabel *>("fileNameValueLabel");
    auto *pathLabel = window.findChild<QLabel *>("filePathValueLabel");
    auto *durationLabel = window.findChild<QLabel *>("fileDurationValueLabel");
    auto *sizeLabel = window.findChild<QLabel *>("fileSizeValueLabel");
    QVERIFY(transcribeButton);
    QVERIFY(recordButton);
    QVERIFY(fileButton);
    QVERIFY(nameLabel);
    QVERIFY(pathLabel);
    QVERIFY(durationLabel);
    QVERIFY(sizeLabel);

    window.selectInputFile(inputPath);
    QVERIFY(!transcribeButton->isEnabled());
    QVERIFY(!recordButton->isEnabled());
    QVERIFY(fileButton->isEnabled());
    QCOMPARE(durationLabel->text(), QStringLiteral("확인 중..."));

    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::InputReady, 5000);
    QCOMPARE(QFileInfo(window.currentInputFile()).canonicalFilePath(),
             QFileInfo(inputPath).canonicalFilePath());
    QCOMPARE(nameLabel->text(), QStringLiteral("회의 자료 01.wav"));
    QCOMPARE(pathLabel->text(), QFileInfo(inputPath).absoluteFilePath());
    QCOMPARE(durationLabel->text(), QStringLiteral("00:01:05"));
    QCOMPARE(sizeLabel->text(), QStringLiteral("7 bytes"));
    QVERIFY(transcribeButton->isEnabled());
}

void MainWindowTest::cancelledSelectionKeepsCurrentInput()
{
    QTemporaryFile currentFile;
    QVERIFY(currentFile.open());

    MainWindow window;
    window.setCurrentInputFile(currentFile.fileName());
    const QString before = window.currentInputFile();
    window.selectInputFile(QString());

    QCOMPARE(window.currentInputFile(), before);
    QCOMPARE(window.appState(), AppState::InputReady);
}

void MainWindowTest::decodeFailureKeepsPreviousInput()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString currentPath = createAudioFixture(directory, QStringLiteral("current.wav"));
    const QString corruptPath = createAudioFixture(directory, QStringLiteral("corrupt.wav"));

    MainWindow window;
    configureMetadataMock(window);
    window.setCurrentInputFile(currentPath);
    QSignalSpy errorSpy(&window, &MainWindow::inputFileErrorOccurred);

    window.selectInputFile(corruptPath);
    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::Error, 5000);
    QCOMPARE(errorSpy.count(), 1);
    QCOMPARE(QFileInfo(window.currentInputFile()).canonicalFilePath(),
             QFileInfo(currentPath).canonicalFilePath());
    auto *transcribeButton = window.findChild<QPushButton *>("transcribeButton");
    QVERIFY(transcribeButton);
    QVERIFY(transcribeButton->isEnabled());
}

void MainWindowTest::newerSelectionWins()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString slowPath = createAudioFixture(directory, QStringLiteral("slow.wav"));
    const QString latestPath = createAudioFixture(directory, QStringLiteral("최신 파일.mp3"));

    MainWindow window;
    configureMetadataMock(window);
    window.selectInputFile(slowPath);
    window.selectInputFile(latestPath);

    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::InputReady, 5000);
    QCOMPARE(QFileInfo(window.currentInputFile()).canonicalFilePath(),
             QFileInfo(latestPath).canonicalFilePath());
    QTest::qWait(800);
    QCOMPARE(QFileInfo(window.currentInputFile()).canonicalFilePath(),
             QFileInfo(latestPath).canonicalFilePath());
}

void MainWindowTest::newRunReplacesPreviousTranscript()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString firstPath = createAudioFixture(
        directory, QStringLiteral("ui_success.wav"));
    const QString secondPath = createAudioFixture(
        directory, QStringLiteral("ui_second.wav"));
    QVERIFY(!firstPath.isEmpty());
    QVERIFY(!secondPath.isEmpty());

    MainWindow window;
    configureBackendMock(window);
    auto *transcribeButton = window.findChild<QPushButton *>("transcribeButton");
    auto *resultTextEdit = window.findChild<QTextEdit *>("resultTextEdit");
    QVERIFY(transcribeButton);
    QVERIFY(resultTextEdit);

    window.setCurrentInputFile(firstPath);
    transcribeButton->click();
    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::Completed, 5000);
    QVERIFY(resultTextEdit->toPlainText().contains(QStringLiteral("첫 번째")));

    window.setCurrentInputFile(secondPath);
    transcribeButton->click();
    QCOMPARE(window.appState(), AppState::Processing);
    QVERIFY(resultTextEdit->toPlainText().isEmpty());
    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::Completed, 5000);
    QCOMPARE(resultTextEdit->toPlainText(),
             QStringLiteral("[00:00:03]\n새 작업"));
}

void MainWindowTest::emptyResultDoesNotReusePreviousTranscript()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString firstPath = createAudioFixture(
        directory, QStringLiteral("ui_second.wav"));
    const QString emptyPath = createAudioFixture(
        directory, QStringLiteral("ui_empty.wav"));
    QVERIFY(!firstPath.isEmpty());
    QVERIFY(!emptyPath.isEmpty());

    MainWindow window;
    configureBackendMock(window);
    auto *transcribeButton = window.findChild<QPushButton *>("transcribeButton");
    auto *resultTextEdit = window.findChild<QTextEdit *>("resultTextEdit");
    QVERIFY(transcribeButton);
    QVERIFY(resultTextEdit);

    window.setCurrentInputFile(firstPath);
    transcribeButton->click();
    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::Completed, 5000);
    QVERIFY(!resultTextEdit->toPlainText().isEmpty());

    window.setCurrentInputFile(emptyPath);
    transcribeButton->click();
    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::Completed, 5000);
    QVERIFY(resultTextEdit->toPlainText().isEmpty());
}

void MainWindowTest::longTranscriptTextIsPreserved()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString inputPath = createAudioFixture(
        directory, QStringLiteral("ui_long.wav"));
    QVERIFY(!inputPath.isEmpty());

    MainWindow window;
    configureBackendMock(window);
    auto *transcribeButton = window.findChild<QPushButton *>("transcribeButton");
    auto *resultTextEdit = window.findChild<QTextEdit *>("resultTextEdit");
    QVERIFY(transcribeButton);
    QVERIFY(resultTextEdit);

    window.setCurrentInputFile(inputPath);
    transcribeButton->click();
    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::Completed, 5000);

    const QString result = resultTextEdit->toPlainText();
    QVERIFY(result.size() > 20000);
    QVERIFY(result.startsWith(QStringLiteral("[00:00:01]\n<start>")));
    QVERIFY(result.endsWith(QStringLiteral("<end>")));
}

void MainWindowTest::progressIndicatorsFollowBackendEvents()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString progressPath = createAudioFixture(
        directory, QStringLiteral("ui_progress.wav"));
    const QString nextPath = createAudioFixture(
        directory, QStringLiteral("ui_success.wav"));
    QVERIFY(!progressPath.isEmpty());
    QVERIFY(!nextPath.isEmpty());

    MainWindow window;
    configureBackendMock(window);
    auto *transcribeButton = window.findChild<QPushButton *>("transcribeButton");
    auto *stateLabel = window.findChild<QLabel *>("processingStateLabel");
    auto *timeLabel = window.findChild<QLabel *>("processingTimeLabel");
    auto *progressBar = window.findChild<QProgressBar *>("processingProgressBar");
    QVERIFY(transcribeButton);
    QVERIFY(stateLabel);
    QVERIFY(timeLabel);
    QVERIFY(progressBar);

    window.setCurrentInputFile(progressPath);
    transcribeButton->click();
    QTRY_COMPARE_WITH_TIMEOUT(stateLabel->text(), QStringLiteral("준비 중..."), 5000);
    QCOMPARE(progressBar->minimum(), 0);
    QCOMPARE(progressBar->maximum(), 0);
    QCOMPARE(timeLabel->text(), QStringLiteral("—"));
    QTRY_COMPARE_WITH_TIMEOUT(stateLabel->text(), QStringLiteral("모델 불러오는 중..."), 5000);
    QTRY_COMPARE_WITH_TIMEOUT(stateLabel->text(), QStringLiteral("오디오 디코딩 중..."), 5000);
    QTRY_COMPARE_WITH_TIMEOUT(stateLabel->text(), QStringLiteral("전사 중..."), 5000);
    QTRY_COMPARE_WITH_TIMEOUT(progressBar->value(), 50, 5000);
    QCOMPARE(progressBar->maximum(), 100);
    QCOMPARE(timeLabel->text(), QStringLiteral("00:01:05 / 00:02:10"));
    QTRY_COMPARE_WITH_TIMEOUT(stateLabel->text(), QStringLiteral("화자 분리 중..."), 5000);
    QTRY_COMPARE_WITH_TIMEOUT(stateLabel->text(), QStringLiteral("결과 저장 중..."), 5000);
    QCOMPARE(progressBar->maximum(), 0);
    QCOMPARE(timeLabel->text(), QStringLiteral("00:01:05 / 00:02:10"));

    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::Completed, 5000);
    QCOMPARE(stateLabel->text(), QStringLiteral("완료"));
    QCOMPARE(progressBar->maximum(), 100);
    QCOMPARE(progressBar->value(), 100);
    QCOMPARE(timeLabel->text(), QStringLiteral("00:02:10 / 00:02:10"));

    window.setCurrentInputFile(nextPath);
    transcribeButton->click();
    QCOMPARE(window.appState(), AppState::Processing);
    QCOMPARE(progressBar->maximum(), 100);
    QCOMPARE(progressBar->value(), 0);
    QCOMPARE(timeLabel->text(), QStringLiteral("—"));
    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::Completed, 5000);
}

void MainWindowTest::failedProcessingDoesNotShowCompletionProgress()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString inputPath = createAudioFixture(
        directory, QStringLiteral("ui_progress_error.wav"));
    QVERIFY(!inputPath.isEmpty());

    MainWindow window;
    configureBackendMock(window);
    auto *transcribeButton = window.findChild<QPushButton *>("transcribeButton");
    auto *stateLabel = window.findChild<QLabel *>("processingStateLabel");
    auto *progressBar = window.findChild<QProgressBar *>("processingProgressBar");
    QVERIFY(transcribeButton);
    QVERIFY(stateLabel);
    QVERIFY(progressBar);

    window.setCurrentInputFile(inputPath);
    transcribeButton->click();
    QTRY_COMPARE_WITH_TIMEOUT(progressBar->value(), 99, 5000);
    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::Error, 5000);
    QCOMPARE(stateLabel->text(), QStringLiteral("오류"));
    QCOMPARE(progressBar->maximum(), 100);
    QCOMPARE(progressBar->value(), 0);
}

void MainWindowTest::diarizationOptionsAndTranscriptLabels()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString offPath = createAudioFixture(directory, QStringLiteral("ui_off.wav"));
    const QString autoPath = createAudioFixture(
        directory, QStringLiteral("ui_diarization_auto.wav"));
    const QString fixedPath = createAudioFixture(
        directory, QStringLiteral("ui_diarization_fixed.wav"));
    QVERIFY(!offPath.isEmpty());
    QVERIFY(!autoPath.isEmpty());
    QVERIFY(!fixedPath.isEmpty());

    MainWindow window;
    configureBackendMock(window);
    auto *transcribeButton = window.findChild<QPushButton *>("transcribeButton");
    auto *diarizationCheckBox = window.findChild<QCheckBox *>("diarizationCheckBox");
    auto *speakerCountComboBox = window.findChild<QComboBox *>("speakerCountComboBox");
    auto *resultTextEdit = window.findChild<QTextEdit *>("resultTextEdit");
    QVERIFY(transcribeButton);
    QVERIFY(diarizationCheckBox);
    QVERIFY(speakerCountComboBox);
    QVERIFY(resultTextEdit);
    QVERIFY(!speakerCountComboBox->isEnabled());

    window.setCurrentInputFile(offPath);
    transcribeButton->click();
    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::Completed, 5000);
    QCOMPARE(resultTextEdit->toPlainText(),
             QStringLiteral("[00:00:02]\n라벨 없는 결과"));

    diarizationCheckBox->setChecked(true);
    QVERIFY(speakerCountComboBox->isEnabled());
    QCOMPARE(speakerCountComboBox->currentText(), QStringLiteral("Auto"));
    window.setCurrentInputFile(autoPath);
    transcribeButton->click();
    QVERIFY(!diarizationCheckBox->isEnabled());
    QVERIFY(!speakerCountComboBox->isEnabled());
    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::Completed, 5000);
    QCOMPARE(resultTextEdit->toPlainText(),
             QStringLiteral("[00:00:01] Speaker 1\n자동 화자\n\n"
                            "[00:00:02] Speaker ?\n미지정 화자"));

    speakerCountComboBox->setCurrentText(QStringLiteral("2"));
    window.setCurrentInputFile(fixedPath);
    transcribeButton->click();
    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::Completed, 5000);
    QCOMPARE(resultTextEdit->toPlainText(),
             QStringLiteral("[00:00:01] Speaker 1\n첫 번째 화자\n\n"
                            "[00:00:02] Speaker 2\n두 번째 화자"));
}

void MainWindowTest::diarizationModelFailureAllowsRetry()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString failurePath = createAudioFixture(
        directory, QStringLiteral("ui_diarization_missing_model.wav"));
    const QString retryPath = createAudioFixture(
        directory, QStringLiteral("ui_diarization_fixed.wav"));
    QVERIFY(!failurePath.isEmpty());
    QVERIFY(!retryPath.isEmpty());

    MainWindow window;
    configureBackendMock(window);
    auto *transcribeButton = window.findChild<QPushButton *>("transcribeButton");
    auto *diarizationCheckBox = window.findChild<QCheckBox *>("diarizationCheckBox");
    auto *speakerCountComboBox = window.findChild<QComboBox *>("speakerCountComboBox");
    QVERIFY(transcribeButton);
    QVERIFY(diarizationCheckBox);
    QVERIFY(speakerCountComboBox);

    diarizationCheckBox->setChecked(true);
    speakerCountComboBox->setCurrentText(QStringLiteral("2"));
    window.setCurrentInputFile(failurePath);
    transcribeButton->click();
    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::Error, 5000);
    QVERIFY(transcribeButton->isEnabled());
    QVERIFY(diarizationCheckBox->isEnabled());
    QVERIFY(speakerCountComboBox->isEnabled());

    window.setCurrentInputFile(retryPath);
    transcribeButton->click();
    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::Completed, 5000);
}

void MainWindowTest::cancellationWaitsForExitAndAllowsRestart()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString cancelledPath = createAudioFixture(
        directory, QStringLiteral("ui_cancel_ignores_terminate.wav"));
    const QString retryPath = createAudioFixture(directory, QStringLiteral("ui_success.wav"));
    QVERIFY(!cancelledPath.isEmpty());
    QVERIFY(!retryPath.isEmpty());

    MainWindow window;
    configureBackendMock(window);
    window.backendProcess()->setCancellationGracePeriod(150);
    auto *transcribeButton = window.findChild<QPushButton *>("transcribeButton");
    auto *cancelButton = window.findChild<QPushButton *>("cancelButton");
    QVERIFY(transcribeButton);
    QVERIFY(cancelButton);

    QFile originalFile(cancelledPath);
    QVERIFY(originalFile.open(QIODevice::ReadOnly));
    const QByteArray originalContents = originalFile.readAll();

    window.setCurrentInputFile(cancelledPath);
    transcribeButton->click();
    QTRY_VERIFY_WITH_TIMEOUT(window.backendProcess()->isRunning(), 5000);
    cancelButton->click();

    QCOMPARE(window.appState(), AppState::Processing);
    QVERIFY(!cancelButton->isEnabled());
    QVERIFY(!transcribeButton->isEnabled());
    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::InputReady, 5000);
    QVERIFY(!window.backendProcess()->isRunning());

    QFile unchangedFile(cancelledPath);
    QVERIFY(unchangedFile.open(QIODevice::ReadOnly));
    QCOMPARE(unchangedFile.readAll(), originalContents);

    window.setCurrentInputFile(retryPath);
    transcribeButton->click();
    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::Completed, 5000);
}

void MainWindowTest::closeDefersUntilBackendIsStopped()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString inputPath = createAudioFixture(
        directory, QStringLiteral("ui_cancel_ignores_terminate.wav"));
    QVERIFY(!inputPath.isEmpty());

    MainWindow window;
    configureBackendMock(window);
    window.backendProcess()->setCancellationGracePeriod(150);
    auto *transcribeButton = window.findChild<QPushButton *>("transcribeButton");
    QVERIFY(transcribeButton);

    window.setCurrentInputFile(inputPath);
    transcribeButton->click();
    QTRY_VERIFY_WITH_TIMEOUT(window.backendProcess()->isRunning(), 5000);

    QCloseEvent closeEvent;
    QCoreApplication::sendEvent(&window, &closeEvent);
    QVERIFY(!closeEvent.isAccepted());
    QTRY_VERIFY_WITH_TIMEOUT(!window.backendProcess()->isRunning(), 5000);
    QTRY_COMPARE_WITH_TIMEOUT(window.appState(), AppState::InputReady, 5000);
}

QTEST_MAIN(MainWindowTest)

#include "tst_mainwindow.moc"
