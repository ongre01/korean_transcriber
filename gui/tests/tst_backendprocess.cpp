#include "../backend/BackendFailure.h"
#include "../backend/BackendProcess.h"
#include "../app/EnginePaths.h"

#include <QDir>
#include <QElapsedTimer>
#include <QFile>
#include <QFileInfo>
#include <QHash>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QSignalSpy>
#include <QStandardPaths>
#include <QTemporaryDir>
#include <QtTest>

namespace {

// Every fake bridge writes protocol events with this helper.
const QByteArray fakeBridgePreamble = R"PY(
import json
import sys
import time
from pathlib import Path

here = Path(__file__).resolve().parent


def emit(event):
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def wait_for_cancel_request():
    for line in sys.stdin:
        if line.strip() == "cancel":
            (here / "cancel_received").write_text("yes", encoding="utf-8")
            return True
    return False
)PY";

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

QString writeFakeBridge(const QTemporaryDir &directory, const QByteArray &body)
{
    return writeFile(directory, QStringLiteral("fake_bridge.py"), fakeBridgePreamble + body);
}

QString findPythonProgram()
{
    const QString configured = QString::fromLocal8Bit(qgetenv("PYTHON")).trimmed();
    if (!configured.isEmpty() && QFileInfo(configured).isFile()) {
        return configured;
    }
    return QStandardPaths::findExecutable(QStringLiteral("python"));
}

TranscribeOptions fakeOptions(const QTemporaryDir &directory)
{
    TranscribeOptions options;
    options.inputFile = directory.filePath(QStringLiteral("input.wav"));
    options.modelDirectory = directory.path();
    options.skipSilence = false;
    return options;
}

QHash<QString, QByteArray> folderContents(const QString &path)
{
    QHash<QString, QByteArray> contents;
    const QDir directory(path);
    const QFileInfoList entries = directory.entryInfoList(
        QDir::AllEntries | QDir::Hidden | QDir::NoDotAndDotDot);
    for (const QFileInfo &entry : entries) {
        QByteArray bytes;
        if (entry.isFile()) {
            QFile file(entry.absoluteFilePath());
            if (file.open(QIODevice::ReadOnly)) {
                bytes = file.readAll();
            }
        } else {
            bytes = QByteArrayLiteral("<directory>");
        }
        contents.insert(entry.fileName(), bytes);
    }
    return contents;
}

} // namespace

class BackendProcessTests : public QObject
{
    Q_OBJECT

private slots:
    void initTestCase();
    void optionsBuildMutuallyExclusiveInputArguments();
    void warningIsForwardedBeforeMultiCompletion();
    void multiInputArtifactsAreStoredAndClearedForNextRun();
    void completionArtifactsMustMatchRunMode_data();
    void completionArtifactsMustMatchRunMode();
    void cancelRequestLetsTheBackendCleanUp();
    void unresponsiveBackendIsKilledAfterGracePeriod();
    void lateCompletedAfterCancelIsIgnored();
    void terminalEventAndExitStatusMismatchIsAnError_data();
    void terminalEventAndExitStatusMismatchIsAnError();
    void protocolViolationStopsTheRunningBackend();
    void userMessageAndDiagnosticsStaySeparate();
    void failureMessagesAreClassified_data();
    void failureMessagesAreClassified();
    void realBridgeCancellationKeepsInputsAndRemovesStagedFiles();

private:
    QString m_pythonProgram;
};

void BackendProcessTests::initTestCase()
{
    m_pythonProgram = findPythonProgram();
}

