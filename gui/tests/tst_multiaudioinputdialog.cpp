#include "../input/MultiAudioInputDialog.h"

#include <QApplication>
#include <QElapsedTimer>
#include <QFile>
#include <QFileInfo>
#include <QItemSelectionModel>
#include <QLabel>
#include <QPushButton>
#include <QStandardPaths>
#include <QTableView>
#include <QTemporaryDir>
#include <QTimer>
#include <QtTest>

namespace {
QString writeFile(const QTemporaryDir &directory, const QString &name,
                  const QByteArray &contents)
{
    const QString path = directory.filePath(name);
    QFile file(path);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate)
        || file.write(contents) != contents.size()) {
        return {};
    }
    file.close();
    return path;
}

QString createAudioFile(const QTemporaryDir &directory, const QString &name)
{
    return writeFile(directory, name, QByteArrayLiteral("test audio"));
}

MultiAudioInput readyInput(const QString &path, const QString &alias,
                           bool enabled = true)
{
    MultiAudioInput input;
    input.path = path;
    input.alias = alias;
    input.durationMilliseconds = 10000;
    input.enabled = enabled;
    input.status = MultiAudioInput::Status::Ready;
    return input;
}

QString resolvedExecutable(QString candidate)
{
    candidate = candidate.trimmed();
    if (candidate.size() >= 2 && candidate.startsWith(QLatin1Char('"'))
        && candidate.endsWith(QLatin1Char('"'))) {
        candidate = candidate.mid(1, candidate.size() - 2);
    }
    if (candidate.isEmpty()) {
        return {};
    }

    const QFileInfo file(candidate);
    if (file.isFile()) {
        const QString canonicalPath = file.canonicalFilePath();
        return canonicalPath.isEmpty() ? file.absoluteFilePath() : canonicalPath;
    }
    return QStandardPaths::findExecutable(candidate);
}

QString findPythonProgram()
{
    const QString configured = resolvedExecutable(
        QString::fromLocal8Bit(qgetenv("PYTHON")));
    return configured.isEmpty()
        ? QStandardPaths::findExecutable(QStringLiteral("python"))
        : configured;
}

void compareInput(const MultiAudioInput &actual,
                  const MultiAudioInput &expected)
{
    QCOMPARE(actual.path, expected.path);
    QCOMPARE(actual.alias, expected.alias);
    QCOMPARE(actual.durationMilliseconds, expected.durationMilliseconds);
    QCOMPARE(actual.enabled, expected.enabled);
    QCOMPARE(actual.hasAlignmentOffset, expected.hasAlignmentOffset);
    QCOMPARE(actual.alignmentOffsetMilliseconds,
             expected.alignmentOffsetMilliseconds);
    QCOMPARE(actual.alignmentOffsetIsManual,
             expected.alignmentOffsetIsManual);
    QCOMPARE(actual.manualAlignmentCorrectionAllowed,
             expected.manualAlignmentCorrectionAllowed);
    QCOMPARE(actual.hasDrift, expected.hasDrift);
    QCOMPARE(actual.driftPpm, expected.driftPpm);
    QCOMPARE(actual.confidence, expected.confidence);
    QCOMPARE(static_cast<int>(actual.status),
             static_cast<int>(expected.status));
    QCOMPARE(actual.statusDetail, expected.statusDetail);
    QCOMPARE(static_cast<int>(actual.alignmentStatus),
             static_cast<int>(expected.alignmentStatus));
    QCOMPARE(actual.alignmentDetail, expected.alignmentDetail);
}

void compareInputs(const QVector<MultiAudioInput> &actual,
                   const QVector<MultiAudioInput> &expected)
{
    QCOMPARE(actual.size(), expected.size());
    if (actual.size() != expected.size()) {
        return;
    }
    for (int index = 0; index < actual.size(); ++index) {
        compareInput(actual.at(index), expected.at(index));
    }
}

QPushButton *button(MultiAudioInputDialog &dialog, const char *objectName)
{
    return dialog.findChild<QPushButton *>(QString::fromLatin1(objectName));
}
} // namespace

