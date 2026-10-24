#include "../input/MultiAudioInputDialog.h"
#include "../app/EnginePaths.h"

#include <QApplication>
#include <QComboBox>
#include <QElapsedTimer>
#include <QFile>
#include <QFileInfo>
#include <QItemSelectionModel>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QProcess>
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

QLabel *label(MultiAudioInputDialog &dialog, const char *objectName)
{
    return dialog.findChild<QLabel *>(QString::fromLatin1(objectName));
}

QVector<QJsonObject> recordedRequests(const QString &path)
{
    QVector<QJsonObject> requests;
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return requests;
    }
    for (const QByteArray &line : file.readAll().split('\n')) {
        if (!line.trimmed().isEmpty()) {
            requests.append(QJsonDocument::fromJson(line).object());
        }
    }
    return requests;
}

// Stands in for multimic_quality.py: records every manifest it receives and
// answers like the engine, applying the manifest's overrides to a fixed
// automatic plan of three segments.
const QByteArray kQualityAnalyzer = R"PY(
import json
import sys
from pathlib import Path

manifest_path = sys.argv[sys.argv.index("--multi-input-manifest") + 1]
with open(manifest_path, "r", encoding="utf-8") as stream:
    manifest = json.load(stream)
with Path(__file__).with_suffix(".requests.jsonl").open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(manifest) + "\n")

first_offset = -310.4
if Path(__file__).with_suffix(".changed").exists():
    first_offset = -300.0
alignment = [
    {"input_index": 0, "source": "automatic", "status": "aligned", "offset_ms": first_offset},
    {"input_index": 1, "source": "reference", "status": "aligned", "offset_ms": 0.0},
    {"input_index": 2, "source": "manual", "status": "aligned", "offset_ms": 475.0},
]
overrides = {
    (item["start_sample"], item["end_sample"]): item["input_index"]
    for item in manifest["selection"].get("overrides", [])
}
segments = []
for start, end, automatic in ((0, 16000, 0), (16000, 40000, 2), (40000, 48000, 0)):
    inputs = [
        {"input_index": index, "quality_score": score, "valid_ratio": 1.0}
        for index, score in enumerate((0.9, 0.55, 0.7))
    ]
    selected = overrides.get((start, end), automatic)
    manual = selected != automatic
    segments.append({
        "start_sample": start,
        "end_sample": end,
        "start": start / 16000,
        "end": end / 16000,
        "selected_input_index": selected,
        "automatic_input_index": automatic,
        "selection_source": "manual_override" if manual else "automatic",
        "quality_score": inputs[selected]["quality_score"],
        "confidence": 1.0 if manual else 0.8,
        "reason": "manual_override" if manual else "best_quality",
        "reason_codes": ["manual_override" if manual else "best_quality"],
        "automatic": {
            "input_index": automatic,
            "quality_score": inputs[automatic]["quality_score"],
            "confidence": 0.8,
            "reason": "best_quality",
            "reason_codes": ["best_quality"],
            "segment_start_sample": start,
            "segment_end_sample": end,
        },
        "inputs": inputs,
    })
print(json.dumps({
    "version": 1,
    "ok": True,
    "warnings": [],
    "alignment": {"reference_index": manifest["reference_index"], "inputs": alignment},
    "selection": {
        "sample_rate": 16000,
        "timeline_sample_count": 48000,
        "rows": segments,
        "warnings": [],
    },
}), flush=True)
)PY";
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
    void qualityAnalysisShowsEvidenceAndKeepsManualSelection();
    void qualityAnalysisCancelKeepsPlanAndEventLoopResponsive();
    void qualityAnalysisRejectsChangedAlignment();
    void qualityAnalysisFailureNamesUnresolvedFile();
    void qualityAnalysisNeedsResolvedAlignmentAndPreviewIsFollowUp();
    void realEngineQualityAnalysisRoundTrip();

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