void BackendProcessTests::optionsBuildMutuallyExclusiveInputArguments()
{
    TranscribeOptions single;
    single.inputFile = QStringLiteral("C:/녹음 파일/회의 one.wav");
    single.modelDirectory = QStringLiteral("C:/모델 폴더/whisper");
    single.outputDirectory = QStringLiteral("C:/결과 폴더");
    single.device = BackendDevice::Npu;
    single.beams = 3;
    single.skipSilence = false;

    QString errorMessage;
    QVERIFY2(single.isValid(&errorMessage), qPrintable(errorMessage));
    QVERIFY(!single.isMultiInput());
    const QStringList singleArguments = single.toBridgeArguments();
    QCOMPARE(singleArguments.value(0), QStringLiteral("--input"));
    QCOMPARE(singleArguments.value(1), single.inputFile);
    QVERIFY(!singleArguments.contains(QStringLiteral("--multi-input-manifest")));

    TranscribeOptions multi = single;
    multi.inputFile.clear();
    multi.multiInputManifestFile = QStringLiteral("C:/작업 폴더/multi manifest.json");
    QVERIFY2(multi.isValid(&errorMessage), qPrintable(errorMessage));
    QVERIFY(multi.isMultiInput());
    const QStringList multiArguments = multi.toBridgeArguments();
    QCOMPARE(multiArguments.value(0), QStringLiteral("--multi-input-manifest"));
    QCOMPARE(multiArguments.value(1), multi.multiInputManifestFile);
    QVERIFY(!multiArguments.contains(QStringLiteral("--input")));
    QCOMPARE(multiArguments.mid(2), singleArguments.mid(2));

    TranscribeOptions missingInput = single;
    missingInput.inputFile.clear();
    QVERIFY(!missingInput.isValid(&errorMessage));
    QCOMPARE(errorMessage,
             QStringLiteral("exactly one of inputFile or multiInputManifestFile must be set"));

    TranscribeOptions ambiguousInput = single;
    ambiguousInput.multiInputManifestFile = QStringLiteral("C:/manifest.json");
    QVERIFY(!ambiguousInput.isValid(&errorMessage));
    QCOMPARE(errorMessage,
             QStringLiteral("exactly one of inputFile or multiInputManifestFile must be set"));
}

void BackendProcessTests::warningIsForwardedBeforeMultiCompletion()
{
    if (m_pythonProgram.isEmpty()) {
        QSKIP("No Python executable was found via PYTHON or PATH (python).");
    }
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString script = writeFakeBridge(directory, R"PY(
emit({"type": "warning", "message": "Low alignment confidence."})
emit({"type": "state", "value": "saving_result"})
emit({"type": "completed", "text_file": "C:/result.txt", "srt_file": "C:/result.srt",
      "merged_audio_file": "C:/merged.wav", "alignment_file": "C:/alignment.json",
      "selection_file": "C:/selection.json"})
)PY");
    QVERIFY(!script.isEmpty());

    BackendProcess backend;
    backend.setPythonProgram(m_pythonProgram);
    backend.setBridgeScript(script);
    QSignalSpy warnings(&backend, &BackendProcess::warningOccurred);
    QSignalSpy completed(&backend, &BackendProcess::completed);
    QSignalSpy errors(&backend, &BackendProcess::errorOccurred);
    QSignalSpy stopped(&backend, &BackendProcess::stopped);

    TranscribeOptions options = fakeOptions(directory);
    options.inputFile.clear();
    options.multiInputManifestFile = directory.filePath(QStringLiteral("manifest.json"));
    backend.start(options);
    QTRY_COMPARE_WITH_TIMEOUT(stopped.count(), 1, 20000);

    QCOMPARE(warnings.count(), 1);
    QCOMPARE(warnings.at(0).at(0).toString(), QStringLiteral("Low alignment confidence."));
    QCOMPARE(completed.count(), 1);
    QCOMPARE(errors.count(), 0);
}