class MultiAudioInputDialogTests : public QObject
{
    Q_OBJECT

private slots:
    void initTestCase();
    void completionMapsShuffledSuccessAndFailureResults();
    void failureResponseRestoresPriorResults();
    void cancelRestoresPriorResultsAndKeepsEventLoopResponsive();
    void inspectionOfExcludedInputsPreventsAlignmentStart();
    void preparationFailureMarksNonReferenceInputFailed();
    void preparationFailureMarksReferenceInputFailed();
    void invalidReferenceResultIsRejected_data();
    void invalidReferenceResultIsRejected();

private:
    QVector<MultiAudioInput> installPriorResults(
        MultiAudioInputDialog &dialog, const QTemporaryDir &directory) const;

    QString m_pythonProgram;
};

void MultiAudioInputDialogTests::initTestCase()
{
    m_pythonProgram = findPythonProgram();
    if (m_pythonProgram.isEmpty()) {
        QSKIP("No Python executable was found via PYTHON or PATH (python).");
    }
}

void MultiAudioInputDialogTests::completionMapsShuffledSuccessAndFailureResults()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());

    QVector<MultiAudioInput> inputs;
    inputs.append(readyInput(createAudioFile(directory, QStringLiteral("a.wav")),
                             QStringLiteral("A")));
    inputs.append(readyInput(createAudioFile(directory, QStringLiteral("excluded.wav")),
                             QStringLiteral("Excluded"), false));
    inputs.append(readyInput(createAudioFile(directory, QStringLiteral("reference.wav")),
                             QStringLiteral("Reference")));
    inputs.append(readyInput(createAudioFile(directory, QStringLiteral("c.wav")),
                             QStringLiteral("C")));
    for (const MultiAudioInput &input : inputs) {
        QVERIFY(!input.path.isEmpty());
    }

    const QByteArray analyzer = R"PY(
import json
import sys
import time

manifest_path = sys.argv[sys.argv.index("--multi-input-manifest") + 1]
with open(manifest_path, "r", encoding="utf-8") as stream:
    manifest = json.load(stream)

assert len(manifest["inputs"]) == 3
assert manifest["reference_index"] == 1
time.sleep(0.20)
payload = {
    "version": 1,
    "ok": True,
    "alignment": {
        "reference_index": 1,
        "inputs": [
            {
                "input_index": 2,
                "reference_index": 1,
                "status": "aligned",
                "offset_ms": 1250,
                "confidence": 0.91,
                "drift_ppm": -42.5,
            },
            {
                "input_index": 0,
                "reference_index": 1,
                "status": "failed",
                "offset_ms": None,
                "confidence": 0.12,
                "drift_ppm": None,
                "failure_reason": "no shared speech",
            },
            {
                "input_index": 1,
                "reference_index": 1,
                "status": "aligned",
                "offset_ms": 0,
                "confidence": 1.0,
                "drift_ppm": 0.0,
            },
        ],
    },
}
print(json.dumps(payload), flush=True)
)PY";
    const QString analyzerPath = writeFile(
        directory, QStringLiteral("successful_analyzer.py"), analyzer);
    QVERIFY(!analyzerPath.isEmpty());

    MultiAudioInputDialog dialog;
    dialog.setInputs(inputs);
    dialog.setReferenceRow(2);
    QCOMPARE(dialog.referenceRow(), 2);
    dialog.setAlignmentAnalyzerConfiguration(m_pythonProgram, analyzerPath);

    QPushButton *analyzeButton = button(
        dialog, "multiAudioAnalyzeAlignmentButton");
    QVERIFY(analyzeButton);
    QVERIFY(analyzeButton->isEnabled());
    analyzeButton->click();

    QVERIFY(dialog.isAlignmentAnalysisRunning());
    QCOMPARE(dialog.model()->inputAt(0).alignmentStatus,
             MultiAudioInput::AlignmentStatus::Analyzing);
    QCOMPARE(dialog.model()->inputAt(2).alignmentStatus,
             MultiAudioInput::AlignmentStatus::Aligned);
    QCOMPARE(dialog.model()->inputAt(3).alignmentStatus,
             MultiAudioInput::AlignmentStatus::Analyzing);

    QTRY_VERIFY_WITH_TIMEOUT(!dialog.isAlignmentAnalysisRunning(), 5000);

    const MultiAudioInput failed = dialog.model()->inputAt(0);
    QCOMPARE(failed.alignmentStatus,
             MultiAudioInput::AlignmentStatus::Failed);
    QVERIFY(!failed.hasAlignmentOffset);
    QCOMPARE(failed.confidence, 0.12);
    QCOMPARE(failed.alignmentDetail, QStringLiteral("no shared speech"));

    const MultiAudioInput excluded = dialog.model()->inputAt(1);
    QCOMPARE(excluded.alignmentStatus,
             MultiAudioInput::AlignmentStatus::NotAnalyzed);

    const MultiAudioInput reference = dialog.model()->inputAt(2);
    QCOMPARE(reference.alignmentStatus,
             MultiAudioInput::AlignmentStatus::Aligned);
    QVERIFY(reference.hasAlignmentOffset);
    QCOMPARE(reference.alignmentOffsetMilliseconds, qint64(0));
    QCOMPARE(reference.confidence, 1.0);

    const MultiAudioInput aligned = dialog.model()->inputAt(3);
    QCOMPARE(aligned.alignmentStatus,
             MultiAudioInput::AlignmentStatus::Aligned);
    QVERIFY(aligned.hasAlignmentOffset);
    QCOMPARE(aligned.alignmentOffsetMilliseconds, qint64(1250));
    QCOMPARE(aligned.confidence, 0.91);
    QVERIFY(aligned.hasDrift);
    QCOMPARE(aligned.driftPpm, -42.5);

    auto *messageLabel = dialog.findChild<QLabel *>(
        QStringLiteral("multiAudioAlignmentMessageLabel"));
    QVERIFY(messageLabel);
    QVERIFY(messageLabel->text().contains(QStringLiteral("실패 1개")));
}