void MultiAudioInputDialogTests::qualityAnalysisShowsEvidenceAndKeepsManualSelection()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString analyzerPath = writeFile(
        directory, QStringLiteral("quality_analyzer.py"), kQualityAnalyzer);
    QVERIFY(!analyzerPath.isEmpty());
    const QString requestLog = directory.filePath(
        QStringLiteral("quality_analyzer.requests.jsonl"));

    MultiAudioInputDialog dialog;
    const QVector<MultiAudioInput> inputs = installPriorResults(dialog, directory);
    QVERIFY(dialog.model()->isAlignmentResolved());
    dialog.setQualityAnalyzerConfiguration(m_pythonProgram, analyzerPath);

    QPushButton *analyzeButton = button(dialog, "multiAudioAnalyzeQualityButton");
    QPushButton *applyButton = button(dialog, "multiAudioApplyOverrideButton");
    auto *selectionView = dialog.findChild<QTableView *>(
        QStringLiteral("multiAudioSelectionTableView"));
    auto *overrideCombo = dialog.findChild<QComboBox *>(
        QStringLiteral("multiAudioOverrideComboBox"));
    QLabel *summaryLabel = label(dialog, "multiAudioSelectionSummaryLabel");
    QVERIFY(analyzeButton && applyButton && selectionView && overrideCombo
            && summaryLabel);
    QVERIFY(analyzeButton->isEnabled());
    QVERIFY(!applyButton->isEnabled());

    analyzeButton->click();
    QVERIFY(dialog.isQualityAnalysisRunning());
    QVERIFY(!button(dialog, "multiAudioAnalyzeAlignmentButton")->isEnabled());
    QVERIFY(!button(dialog, "multiAudioAddButton")->isEnabled());
    QTRY_VERIFY_WITH_TIMEOUT(!dialog.isQualityAnalysisRunning(), 5000);

    MultiAudioSelectionModel *model = dialog.selectionModel();
    QCOMPARE(model->rowCount(), 3);
    for (int row = 0; row < model->rowCount(); ++row) {
        QCOMPARE(model->index(row, MultiAudioSelectionModel::SourceColumn)
                     .data().toString(),
                 QStringLiteral("자동"));
    }
    QCOMPARE(model->index(1, MultiAudioSelectionModel::AutomaticInputColumn)
                 .data().toString(),
             QStringLiteral("Prior Manual"));
    QCOMPARE(model->index(1, MultiAudioSelectionModel::AutomaticScoreColumn)
                 .data().toString(),
             QLocale().toString(0.7, 'f', 2));
    QCOMPARE(model->index(1, MultiAudioSelectionModel::ReasonColumn)
                 .data().toString(),
             QStringLiteral("최고 품질"));
    QVERIFY(summaryLabel->text().contains(QStringLiteral("구간 3개")));

    QVector<QJsonObject> requests = recordedRequests(requestLog);
    QCOMPARE(requests.size(), 1);
    QVERIFY(requests.at(0).value(QStringLiteral("selection")).toObject()
                .value(QStringLiteral("overrides")).toArray().isEmpty());
    QCOMPARE(requests.at(0).value(QStringLiteral("alignment")).toObject()
                 .value(QStringLiteral("manual_offsets_ms")).toObject()
                 .value(QStringLiteral("2")).toInteger(),
             qint64(475));

    // Force the reference microphone for the second interval.
    selectionView->selectRow(1);
    const int referenceItem = overrideCombo->findText(QStringLiteral("Prior Reference"));
    QVERIFY(referenceItem >= 0);
    overrideCombo->setCurrentIndex(referenceItem);
    QVERIFY(applyButton->isEnabled());
    applyButton->click();
    QVERIFY(model->index(1, 0).data(MultiAudioSelectionModel::IsManualRole).toBool());
    QCOMPARE(model->index(1, MultiAudioSelectionModel::FinalInputColumn)
                 .data().toString(),
             QStringLiteral("Prior Reference"));
    QCOMPARE(model->index(1, MultiAudioSelectionModel::SourceColumn)
                 .data().toString(),
             QStringLiteral("수동"));
    QCOMPARE(model->index(1, MultiAudioSelectionModel::AutomaticInputColumn)
                 .data().toString(),
             QStringLiteral("Prior Manual"));
    QVERIFY(summaryLabel->text().contains(QStringLiteral("수동 1")));

    // The next analysis sends the manual choice in the manifest, and the
    // engine's final plan keeps it.
    analyzeButton->click();
    QTRY_VERIFY_WITH_TIMEOUT(!dialog.isQualityAnalysisRunning(), 5000);
    requests = recordedRequests(requestLog);
    QCOMPARE(requests.size(), 2);
    const QJsonArray overrides = requests.at(1).value(QStringLiteral("selection"))
                                     .toObject()
                                     .value(QStringLiteral("overrides"))
                                     .toArray();
    QCOMPARE(overrides.size(), 1);
    const QJsonObject override = overrides.at(0).toObject();
    QCOMPARE(override.value(QStringLiteral("start_sample")).toInteger(), qint64(16000));
    QCOMPARE(override.value(QStringLiteral("end_sample")).toInteger(), qint64(40000));
    QCOMPARE(override.value(QStringLiteral("input_index")).toInt(), 1);

    const MultiAudioSelectionPlan plan = dialog.selectionPlan();
    QCOMPARE(plan.manualSegmentCount(), 1);
    QCOMPARE(plan.segments.at(1).overrideInputPath, inputs.at(1).path);
    QCOMPARE(plan.segments.at(1).automaticInputPath, inputs.at(2).path);
    QVERIFY(plan.isCurrentFor(dialog.model()->inputs(), dialog.referenceRow()));
    QVERIFY(label(dialog, "multiAudioQualityMessageLabel")->text().contains(
        QStringLiteral("수동 선택 1개")));

    // Excluding a microphone makes the result stale until it is re-run.
    QVERIFY(dialog.model()->setData(
        dialog.model()->index(0, MultiAudioInputModel::UseColumn),
        Qt::Unchecked, Qt::CheckStateRole));
    QVERIFY(!dialog.selectionPlan().isCurrentFor(dialog.model()->inputs(),
                                                 dialog.referenceRow()));
    selectionView->selectRow(0);
    QVERIFY(!applyButton->isEnabled());
    QVERIFY(summaryLabel->text().contains(QStringLiteral("최신이 아닙니다")));
}