void BackendProcessTests::multiInputArtifactsAreStoredAndClearedForNextRun()
{
    if (m_pythonProgram.isEmpty()) {
        QSKIP("No Python executable was found via PYTHON or PATH (python).");
    }
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString script = writeFakeBridge(directory, R"PY(
if "--multi-input-manifest" in sys.argv:
    emit({"type": "state", "value": "saving_result"})
    emit({"type": "completed", "text_file": "C:/result.txt", "srt_file": "C:/result.srt",
          "merged_audio_file": "C:/merged.wav", "alignment_file": "C:/alignment.json",
          "selection_file": "C:/selection.json"})
else:
    emit({"type": "state", "value": "transcribing"})
    if wait_for_cancel_request():
        emit({"type": "error", "message": "Transcription was cancelled."})
        sys.exit(130)
)PY");
    QVERIFY(!script.isEmpty());

    BackendProcess backend;
    backend.setPythonProgram(m_pythonProgram);
    backend.setBridgeScript(script);
    backend.setCancellationGracePeriod(20000);
    QSignalSpy states(&backend, &BackendProcess::stateChanged);
    QSignalSpy completed(&backend, &BackendProcess::completed);
    QSignalSpy cancelled(&backend, &BackendProcess::cancelled);
    QSignalSpy errors(&backend, &BackendProcess::errorOccurred);
    QSignalSpy stopped(&backend, &BackendProcess::stopped);

    TranscribeOptions multi = fakeOptions(directory);
    multi.inputFile.clear();
    multi.multiInputManifestFile = directory.filePath(QStringLiteral("manifest.json"));
    backend.start(multi);
    QTRY_COMPARE_WITH_TIMEOUT(stopped.count(), 1, 20000);

    QCOMPARE(completed.count(), 1);
    QCOMPARE(errors.count(), 0);
    QCOMPARE(backend.textResultFile(), QStringLiteral("C:/result.txt"));
    QCOMPARE(backend.srtResultFile(), QStringLiteral("C:/result.srt"));
    QCOMPARE(backend.mergedAudioResultFile(), QStringLiteral("C:/merged.wav"));
    QCOMPARE(backend.alignmentResultFile(), QStringLiteral("C:/alignment.json"));
    QCOMPARE(backend.selectionResultFile(), QStringLiteral("C:/selection.json"));

    backend.start(fakeOptions(directory));
    QTRY_COMPARE_WITH_TIMEOUT(states.count(), 2, 20000);
    QCOMPARE(states.constLast().at(0).toString(), QStringLiteral("transcribing"));
    QVERIFY(backend.textResultFile().isEmpty());
    QVERIFY(backend.srtResultFile().isEmpty());
    QVERIFY(backend.mergedAudioResultFile().isEmpty());
    QVERIFY(backend.alignmentResultFile().isEmpty());
    QVERIFY(backend.selectionResultFile().isEmpty());

    backend.cancel();
    QTRY_COMPARE_WITH_TIMEOUT(stopped.count(), 2, 25000);
    QCOMPARE(completed.count(), 1);
    QCOMPARE(cancelled.count(), 1);
    QCOMPARE(errors.count(), 0);
}

void BackendProcessTests::completionArtifactsMustMatchRunMode_data()
{
    QTest::addColumn<bool>("multiInput");
    QTest::addColumn<QByteArray>("completedEvent");
    QTest::addColumn<QString>("expectedError");

    QTest::newRow("multi run without artifacts")
        << true
        << QByteArrayLiteral(
               "{\"type\":\"completed\",\"text_file\":\"C:/r.txt\","
               "\"srt_file\":\"C:/r.srt\"}")
        << QStringLiteral(
               "Backend protocol error: multi-input run completed without multi-input artifacts");
    QTest::newRow("single run with multi artifacts")
        << false
        << QByteArrayLiteral(
               "{\"type\":\"completed\",\"text_file\":\"C:/r.txt\","
               "\"srt_file\":\"C:/r.srt\",\"merged_audio_file\":\"C:/merged.wav\","
               "\"alignment_file\":\"C:/alignment.json\","
               "\"selection_file\":\"C:/selection.json\"}")
        << QStringLiteral(
               "Backend protocol error: single-input run completed with multi-input artifacts");
}

void BackendProcessTests::completionArtifactsMustMatchRunMode()
{
    if (m_pythonProgram.isEmpty()) {
        QSKIP("No Python executable was found via PYTHON or PATH (python).");
    }
    QFETCH(bool, multiInput);
    QFETCH(QByteArray, completedEvent);
    QFETCH(QString, expectedError);
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString script = writeFakeBridge(
        directory,
        QByteArrayLiteral("emit({\"type\": \"state\", \"value\": \"saving_result\"})\n")
            + QByteArrayLiteral("emit(") + completedEvent + QByteArrayLiteral(")\n"));
    QVERIFY(!script.isEmpty());

    BackendProcess backend;
    backend.setPythonProgram(m_pythonProgram);
    backend.setBridgeScript(script);
    QSignalSpy completed(&backend, &BackendProcess::completed);
    QSignalSpy errors(&backend, &BackendProcess::errorOccurred);
    QSignalSpy stopped(&backend, &BackendProcess::stopped);

    TranscribeOptions options = fakeOptions(directory);
    if (multiInput) {
        options.inputFile.clear();
        options.multiInputManifestFile = directory.filePath(QStringLiteral("manifest.json"));
    }
    backend.start(options);
    QTRY_COMPARE_WITH_TIMEOUT(stopped.count(), 1, 20000);

    QCOMPARE(completed.count(), 0);
    QCOMPARE(errors.count(), 1);
    QCOMPARE(errors.at(0).at(0).toString(), expectedError);
    QVERIFY(backend.textResultFile().isEmpty());
    QVERIFY(backend.mergedAudioResultFile().isEmpty());
}