QVector<MultiAudioInput> MultiAudioInputDialogTests::installPriorResults(
    MultiAudioInputDialog &dialog, const QTemporaryDir &directory) const
{
    MultiAudioInput first = readyInput(
        createAudioFile(directory, QStringLiteral("prior-a.wav")),
        QStringLiteral("Prior A"));
    first.hasAlignmentOffset = true;
    first.alignmentOffsetMilliseconds = -310;
    first.hasDrift = true;
    first.driftPpm = 7.25;
    first.confidence = 0.84;
    first.alignmentStatus = MultiAudioInput::AlignmentStatus::Aligned;
    first.alignmentDetail = QStringLiteral("existing automatic result");

    MultiAudioInput reference = readyInput(
        createAudioFile(directory, QStringLiteral("prior-reference.wav")),
        QStringLiteral("Prior Reference"));

    MultiAudioInput manual = readyInput(
        createAudioFile(directory, QStringLiteral("prior-manual.wav")),
        QStringLiteral("Prior Manual"));
    manual.hasAlignmentOffset = true;
    manual.alignmentOffsetMilliseconds = 475;
    manual.alignmentOffsetIsManual = true;
    manual.manualAlignmentCorrectionAllowed = true;
    manual.hasDrift = true;
    manual.driftPpm = -13.5;
    manual.confidence = 0.31;
    manual.alignmentStatus = MultiAudioInput::AlignmentStatus::Manual;
    manual.alignmentDetail = QStringLiteral("existing manual result");

    dialog.setInputs({first, reference, manual});
    dialog.setReferenceRow(1);
    return dialog.model()->inputs();
}

