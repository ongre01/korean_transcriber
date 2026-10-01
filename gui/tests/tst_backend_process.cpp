#include "backend/BackendProcess.h"

#include <QFileInfo>
#include <QSignalSpy>
#include <QStandardPaths>
#include <QTest>
#include <QTimer>

class BackendProcessTest : public QObject
{
    Q_OBJECT

private slots:
    void initTestCase();
    void delayedEventsRemainAsynchronous();
    void splitUtf8AndMultipleLinesAreDeliveredOnce();
    void processFailures_data();
    void processFailures();
    void missingPythonProgramIsReportedOnce();

private:
    TranscribeOptions options(const QString &scenario) const;
    void configure(BackendProcess *process) const;

    QString m_pythonProgram;
    QString m_bridgeScript;
};

void BackendProcessTest::initTestCase()
{
    m_pythonProgram = qEnvironmentVariable("PYTHON");
    if (m_pythonProgram.isEmpty()) {
        m_pythonProgram = QStandardPaths::findExecutable(QStringLiteral("python"));
    }
    if (m_pythonProgram.isEmpty()) {
        m_pythonProgram = QStandardPaths::findExecutable(QStringLiteral("python3"));
    }
    QVERIFY2(!m_pythonProgram.isEmpty(), "A Python interpreter is required for this test");

    m_bridgeScript = QFINDTESTDATA("fixtures/mock_backend_process.py");
    QVERIFY2(!m_bridgeScript.isEmpty(), "The mock backend script was not found");
}

TranscribeOptions BackendProcessTest::options(const QString &scenario) const
{
    TranscribeOptions result;
    result.inputFile = scenario;
    result.modelDirectory = QStringLiteral("mock-model");
    return result;
}

void BackendProcessTest::configure(BackendProcess *process) const
{
    process->setPythonProgram(m_pythonProgram);
    process->setBridgeScript(m_bridgeScript);
    process->setWorkingDirectory(QFileInfo(m_bridgeScript).absolutePath());
}

void BackendProcessTest::delayedEventsRemainAsynchronous()
{
    BackendProcess process;
    configure(&process);

    QSignalSpy started(&process, &BackendProcess::started);
    QSignalSpy states(&process, &BackendProcess::stateChanged);
    QSignalSpy progress(&process, &BackendProcess::progressChanged);
    QSignalSpy progressTime(&process, &BackendProcess::progressTimeChanged);
    QSignalSpy segments(&process, &BackendProcess::segmentReceived);
    QSignalSpy completed(&process, &BackendProcess::completed);
    QSignalSpy errors(&process, &BackendProcess::errorOccurred);

    bool timerFired = false;
    QTimer::singleShot(30, this, [&timerFired]() { timerFired = true; });
    process.start(options(QStringLiteral("normal")));

    QTRY_COMPARE_WITH_TIMEOUT(completed.count(), 1, 5000);
    QCOMPARE(errors.count(), 0);
    QCOMPARE(started.count(), 1);
    QVERIFY(timerFired);
    QCOMPARE(states.count(), 1);
    QCOMPARE(states.at(0).at(0).toString(), QStringLiteral("preparing"));
    QCOMPARE(progress.count(), 2);
    QCOMPARE(progress.at(0).at(0).toInt(), -1);
    QCOMPARE(progress.at(1).at(0).toInt(), 50);
    QCOMPARE(progressTime.count(), 1);
    QCOMPARE(segments.count(), 1);
    QCOMPARE(segments.at(0).at(2).toInt(), 1);
    QCOMPARE(segments.at(0).at(3).toString(), QStringLiteral("안녕하세요."));
    QCOMPARE(process.textResultFile(), QStringLiteral("C:/result.txt"));
    QCOMPARE(process.srtResultFile(), QStringLiteral("C:/result.srt"));
    QVERIFY(process.standardErrorOutput().contains("mock diagnostic"));
    QVERIFY(!process.isRunning());
}

void BackendProcessTest::splitUtf8AndMultipleLinesAreDeliveredOnce()
{
    BackendProcess process;
    configure(&process);

    QSignalSpy states(&process, &BackendProcess::stateChanged);
    QSignalSpy progress(&process, &BackendProcess::progressChanged);
    QSignalSpy segments(&process, &BackendProcess::segmentReceived);
    QSignalSpy completed(&process, &BackendProcess::completed);
    QSignalSpy errors(&process, &BackendProcess::errorOccurred);

    process.start(options(QStringLiteral("split")));
    QTRY_COMPARE_WITH_TIMEOUT(completed.count(), 1, 5000);
    QTest::qWait(50);

    QCOMPARE(errors.count(), 0);
    QCOMPARE(states.count(), 1);
    QCOMPARE(progress.count(), 1);
    QCOMPARE(segments.count(), 1);
    QCOMPARE(segments.at(0).at(2).toInt(), 0);
    QCOMPARE(segments.at(0).at(3).toString(), QStringLiteral("한글 분할"));
    QCOMPARE(completed.count(), 1);
}

void BackendProcessTest::processFailures_data()
{
    QTest::addColumn<QString>("scenario");

    QTest::newRow("bridge-error") << QStringLiteral("bridge_error");
    QTest::newRow("malformed-json") << QStringLiteral("malformed");
    QTest::newRow("missing-completed") << QStringLiteral("missing_completed");
    QTest::newRow("event-after-completed") << QStringLiteral("trailing_event");
    QTest::newRow("duplicate-completed") << QStringLiteral("duplicate_completed");
    QTest::newRow("crash") << QStringLiteral("crash");
    QTest::newRow("completed-then-crash") << QStringLiteral("completed_then_crash");
}

void BackendProcessTest::processFailures()
{
    QFETCH(QString, scenario);

    BackendProcess process;
    configure(&process);
    QSignalSpy completed(&process, &BackendProcess::completed);
    QSignalSpy errors(&process, &BackendProcess::errorOccurred);

    process.start(options(scenario));
    QTRY_COMPARE_WITH_TIMEOUT(errors.count(), 1, 5000);
    QTRY_VERIFY_WITH_TIMEOUT(!process.isRunning(), 5000);
    QTest::qWait(50);

    QCOMPARE(errors.count(), 1);
    QCOMPARE(completed.count(), 0);
    QVERIFY(!errors.at(0).at(0).toString().isEmpty());
}

void BackendProcessTest::missingPythonProgramIsReportedOnce()
{
    BackendProcess process;
    process.setPythonProgram(QFileInfo(m_bridgeScript).absolutePath()
                             + QStringLiteral("/missing-python-executable"));
    process.setBridgeScript(m_bridgeScript);
    process.setWorkingDirectory(QFileInfo(m_bridgeScript).absolutePath());

    QSignalSpy completed(&process, &BackendProcess::completed);
    QSignalSpy errors(&process, &BackendProcess::errorOccurred);

    process.start(options(QStringLiteral("normal")));
    QTRY_COMPARE_WITH_TIMEOUT(errors.count(), 1, 5000);
    QTest::qWait(50);

    QCOMPARE(errors.count(), 1);
    QCOMPARE(completed.count(), 0);
    QVERIFY(!process.isRunning());
}

QTEST_GUILESS_MAIN(BackendProcessTest)

#include "tst_backend_process.moc"