void BackendProcessTests::cancelRequestLetsTheBackendCleanUp()
{
    if (m_pythonProgram.isEmpty()) {
        QSKIP("No Python executable was found via PYTHON or PATH (python).");
    }
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString script = writeFakeBridge(directory, R"PY(
staged = here / "merged_16k_mono.wav"
staged.write_bytes(b"staged audio")
emit({"type": "state", "value": "preparing"})
emit({"type": "state", "value": "rendering_merged_audio"})
emit({"type": "progress", "value": None})
if wait_for_cancel_request():
    staged.unlink()
    emit({"type": "error", "message": "Transcription was cancelled."})
    sys.exit(130)
)PY");
    QVERIFY(!script.isEmpty());

    BackendProcess backend;
    backend.setPythonProgram(m_pythonProgram);
    backend.setBridgeScript(script);
    backend.setCancellationGracePeriod(20000);
    QSignalSpy states(&backend, &BackendProcess::stateChanged);
    QSignalSpy cancelled(&backend, &BackendProcess::cancelled);
    QSignalSpy completed(&backend, &BackendProcess::completed);
    QSignalSpy errors(&backend, &BackendProcess::errorOccurred);
    QSignalSpy stopped(&backend, &BackendProcess::stopped);

    backend.start(fakeOptions(directory));
    QTRY_COMPARE_WITH_TIMEOUT(states.count(), 2, 20000);
    QCOMPARE(states.at(1).at(0).toString(), QStringLiteral("rendering_merged_audio"));

    QElapsedTimer timer;
    timer.start();
    backend.cancel();
    QTRY_COMPARE_WITH_TIMEOUT(stopped.count(), 1, 15000);

    // The backend exited on its own well before the grace period ended.
    QVERIFY2(timer.elapsed() < 15000, qPrintable(QString::number(timer.elapsed())));
    QCOMPARE(cancelled.count(), 1);
    QCOMPARE(completed.count(), 0);
    QCOMPARE(errors.count(), 0);
    QVERIFY(QFileInfo::exists(directory.filePath(QStringLiteral("cancel_received"))));
    QVERIFY(!QFileInfo::exists(directory.filePath(QStringLiteral("merged_16k_mono.wav"))));
    QVERIFY(!backend.isRunning());
}

void BackendProcessTests::unresponsiveBackendIsKilledAfterGracePeriod()
{
    if (m_pythonProgram.isEmpty()) {
        QSKIP("No Python executable was found via PYTHON or PATH (python).");
    }
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString script = writeFakeBridge(directory, R"PY(
emit({"type": "state", "value": "aligning_audio"})
time.sleep(120)
)PY");
    QVERIFY(!script.isEmpty());

    BackendProcess backend;
    backend.setPythonProgram(m_pythonProgram);
    backend.setBridgeScript(script);
    backend.setCancellationGracePeriod(300);
    QSignalSpy states(&backend, &BackendProcess::stateChanged);
    QSignalSpy cancelled(&backend, &BackendProcess::cancelled);
    QSignalSpy errors(&backend, &BackendProcess::errorOccurred);
    QSignalSpy stopped(&backend, &BackendProcess::stopped);

    backend.start(fakeOptions(directory));
    QTRY_COMPARE_WITH_TIMEOUT(states.count(), 1, 20000);

    QElapsedTimer timer;
    timer.start();
    backend.cancel();
    backend.cancel();
    QTRY_COMPARE_WITH_TIMEOUT(stopped.count(), 1, 10000);

    QVERIFY(timer.elapsed() >= 250);
    QCOMPARE(cancelled.count(), 1);
    QCOMPARE(errors.count(), 0);
    QVERIFY(!backend.isRunning());
}