void MultiAudioInputDialogTests::failureResponseRestoresPriorResults()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());

    const QString analyzerPath = writeFile(
        directory,
        QStringLiteral("failing_analyzer.py"),
        R"PY(
import json
import time

time.sleep(0.10)
print(json.dumps({
    "version": 1,
    "ok": False,
    "message": "synthetic alignment failure",
}), flush=True)
raise SystemExit(7)
)PY");
    QVERIFY(!analyzerPath.isEmpty());

    MultiAudioInputDialog dialog;
    const QVector<MultiAudioInput> before = installPriorResults(dialog, directory);
    QCOMPARE(dialog.referenceRow(), 1);
    dialog.setAlignmentAnalyzerConfiguration(m_pythonProgram, analyzerPath);

    QPushButton *analyzeButton = button(
        dialog, "multiAudioAnalyzeAlignmentButton");
    QVERIFY(analyzeButton);
    QVERIFY(analyzeButton->isEnabled());
    analyzeButton->click();
    QVERIFY(dialog.isAlignmentAnalysisRunning());

    QTRY_VERIFY_WITH_TIMEOUT(!dialog.isAlignmentAnalysisRunning(), 5000);
    QCOMPARE(dialog.referenceRow(), 1);
    compareInputs(dialog.model()->inputs(), before);

    auto *messageLabel = dialog.findChild<QLabel *>(
        QStringLiteral("multiAudioAlignmentMessageLabel"));
    QVERIFY(messageLabel);
    QCOMPARE(messageLabel->text(), QStringLiteral("synthetic alignment failure"));
}

void MultiAudioInputDialogTests::cancelRestoresPriorResultsAndKeepsEventLoopResponsive()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());

    const QString analyzerPath = writeFile(
        directory,
        QStringLiteral("cancel_analyzer.py"),
        R"PY(
from pathlib import Path
import time

Path(__file__).with_suffix(".started").write_text("started", encoding="utf-8")
time.sleep(30)
)PY");
    QVERIFY(!analyzerPath.isEmpty());
    const QString startedPath = directory.filePath(
        QStringLiteral("cancel_analyzer.started"));

    MultiAudioInputDialog dialog;
    const QVector<MultiAudioInput> before = installPriorResults(dialog, directory);
    QCOMPARE(dialog.referenceRow(), 1);
    dialog.setAlignmentAnalyzerConfiguration(m_pythonProgram, analyzerPath);

    QPushButton *analyzeButton = button(
        dialog, "multiAudioAnalyzeAlignmentButton");
    QPushButton *cancelButton = button(
        dialog, "multiAudioCancelAlignmentButton");
    QVERIFY(analyzeButton);
    QVERIFY(cancelButton);
    QVERIFY(analyzeButton->isEnabled());

    bool eventDelivered = false;
    QTimer::singleShot(0, &dialog, [&eventDelivered]() {
        eventDelivered = true;
    });
    QElapsedTimer clickDuration;
    clickDuration.start();
    analyzeButton->click();
    QVERIFY2(clickDuration.elapsed() < 1000,
             "Starting alignment blocked the GUI thread.");
    QVERIFY(dialog.isAlignmentAnalysisRunning());

    QTRY_VERIFY_WITH_TIMEOUT(eventDelivered, 1000);
    QTRY_VERIFY_WITH_TIMEOUT(QFileInfo::exists(startedPath), 3000);
    QVERIFY(dialog.isAlignmentAnalysisRunning());
    QVERIFY(cancelButton->isEnabled());

    cancelButton->click();
    QTRY_VERIFY_WITH_TIMEOUT(!dialog.isAlignmentAnalysisRunning(), 5000);

    QCOMPARE(dialog.referenceRow(), 1);
    compareInputs(dialog.model()->inputs(), before);
    QVERIFY(analyzeButton->isEnabled());

    auto *messageLabel = dialog.findChild<QLabel *>(
        QStringLiteral("multiAudioAlignmentMessageLabel"));
    QVERIFY(messageLabel);
    QVERIFY(messageLabel->text().contains(QStringLiteral("이전 입력과 정렬 결과")));
}