void MultiAudioInputDialogTests::qualityAnalysisCancelKeepsPlanAndEventLoopResponsive()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString analyzerPath = writeFile(
        directory,
        QStringLiteral("quality_cancel.py"),
        R"PY(
from pathlib import Path
import time

Path(__file__).with_suffix(".started").write_text("started", encoding="utf-8")
time.sleep(30)
)PY");
    QVERIFY(!analyzerPath.isEmpty());
    const QString startedPath = directory.filePath(
        QStringLiteral("quality_cancel.started"));

    MultiAudioInputDialog dialog;
    const QVector<MultiAudioInput> inputs = installPriorResults(dialog, directory);
    MultiAudioSelectionPlan prior;
    prior.analysisKey = MultiAudioInputModel::selectionAnalysisKey(
        inputs, dialog.referenceRow());
    prior.timelineKey = MultiAudioInputModel::selectionTimelineKey(
        inputs, dialog.referenceRow());
    prior.sampleRate = 16000;
    prior.timelineSampleCount = 32000;
    for (const MultiAudioInput &input : inputs) {
        prior.inputPaths.append(input.path);
    }
    for (int index = 0; index < 2; ++index) {
        MultiAudioSelectionSegment segment;
        segment.startSample = index * 16000;
        segment.endSample = (index + 1) * 16000;
        segment.automaticInputPath = inputs.at(index).path;
        segment.automaticReasonCodes = {QStringLiteral("best_quality")};
        for (const MultiAudioInput &input : inputs) {
            segment.inputs.append({input.path, true, 0.5, 1.0});
        }
        prior.segments.append(segment);
    }
    prior.segments[0].overrideInputPath = inputs.at(2).path;
    dialog.setSelectionPlan(prior);
    dialog.setQualityAnalyzerConfiguration(m_pythonProgram, analyzerPath);
    QCOMPARE(dialog.selectionModel()->rowCount(), 2);

    QPushButton *analyzeButton = button(dialog, "multiAudioAnalyzeQualityButton");
    QPushButton *cancelButton = button(dialog, "multiAudioCancelQualityButton");
    QPushButton *clearAllButton = button(dialog, "multiAudioClearAllOverridesButton");
    QVERIFY(analyzeButton && cancelButton && clearAllButton);
    QVERIFY(clearAllButton->isEnabled());

    bool eventDelivered = false;
    QTimer::singleShot(0, &dialog, [&eventDelivered]() {
        eventDelivered = true;
    });
    QElapsedTimer clickDuration;
    clickDuration.start();
    analyzeButton->click();
    QVERIFY2(clickDuration.elapsed() < 1000,
             "Starting quality analysis blocked the GUI thread.");
    QVERIFY(dialog.isQualityAnalysisRunning());
    QTRY_VERIFY_WITH_TIMEOUT(eventDelivered, 1000);
    QTRY_VERIFY_WITH_TIMEOUT(QFileInfo::exists(startedPath), 3000);
    QVERIFY(cancelButton->isEnabled());
    QVERIFY(!clearAllButton->isEnabled());
    QVERIFY(!button(dialog, "multiAudioRemoveButton")->isEnabled());

    cancelButton->click();
    QTRY_VERIFY_WITH_TIMEOUT(!dialog.isQualityAnalysisRunning(), 5000);

    const MultiAudioSelectionPlan after = dialog.selectionPlan();
    QCOMPARE(after.segments.size(), 2);
    QCOMPARE(after.manualSegmentCount(), 1);
    QVERIFY(after.isCurrentFor(dialog.model()->inputs(), dialog.referenceRow()));
    QVERIFY(analyzeButton->isEnabled());
    QVERIFY(clearAllButton->isEnabled());
    QVERIFY(label(dialog, "multiAudioQualityMessageLabel")->text().contains(
        QStringLiteral("이전 분석 결과")));
}

