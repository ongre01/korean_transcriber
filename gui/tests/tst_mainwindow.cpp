#include "app/AppState.h"
#include "mainwindow.h"

#include <QPushButton>
#include <QSignalSpy>
#include <QTemporaryFile>
#include <QTest>

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
};

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
    QTemporaryFile inputFile;
    QVERIFY(inputFile.open());

    MainWindow window;
    QCOMPARE(window.appState(), AppState::Idle);

    window.setCurrentInputFile(inputFile.fileName());
    QCOMPARE(window.appState(), AppState::InputReady);

    auto *transcribeButton = window.findChild<QPushButton *>("transcribeButton");
    QVERIFY(transcribeButton);
    QSignalSpy startSpy(&window, &MainWindow::transcriptionStartRequested);

    transcribeButton->click();
    QCOMPARE(window.appState(), AppState::Processing);
    QCOMPARE(startSpy.count(), 1);

    transcribeButton->click();
    QCOMPARE(window.appState(), AppState::Processing);
    QCOMPARE(startSpy.count(), 1);

    window.processingCompleted();
    QCOMPARE(window.appState(), AppState::Completed);
    QVERIFY(transcribeButton->isEnabled());
}

void MainWindowTest::errorAllowsRetry()
{
    QTemporaryFile inputFile;
    QVERIFY(inputFile.open());

    MainWindow window;
    window.setCurrentInputFile(inputFile.fileName());

    auto *transcribeButton = window.findChild<QPushButton *>("transcribeButton");
    QVERIFY(transcribeButton);
    QSignalSpy startSpy(&window, &MainWindow::transcriptionStartRequested);

    transcribeButton->click();
    window.processingFailed(QStringLiteral("mock failure"));
    QCOMPARE(window.appState(), AppState::Error);
    QVERIFY(transcribeButton->isEnabled());

    transcribeButton->click();
    QCOMPARE(window.appState(), AppState::Processing);
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

QTEST_MAIN(MainWindowTest)

#include "tst_mainwindow.moc"