void MultiAudioInputDialogTests::inspectionOfExcludedInputsPreventsAlignmentStart()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());

    const QString probePath = writeFile(
        directory,
        QStringLiteral("slow_probe.py"),
        R"PY(
from pathlib import Path
import time

Path(__file__).with_suffix(".started").write_text("started", encoding="utf-8")
time.sleep(30)
)PY");
    const QString analyzerPath = writeFile(
        directory,
        QStringLiteral("blocked_analyzer.py"),
        R"PY(
from pathlib import Path

Path(__file__).with_suffix(".started").write_text("started", encoding="utf-8")
)PY");
    QVERIFY(!probePath.isEmpty());
    QVERIFY(!analyzerPath.isEmpty());

    MultiAudioInput first = readyInput(
        createAudioFile(directory, QStringLiteral("ready-a.wav")),
        QStringLiteral("Ready A"));
    MultiAudioInput second = readyInput(
        createAudioFile(directory, QStringLiteral("ready-b.wav")),
        QStringLiteral("Ready B"));
    MultiAudioInput inspecting = readyInput(
        createAudioFile(directory, QStringLiteral("excluded-inspecting.wav")),
        QStringLiteral("Excluded Inspecting"), false);
    inspecting.durationMilliseconds = -1;
    inspecting.status = MultiAudioInput::Status::Pending;
    MultiAudioInput queued = readyInput(
        createAudioFile(directory, QStringLiteral("excluded-pending.wav")),
        QStringLiteral("Excluded Pending"), false);
    queued.durationMilliseconds = -1;
    queued.status = MultiAudioInput::Status::Pending;
    QVERIFY(!first.path.isEmpty());
    QVERIFY(!second.path.isEmpty());
    QVERIFY(!inspecting.path.isEmpty());
    QVERIFY(!queued.path.isEmpty());

    MultiAudioInputDialog dialog;
    dialog.setAudioFileInfoConfiguration(m_pythonProgram, probePath);
    dialog.setAlignmentAnalyzerConfiguration(m_pythonProgram, analyzerPath);
    dialog.setInputs({first, second, inspecting, queued});

    QCOMPARE(dialog.model()->inputAt(2).status,
             MultiAudioInput::Status::Inspecting);
    QCOMPARE(dialog.model()->inputAt(3).status,
             MultiAudioInput::Status::Pending);
    QVERIFY2(dialog.model()->isConfigurationValid(),
             "Excluded inspections must not invalidate the ready input set; "
             "the dialog's in-flight inspection guard is under test.");

    const QString probeStartedPath = directory.filePath(
        QStringLiteral("slow_probe.started"));
    const QString analyzerStartedPath = directory.filePath(
        QStringLiteral("blocked_analyzer.started"));
    QTRY_VERIFY_WITH_TIMEOUT(QFileInfo::exists(probeStartedPath), 3000);

    QPushButton *analyzeButton = button(
        dialog, "multiAudioAnalyzeAlignmentButton");
    QVERIFY(analyzeButton);
    QVERIFY(!analyzeButton->isEnabled());

    analyzeButton->click();
    QVERIFY(!dialog.isAlignmentAnalysisRunning());
    QVERIFY(!QFileInfo::exists(analyzerStartedPath));
    QCOMPARE(dialog.model()->inputAt(2).status,
             MultiAudioInput::Status::Inspecting);
    QCOMPARE(dialog.model()->inputAt(3).status,
             MultiAudioInput::Status::Pending);
}