void BackendProcessTests::lateCompletedAfterCancelIsIgnored()
{
    if (m_pythonProgram.isEmpty()) {
        QSKIP("No Python executable was found via PYTHON or PATH (python).");
    }
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString script = writeFakeBridge(directory, R"PY(
emit({"type": "state", "value": "transcribing"})
wait_for_cancel_request()
emit({"type": "state", "value": "saving_result"})
emit({"type": "progress", "value": 100})
emit({"type": "completed", "text_file": "C:/late.txt", "srt_file": "C:/late.srt"})
)PY");
    QVERIFY(!script.isEmpty());

    BackendProcess backend;
    backend.setPythonProgram(m_pythonProgram);
    backend.setBridgeScript(script);
    QSignalSpy states(&backend, &BackendProcess::stateChanged);
    QSignalSpy cancelled(&backend, &BackendProcess::cancelled);
    QSignalSpy completed(&backend, &BackendProcess::completed);
    QSignalSpy stopped(&backend, &BackendProcess::stopped);

    backend.start(fakeOptions(directory));
    QTRY_COMPARE_WITH_TIMEOUT(states.count(), 1, 20000);
    backend.cancel();
    QTRY_COMPARE_WITH_TIMEOUT(stopped.count(), 1, 15000);

    QCOMPARE(cancelled.count(), 1);
    QCOMPARE(completed.count(), 0);
    QCOMPARE(states.count(), 1);
    QVERIFY(backend.textResultFile().isEmpty());
}

void BackendProcessTests::terminalEventAndExitStatusMismatchIsAnError_data()
{
    QTest::addColumn<QByteArray>("body");
    QTest::addColumn<QString>("expectedError");

    QTest::newRow("completed with non-zero exit")
        << QByteArray(R"PY(
emit({"type": "state", "value": "saving_result"})
emit({"type": "completed", "text_file": "C:/r.txt", "srt_file": "C:/r.srt"})
sys.exit(1)
)PY")
        << QStringLiteral("Backend protocol error: completed event requires exit code 0");
    QTest::newRow("error with zero exit")
        << QByteArray(R"PY(
emit({"type": "state", "value": "aligning_audio"})
emit({"type": "error", "message": "Time alignment is unresolved for B."})
sys.exit(0)
)PY")
        << QStringLiteral("Backend protocol error: error event requires a non-zero exit code");
    QTest::newRow("zero exit without terminal event")
        << QByteArray(R"PY(
emit({"type": "state", "value": "rendering_merged_audio"})
sys.exit(0)
)PY")
        << QStringLiteral(
               "Backend protocol error: stream ended without completed or error event");
    QTest::newRow("cancel exit code without a user cancellation")
        << QByteArray(R"PY(
emit({"type": "state", "value": "transcribing"})
sys.exit(130)
)PY")
        << QStringLiteral(
               "Backend protocol error: stream ended without completed or error event");
    QTest::newRow("event after completed")
        << QByteArray(R"PY(
emit({"type": "state", "value": "saving_result"})
emit({"type": "completed", "text_file": "C:/r.txt", "srt_file": "C:/r.srt"})
emit({"type": "warning", "message": "late"})
)PY")
        << QStringLiteral("Backend protocol error: event received after terminal event");
    QTest::newRow("multi-input completed missing an artifact")
        << QByteArray(R"PY(
emit({"type": "state", "value": "saving_result"})
emit({"type": "completed", "text_file": "C:/r.txt", "srt_file": "C:/r.srt",
      "merged_audio_file": "C:/merged_16k_mono.wav"})
)PY")
        << QString();
}

void BackendProcessTests::terminalEventAndExitStatusMismatchIsAnError()
{
    if (m_pythonProgram.isEmpty()) {
        QSKIP("No Python executable was found via PYTHON or PATH (python).");
    }
    QFETCH(QByteArray, body);
    QFETCH(QString, expectedError);
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString script = writeFakeBridge(directory, body);
    QVERIFY(!script.isEmpty());

    BackendProcess backend;
    backend.setPythonProgram(m_pythonProgram);
    backend.setBridgeScript(script);
    QSignalSpy completed(&backend, &BackendProcess::completed);
    QSignalSpy errors(&backend, &BackendProcess::errorOccurred);
    QSignalSpy stopped(&backend, &BackendProcess::stopped);

    backend.start(fakeOptions(directory));
    QTRY_COMPARE_WITH_TIMEOUT(stopped.count(), 1, 20000);

    QCOMPARE(completed.count(), 0);
    QCOMPARE(errors.count(), 1);
    const QString error = errors.at(0).at(0).toString();
    if (expectedError.isEmpty()) {
        QVERIFY2(error.startsWith(QStringLiteral("Backend protocol error: ")), qPrintable(error));
    } else {
        QCOMPARE(error, expectedError);
    }
    QVERIFY(!backend.isRunning());
}