void MultiAudioInputDialogTests::qualityAnalysisRejectsChangedAlignment()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString analyzerPath = writeFile(
        directory, QStringLiteral("quality_changed.py"), kQualityAnalyzer);
    QVERIFY(!analyzerPath.isEmpty());
    QVERIFY(!writeFile(directory, QStringLiteral("quality_changed.changed"),
                       QByteArrayLiteral("1")).isEmpty());

    MultiAudioInputDialog dialog;
    installPriorResults(dialog, directory);
    dialog.setQualityAnalyzerConfiguration(m_pythonProgram, analyzerPath);

    button(dialog, "multiAudioAnalyzeQualityButton")->click();
    QVERIFY(dialog.isQualityAnalysisRunning());
    QTRY_VERIFY_WITH_TIMEOUT(!dialog.isQualityAnalysisRunning(), 5000);

    QVERIFY(dialog.selectionPlan().isEmpty());
    const QString message = label(dialog, "multiAudioQualityMessageLabel")->text();
    QVERIFY(message.contains(QStringLiteral("Prior A")));
    QVERIFY(message.contains(QStringLiteral("검토한 값과 다릅니다")));
}

void MultiAudioInputDialogTests::qualityAnalysisFailureNamesUnresolvedFile()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString analyzerPath = writeFile(
        directory,
        QStringLiteral("quality_unresolved.py"),
        R"PY(
import json

print(json.dumps({
    "version": 1,
    "ok": False,
    "kind": "alignment",
    "code": "alignment_unresolved",
    "message": "unresolved",
    "errors": [{
        "input_index": 0,
        "status": "failed",
        "code": "insufficient_shared_audio",
        "message": "Too few shared events.",
    }],
    "warnings": [],
}), flush=True)
raise SystemExit(1)
)PY");
    QVERIFY(!analyzerPath.isEmpty());

    MultiAudioInputDialog dialog;
    installPriorResults(dialog, directory);
    dialog.setQualityAnalyzerConfiguration(m_pythonProgram, analyzerPath);

    button(dialog, "multiAudioAnalyzeQualityButton")->click();
    QTRY_VERIFY_WITH_TIMEOUT(!dialog.isQualityAnalysisRunning(), 5000);

    QVERIFY(dialog.selectionPlan().isEmpty());
    const QString message = label(dialog, "multiAudioQualityMessageLabel")->text();
    QVERIFY(message.contains(QStringLiteral("시간 정렬이 해결되지 않은")));
    QVERIFY(message.contains(QStringLiteral("Prior A")));
    QVERIFY(message.contains(QStringLiteral("insufficient_shared_audio")));
}

void MultiAudioInputDialogTests::qualityAnalysisNeedsResolvedAlignmentAndPreviewIsFollowUp()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());

    MultiAudioInputDialog dialog;
    dialog.setInputs({
        readyInput(createAudioFile(directory, QStringLiteral("a.wav")),
                   QStringLiteral("A")),
        readyInput(createAudioFile(directory, QStringLiteral("b.wav")),
                   QStringLiteral("B")),
    });
    QVERIFY(!dialog.model()->isAlignmentResolved());

    QPushButton *analyzeButton = button(dialog, "multiAudioAnalyzeQualityButton");
    QVERIFY(analyzeButton);
    QVERIFY(!analyzeButton->isEnabled());
    QVERIFY(!analyzeButton->toolTip().isEmpty());
    QVERIFY(label(dialog, "multiAudioSelectionSummaryLabel")->text().contains(
        QStringLiteral("음질 분석 결과가 없습니다")));

    // There is no playback in the application yet, so the preview is shown
    // as a disabled, conditional follow-up item.
    QPushButton *previewButton = button(dialog, "multiAudioPreviewButton");
    QVERIFY(previewButton);
    QVERIFY(!previewButton->isEnabled());
    QVERIFY(previewButton->text().contains(QStringLiteral("후속")));
    QVERIFY(previewButton->toolTip().contains(QStringLiteral("조건부")));
}