void MultiAudioInputDialogTests::preparationFailureMarksNonReferenceInputFailed()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());

    const QString analyzerPath = writeFile(
        directory,
        QStringLiteral("preparation_failure_nonreference.py"),
        R"PY(
import json

print(json.dumps({
    "version": 1,
    "ok": False,
    "kind": "preparation",
    "message": "one source could not be prepared",
    "errors": [{
        "input_index": 2,
        "code": "decode_failed",
        "message": "unreadable channel",
    }],
}), flush=True)
raise SystemExit(1)
)PY");
    QVERIFY(!analyzerPath.isEmpty());

    MultiAudioInputDialog dialog;
    const QVector<MultiAudioInput> before = installPriorResults(dialog, directory);
    QVERIFY(dialog.model()->isAlignmentResolved());
    QCOMPARE(dialog.referenceRow(), 1);
    dialog.setAlignmentAnalyzerConfiguration(m_pythonProgram, analyzerPath);

    QPushButton *analyzeButton = button(
        dialog, "multiAudioAnalyzeAlignmentButton");
    QVERIFY(analyzeButton);
    QVERIFY(analyzeButton->isEnabled());
    analyzeButton->click();
    QVERIFY(dialog.isAlignmentAnalysisRunning());
    QTRY_VERIFY_WITH_TIMEOUT(!dialog.isAlignmentAnalysisRunning(), 5000);

    compareInput(dialog.model()->inputAt(0), before.at(0));
    compareInput(dialog.model()->inputAt(1), before.at(1));
    const MultiAudioInput failed = dialog.model()->inputAt(2);
    QCOMPARE(failed.alignmentStatus,
             MultiAudioInput::AlignmentStatus::Failed);
    QVERIFY(!failed.hasAlignmentOffset);
    QCOMPARE(failed.alignmentOffsetMilliseconds, qint64(0));
    QVERIFY(!failed.alignmentOffsetIsManual);
    QVERIFY(!failed.manualAlignmentCorrectionAllowed);
    QVERIFY(!failed.hasDrift);
    QCOMPARE(failed.confidence, 0.0);
    QCOMPARE(failed.alignmentDetail,
             QStringLiteral("[decode_failed] unreadable channel"));

    QString reason;
    QVERIFY(!dialog.model()->isAlignmentResolved(&reason));
    QVERIFY(reason.contains(QStringLiteral("실패")));
    QVERIFY(!dialog.model()->setManualAlignmentOffset(
        failed.path, 900, QStringLiteral("must remain blocked")));
    QVERIFY(!dialog.model()->inputAt(2).hasAlignmentOffset);

    auto *tableView = dialog.findChild<QTableView *>(
        QStringLiteral("multiAudioInputTableView"));
    auto *manualOffsetButton = button(
        dialog, "multiAudioManualOffsetButton");
    QVERIFY(tableView);
    QVERIFY(manualOffsetButton);
    tableView->selectRow(2);
    QCOMPARE(tableView->selectionModel()->selectedRows().size(), 1);
    QCOMPARE(tableView->selectionModel()->selectedRows().constFirst().row(), 2);
    QVERIFY(!manualOffsetButton->isEnabled());

    auto *messageLabel = dialog.findChild<QLabel *>(
        QStringLiteral("multiAudioAlignmentMessageLabel"));
    QVERIFY(messageLabel);
    QVERIFY(messageLabel->text().contains(QStringLiteral("Prior Manual")));
    QVERIFY(messageLabel->text().contains(QStringLiteral("unreadable channel")));
}

void MultiAudioInputDialogTests::preparationFailureMarksReferenceInputFailed()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());

    const QString analyzerPath = writeFile(
        directory,
        QStringLiteral("preparation_failure_reference.py"),
        R"PY(
import json

print(json.dumps({
    "version": 1,
    "ok": False,
    "kind": "preparation",
    "errors": [{
        "input_index": 1,
        "code": "empty_audio",
        "message": "reference contains no decodable audio",
    }],
}), flush=True)
raise SystemExit(1)
)PY");
    QVERIFY(!analyzerPath.isEmpty());

    MultiAudioInputDialog dialog;
    const QVector<MultiAudioInput> before = installPriorResults(dialog, directory);
    QVERIFY(dialog.model()->isAlignmentResolved());
    QCOMPARE(dialog.referenceRow(), 1);
    dialog.setAlignmentAnalyzerConfiguration(m_pythonProgram, analyzerPath);

    QPushButton *analyzeButton = button(
        dialog, "multiAudioAnalyzeAlignmentButton");
    QVERIFY(analyzeButton);
    QVERIFY(analyzeButton->isEnabled());
    analyzeButton->click();
    QVERIFY(dialog.isAlignmentAnalysisRunning());
    QTRY_VERIFY_WITH_TIMEOUT(!dialog.isAlignmentAnalysisRunning(), 5000);

    compareInput(dialog.model()->inputAt(0), before.at(0));
    compareInput(dialog.model()->inputAt(2), before.at(2));
    QCOMPARE(dialog.referenceRow(), 1);
    const MultiAudioInput failedReference = dialog.model()->inputAt(1);
    QCOMPARE(failedReference.alignmentStatus,
             MultiAudioInput::AlignmentStatus::Failed);
    QVERIFY(!failedReference.hasAlignmentOffset);
    QVERIFY(!failedReference.hasDrift);
    QCOMPARE(failedReference.confidence, 0.0);
    QCOMPARE(failedReference.alignmentDetail,
             QStringLiteral("[empty_audio] reference contains no decodable audio"));

    QString reason;
    QVERIFY(!dialog.model()->isAlignmentResolved(&reason));
    QVERIFY(reason.contains(QStringLiteral("기준 파일")));
    QVERIFY(reason.contains(QStringLiteral("실패")));
    auto *messageLabel = dialog.findChild<QLabel *>(
        QStringLiteral("multiAudioAlignmentMessageLabel"));
    QVERIFY(messageLabel);
    QVERIFY(messageLabel->text().contains(QStringLiteral("Prior Reference")));
}