void BackendProcessTests::protocolViolationStopsTheRunningBackend()
{
    if (m_pythonProgram.isEmpty()) {
        QSKIP("No Python executable was found via PYTHON or PATH (python).");
    }
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    // The backend keeps running after its invalid output, as a bridge that
    // broke the contract could; it must not keep the GUI waiting.
    const QString script = writeFakeBridge(directory, R"PY(
emit({"type": "state", "value": "assessing_channels"})
sys.stdout.write("channel 2 looks better\n")
sys.stdout.flush()
wait_for_cancel_request()
time.sleep(120)
)PY");
    QVERIFY(!script.isEmpty());

    BackendProcess backend;
    backend.setPythonProgram(m_pythonProgram);
    backend.setBridgeScript(script);
    backend.setCancellationGracePeriod(500);
    QSignalSpy errors(&backend, &BackendProcess::errorOccurred);
    QSignalSpy cancelled(&backend, &BackendProcess::cancelled);
    QSignalSpy stopped(&backend, &BackendProcess::stopped);

    backend.start(fakeOptions(directory));
    QTRY_COMPARE_WITH_TIMEOUT(errors.count(), 1, 20000);
    QVERIFY(errors.at(0).at(0).toString().startsWith(QStringLiteral("Backend protocol error: ")));
    QTRY_COMPARE_WITH_TIMEOUT(stopped.count(), 1, 10000);

    QCOMPARE(errors.count(), 1);
    QCOMPARE(cancelled.count(), 0);
    QTRY_VERIFY_WITH_TIMEOUT(
        QFileInfo::exists(directory.filePath(QStringLiteral("cancel_received"))), 5000);
    QVERIFY(!backend.isRunning());
}

void BackendProcessTests::userMessageAndDiagnosticsStaySeparate()
{
    if (m_pythonProgram.isEmpty()) {
        QSKIP("No Python executable was found via PYTHON or PATH (python).");
    }
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString script = writeFakeBridge(directory, R"PY(
for state in ("preparing", "decoding_audio", "aligning_audio"):
    emit({"type": "state", "value": state})
    emit({"type": "progress", "value": None})
sys.stderr.write("Unresolved multi-input alignment:\n[{\"input_index\": 1}]\n")
sys.stderr.write("Traceback (most recent call last):\n  File \"pipeline.py\"\n")
sys.stderr.flush()
emit({"type": "error", "message": "Time alignment is unresolved for B (no_reliable_peak). "
      "Review the alignment and enter a manual offset for these inputs before transcribing."})
sys.exit(1)
)PY");
    QVERIFY(!script.isEmpty());

    BackendProcess backend;
    backend.setPythonProgram(m_pythonProgram);
    backend.setBridgeScript(script);
    QSignalSpy errors(&backend, &BackendProcess::errorOccurred);
    QSignalSpy stopped(&backend, &BackendProcess::stopped);

    backend.start(fakeOptions(directory));
    QTRY_COMPARE_WITH_TIMEOUT(stopped.count(), 1, 20000);

    QCOMPARE(errors.count(), 1);
    const QString message = errors.at(0).at(0).toString();
    QVERIFY(message.startsWith(QStringLiteral("Time alignment is unresolved for B")));
    QVERIFY(!message.contains(QStringLiteral("Traceback")));
    QCOMPARE(BackendFailure::classify(message), BackendFailureKind::Synchronization);
    const QByteArray diagnostics = backend.standardErrorOutput();
    QVERIFY(diagnostics.contains("Traceback (most recent call last):"));
    QVERIFY(diagnostics.contains("\"input_index\": 1"));
}