void MultiAudioInputDialogTests::realEngineQualityAnalysisRoundTrip()
{
    const EnginePaths enginePaths = EnginePaths::discover();
    const QString engineDirectory = enginePaths.engineDirectory();
    const QString script = enginePaths.assetPath(QStringLiteral("multimic_quality.py"));
    const QString python = enginePaths.defaultPythonProgram(
        QString::fromLocal8Bit(qgetenv("KOREAN_TRANSCRIBER_ENGINE_PYTHON")));
    if (engineDirectory.isEmpty() || script.isEmpty()
        || !QFileInfo(python).isFile()) {
        QSKIP("The engine and its Python environment are not available.");
    }

    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString generator = writeFile(
        directory,
        QStringLiteral("make_inputs.py"),
        R"PY(
import sys
import wave
from pathlib import Path

import numpy as np

engine = Path(sys.argv[1])
sys.path[:0] = [str(engine), str(engine / "tests")]
from test_multimic_align import speech_like_master

rate = 16000
master = speech_like_master(seed=77, duration_seconds=16.0)
start, length, delay = 3 * rate, 9 * rate, rate // 2
signals = {
    "reference.wav": master[start:start + length],
    "later.wav": 0.65 * master[start + delay:start + delay + length],
}
for name, samples in signals.items():
    pcm = np.rint(np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(Path(sys.argv[2]) / name), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(pcm.tobytes())
)PY");
    QVERIFY(!generator.isEmpty());
    QProcess process;
    process.start(python, {generator, engineDirectory, directory.path()});
    if (!process.waitForFinished(60000)
        || process.exitStatus() != QProcess::NormalExit
        || process.exitCode() != 0) {
        QSKIP("Test recordings could not be generated with the engine environment.");
    }

    MultiAudioInput reference = readyInput(
        directory.filePath(QStringLiteral("reference.wav")),
        QStringLiteral("Reference"));
    MultiAudioInput later = readyInput(
        directory.filePath(QStringLiteral("later.wav")),
        QStringLiteral("Later"));
    later.hasAlignmentOffset = true;
    later.alignmentOffsetMilliseconds = 500;
    later.alignmentOffsetIsManual = true;
    later.manualAlignmentCorrectionAllowed = true;
    later.confidence = 0.5;
    later.alignmentStatus = MultiAudioInput::AlignmentStatus::Manual;

    MultiAudioInputDialog dialog;
    dialog.setInputs({reference, later});
    dialog.setReferenceRow(0);
    QVERIFY(dialog.model()->isAlignmentResolved());
    dialog.setQualityAnalyzerConfiguration(python, script);
    QLabel *messageLabel = label(dialog, "multiAudioQualityMessageLabel");

    QPushButton *analyzeButton = button(dialog, "multiAudioAnalyzeQualityButton");
    analyzeButton->click();
    QTRY_VERIFY_WITH_TIMEOUT(!dialog.isQualityAnalysisRunning(), 120000);
    MultiAudioSelectionPlan plan = dialog.selectionPlan();
    QVERIFY2(!plan.isEmpty(), qPrintable(messageLabel->text()));
    QCOMPARE(plan.timelineSampleCount, qint64(9 * 16000));
    QCOMPARE(plan.manualSegmentCount(), 0);

    // Force the microphone the automatic plan did not choose for the first
    // interval; the real engine must apply it as a manual override.
    const QString automaticAlias = dialog.selectionModel()->aliasForPath(
        plan.segments.constFirst().automaticInputPath);
    const QString forcedAlias = automaticAlias == QStringLiteral("Later")
        ? QStringLiteral("Reference")
        : QStringLiteral("Later");
    dialog.findChild<QTableView *>(QStringLiteral("multiAudioSelectionTableView"))
        ->selectRow(0);
    auto *overrideCombo = dialog.findChild<QComboBox *>(
        QStringLiteral("multiAudioOverrideComboBox"));
    overrideCombo->setCurrentIndex(overrideCombo->findText(forcedAlias));
    button(dialog, "multiAudioApplyOverrideButton")->click();
    QCOMPARE(dialog.selectionPlan().manualSegmentCount(), 1);

    analyzeButton->click();
    QTRY_VERIFY_WITH_TIMEOUT(!dialog.isQualityAnalysisRunning(), 120000);
    plan = dialog.selectionPlan();
    QVERIFY2(plan.manualSegmentCount() == 1, qPrintable(messageLabel->text()));
    QCOMPARE(dialog.selectionModel()->aliasForPath(
                 plan.segments.constFirst().overrideInputPath),
             forcedAlias);
    QCOMPARE(dialog.selectionModel()->aliasForPath(
                 plan.segments.constFirst().automaticInputPath),
             automaticAlias);
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