void MultiAudioInputDialogTests::invalidReferenceResultIsRejected_data()
{
    QTest::addColumn<QString>("scriptName");
    QTest::addColumn<QByteArray>("referenceResult");

    QTest::newRow("reference-failed")
        << QStringLiteral("invalid_reference_failed.py")
        << QByteArrayLiteral(
               "{\"input_index\": 1, \"reference_index\": 1, "
               "\"status\": \"failed\", \"offset_ms\": None, "
               "\"confidence\": 0.0, \"drift_ppm\": None}");
    QTest::newRow("reference-nonzero-offset")
        << QStringLiteral("invalid_reference_nonzero.py")
        << QByteArrayLiteral(
               "{\"input_index\": 1, \"reference_index\": 1, "
               "\"status\": \"aligned\", \"offset_ms\": 25, "
               "\"confidence\": 1.0, \"drift_ppm\": 0.0}");
}

void MultiAudioInputDialogTests::invalidReferenceResultIsRejected()
{
    QFETCH(QString, scriptName);
    QFETCH(QByteArray, referenceResult);

    QTemporaryDir directory;
    QVERIFY(directory.isValid());

    const QByteArray analyzer = QByteArrayLiteral(R"PY(
import json

reference_result = )PY")
        + referenceResult
        + QByteArrayLiteral(R"PY(
payload = {
    "version": 1,
    "ok": True,
    "alignment": {
        "reference_index": 1,
        "inputs": [
            {
                "input_index": 0,
                "reference_index": 1,
                "status": "aligned",
                "offset_ms": -310,
                "confidence": 0.84,
                "drift_ppm": 7.25,
            },
            reference_result,
            {
                "input_index": 2,
                "reference_index": 1,
                "status": "aligned",
                "offset_ms": 475,
                "confidence": 0.75,
                "drift_ppm": -13.5,
            },
        ],
    },
}
print(json.dumps(payload), flush=True)
)PY");
    const QString analyzerPath = writeFile(directory, scriptName, analyzer);
    QVERIFY(!analyzerPath.isEmpty());

    MultiAudioInputDialog dialog;
    const QVector<MultiAudioInput> before = installPriorResults(dialog, directory);
    QVERIFY(dialog.model()->isAlignmentResolved());
    dialog.setAlignmentAnalyzerConfiguration(m_pythonProgram, analyzerPath);

    QPushButton *analyzeButton = button(
        dialog, "multiAudioAnalyzeAlignmentButton");
    QVERIFY(analyzeButton);
    QVERIFY(analyzeButton->isEnabled());
    analyzeButton->click();
    QVERIFY(dialog.isAlignmentAnalysisRunning());
    QTRY_VERIFY_WITH_TIMEOUT(!dialog.isAlignmentAnalysisRunning(), 5000);

    QCOMPARE(dialog.referenceRow(), 1);
    compareInputs(dialog.model()->inputs(), before);
    QVERIFY(dialog.model()->isAlignmentResolved());
    auto *messageLabel = dialog.findChild<QLabel *>(
        QStringLiteral("multiAudioAlignmentMessageLabel"));
    QVERIFY(messageLabel);
    QVERIFY(messageLabel->text().contains(QStringLiteral("형식")));
}

int main(int argc, char **argv)
{
#if defined(Q_OS_UNIX) && !defined(Q_OS_MACOS)
    if (qgetenv("QT_QPA_PLATFORM").isEmpty()
        && qgetenv("DISPLAY").isEmpty()) {
        qputenv("QT_QPA_PLATFORM", QByteArrayLiteral("offscreen"));
    }
#endif
    QApplication application(argc, argv);
    MultiAudioInputDialogTests tests;
    return QTest::qExec(&tests, argc, argv);
}

#include "tst_multiaudioinputdialog.moc"