void BackendProcessTests::failureMessagesAreClassified_data()
{
    QTest::addColumn<QString>("message");
    QTest::addColumn<BackendFailureKind>("kind");

    const auto row = [](const char *name, const char *message, BackendFailureKind kind) {
        QTest::newRow(name) << QString::fromUtf8(message) << kind;
    };
    row("unresolved alignment",
        "Time alignment is unresolved for 발표자 근처 (no_reliable_peak). Review ...",
        BackendFailureKind::Synchronization);
    row("alignment stage", "Multi-input time alignment failed: boom",
        BackendFailureKind::Synchronization);
    row("input preparation",
        "Multi-input audio could not be prepared. 발표자 근처: Invalid data found",
        BackendFailureKind::Decode);
    row("multi decoding stage", "Multi-input audio decoding failed: boom",
        BackendFailureKind::Decode);
    row("single decoding", "Audio decoding failed: Invalid data", BackendFailureKind::Decode);
    row("speech detection", "Multi-input speech detection failed: boom",
        BackendFailureKind::Conversion);
    row("quality", "Channel quality analysis failed: boom", BackendFailureKind::Conversion);
    row("selection", "Channel selection failed: boom", BackendFailureKind::Conversion);
    row("rendering", "Merged audio could not be created: [Errno 28] No space left on device",
        BackendFailureKind::Conversion);
    // Mentions decoding, but the merged audio was produced by the conversion.
    row("merged decode", "Merged audio could not be decoded: boom",
        BackendFailureKind::Conversion);
    row("merged length",
        "Merged audio does not match the reference recording timeline: expected 1, decoded 2.",
        BackendFailureKind::Conversion);
    row("model directory", "Model directory not found: C:/models", BackendFailureKind::Model);
    row("model load on npu", "Whisper model could not be loaded: NPU compile failed",
        BackendFailureKind::Model);
    row("generation config", "Whisper generation configuration failed: x",
        BackendFailureKind::Model);
    row("result save", "Result files could not be saved: [WinError 32] in use",
        BackendFailureKind::ResultSave);
    row("python module",
        "Required Python module is not installed: av. Current interpreter: python",
        BackendFailureKind::PythonEnvironment);
    row("start failure", "Failed to start backend process: not found",
        BackendFailureKind::PythonEnvironment);
    row("single input missing", "Input file not found: C:/a.wav",
        BackendFailureKind::InputFile);
    row("manifest input missing",
        "Invalid bridge arguments: multi-input manifest inputs[1].path does not identify "
        "an existing file: C:/b.wav",
        BackendFailureKind::InputFile);
    row("diarization", "Speaker diarization failed: segmentation model missing",
        BackendFailureKind::Diarization);
    row("device", "OpenVINO device selection failed: NPU not found",
        BackendFailureKind::Device);
    row("transcription on npu", "Transcription failed: NPU device lost",
        BackendFailureKind::Device);
    row("protocol", "Backend protocol error: stream ended without completed or error event",
        BackendFailureKind::Other);
    row("external interrupt", "Transcription was cancelled.", BackendFailureKind::Other);
}

void BackendProcessTests::failureMessagesAreClassified()
{
    QFETCH(QString, message);
    QFETCH(BackendFailureKind, kind);

    QCOMPARE(BackendFailure::classify(message), kind);
}

