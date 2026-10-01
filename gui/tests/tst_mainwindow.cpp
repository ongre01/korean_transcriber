#include "app/AppState.h"
#include "mainwindow.h"

#include <QPushButton>
#include <QLabel>
#include <QFile>
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
    void selectedFileMetadataIsDisplayed();
    void cancelledSelectionKeepsCurrentInput();
    void decodeFailureKeepsPreviousInput();
    void newerSelectionWins();
    void newRunReplacesPreviousTranscript();
    void emptyResultDoesNotReusePreviousTranscript();
    void longTranscriptTextIsPreserved();
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
                            "[00:00:12] Speaker 2\n<b>두 번째</b>"));
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
             QStringLiteral("[00:00:03] Speaker 1\n새 작업"));
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

QTEST_MAIN(MainWindowTest)

#include "tst_mainwindow.moc"