void BackendProcessTests::realBridgeCancellationKeepsInputsAndRemovesStagedFiles()
{
    const EnginePaths enginePaths = EnginePaths::discover();
    const QString engineDirectory = enginePaths.engineDirectory();
    const QString python = enginePaths.defaultPythonProgram(
        QString::fromLocal8Bit(qgetenv("KOREAN_TRANSCRIBER_ENGINE_PYTHON")));
    if (engineDirectory.isEmpty() || !QFileInfo(python).isFile()) {
        QSKIP("The engine and its Python environment are not available.");
    }

    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString recordings = directory.filePath(QStringLiteral("recordings"));
    const QString manifest = directory.filePath(QStringLiteral("manifest.json"));
    const QString generator = writeFile(directory, QStringLiteral("make_inputs.py"), R"PY(
import json
import sys
from pathlib import Path

import numpy as np

engine, recordings, manifest = (Path(value) for value in sys.argv[1:])
sys.path[:0] = [str(engine), str(engine / "tests")]
from test_multimic_align import speech_like_master
from test_multimic_quality_cli import write_source_wav

rate = 16000
master = speech_like_master(seed=77, duration_seconds=16.0)
start, length, delay = 3 * rate, 9 * rate, rate // 2
recordings.mkdir()
write_source_wav(recordings / "reference.wav", master[start:start + length])
write_source_wav(
    recordings / "later.wav",
    (0.65 * master[start + delay:start + delay + length]).astype(np.float32),
)
manifest.write_text(json.dumps({
    "version": 1,
    "inputs": [
        {"path": str(recordings / "reference.wav"), "label": "Reference"},
        {"path": str(recordings / "later.wav"), "label": "Later"},
    ],
    "reference_index": 0,
    "alignment": {"mode": "auto", "manual_offsets_ms": {}},
    "selection": {"mode": "automatic", "window_seconds": 3.0, "hop_seconds": 1.0},
}), encoding="utf-8")
)PY");
    QVERIFY(!generator.isEmpty());
    QProcess process;
    process.start(python, {generator, engineDirectory, recordings, manifest});
    if (!process.waitForFinished(120000) || process.exitStatus() != QProcess::NormalExit
        || process.exitCode() != 0) {
        QSKIP(qPrintable(QStringLiteral("Test recordings could not be generated: %1")
                             .arg(QString::fromLocal8Bit(process.readAllStandardError()))));
    }
    const QHash<QString, QByteArray> recordingsBefore = folderContents(recordings);
    QCOMPARE(recordingsBefore.size(), 2);

    // The real bridge entry point in multi-input mode; only Whisper is
    // replaced, by inference that runs until the job is cancelled.
    const QString driver = writeFile(directory, QStringLiteral("bridge_driver.py"), R"PY(
import json
import sys
import time
from pathlib import Path

settings = json.loads(Path(__file__).with_name("driver.json").read_text(encoding="utf-8"))
engine = Path(settings["engine"])
sys.path[:0] = [str(engine), str(engine / "tests")]

import backend_bridge
from test_backend_bridge_multi_pipeline import MergedAudioEngine


class BlockingEngine(MergedAudioEngine):
    def transcribe_windows(self, *_arguments, **_keyword_arguments):
        while True:
            time.sleep(0.02)


raise SystemExit(backend_bridge.run_as_script(
    ["--multi-input-manifest", settings["manifest"],
     "--model-dir", settings["model_dir"], "--device", "CPU"],
    engine_module=BlockingEngine(),
))
)PY");
    QVERIFY(!driver.isEmpty());
    const QJsonObject settings {
        {QStringLiteral("engine"), engineDirectory},
        {QStringLiteral("manifest"), manifest},
        {QStringLiteral("model_dir"), directory.path()},
    };
    QVERIFY(!writeFile(directory, QStringLiteral("driver.json"),
                       QJsonDocument(settings).toJson()).isEmpty());

    BackendProcess backend;
    backend.setPythonProgram(python);
    backend.setBridgeScript(driver);
    backend.setCancellationGracePeriod(20000);
    QSignalSpy states(&backend, &BackendProcess::stateChanged);
    QSignalSpy cancelled(&backend, &BackendProcess::cancelled);
    QSignalSpy errors(&backend, &BackendProcess::errorOccurred);
    QSignalSpy stopped(&backend, &BackendProcess::stopped);

    TranscribeOptions options = fakeOptions(directory);
    options.inputFile.clear();
    options.multiInputManifestFile = manifest;
    backend.start(options);
    QTRY_VERIFY_WITH_TIMEOUT(
        !states.isEmpty()
            && states.constLast().at(0).toString() == QStringLiteral("transcribing"),
        120000);
    QVERIFY2(errors.isEmpty(), errors.isEmpty() ? "" : qPrintable(errors.at(0).at(0).toString()));
    QStringList stages;
    for (const QList<QVariant> &arguments : states) {
        stages.append(arguments.at(0).toString());
    }
    QVERIFY2(stages.contains(QStringLiteral("rendering_merged_audio")),
             qPrintable(stages.join(QLatin1Char(','))));

    QElapsedTimer timer;
    timer.start();
    backend.cancel();
    QTRY_COMPARE_WITH_TIMEOUT(stopped.count(), 1, 25000);

    // The bridge cleaned up and exited after the stdin request, well before
    // the 3 s the GUI would wait before kill().
    QVERIFY2(timer.elapsed() < 3000, qPrintable(QString::number(timer.elapsed())));
    QCOMPARE(cancelled.count(), 1);
    QCOMPARE(errors.count(), 0);
    QVERIFY(backend.standardErrorOutput().contains("KeyboardInterrupt"));
    QCOMPARE(folderContents(recordings), recordingsBefore);
}

QTEST_GUILESS_MAIN(BackendProcessTests)

#include "tst_backendprocess.moc"
