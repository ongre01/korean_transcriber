#include "AudioFileInfo.h"

#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>
#include <QProcess>
#include <QSaveFile>
#include <QStandardPaths>
#include <QStringList>

#include <cmath>
#include <limits>

namespace {
QString defaultPythonProgram()
{
    const QString configured = QString::fromLocal8Bit(qgetenv("PYTHON")).trimmed();
    if (!configured.isEmpty()) {
        return configured;
    }

    QStringList candidates;
    candidates << QDir::current().absoluteFilePath(
        QStringLiteral("engine/.venv/Scripts/python.exe"));

    QDir applicationDirectory(QCoreApplication::applicationDirPath());
    for (int depth = 0; depth < 6; ++depth) {
        candidates << applicationDirectory.absoluteFilePath(
            QStringLiteral("engine/.venv/Scripts/python.exe"));
        if (!applicationDirectory.cdUp()) {
            break;
        }
    }

    for (const QString &candidate : candidates) {
        const QFileInfo python(candidate);
        if (python.isFile()) {
            const QString canonicalPath = python.canonicalFilePath();
            return canonicalPath.isEmpty() ? python.absoluteFilePath() : canonicalPath;
        }
    }

    return QStringLiteral("python");
}

QString defaultProbeScript()
{
    QStringList candidates;
    candidates << QDir::current().absoluteFilePath(QStringLiteral("engine/audio_metadata.py"));

    QDir applicationDirectory(QCoreApplication::applicationDirPath());
    for (int depth = 0; depth < 6; ++depth) {
        candidates << applicationDirectory.absoluteFilePath(QStringLiteral("engine/audio_metadata.py"));
        if (!applicationDirectory.cdUp()) {
            break;
        }
    }

    for (const QString &candidate : candidates) {
        const QFileInfo script(candidate);
        if (script.isFile()) {
            const QString canonicalPath = script.canonicalFilePath();
            return canonicalPath.isEmpty() ? script.absoluteFilePath() : canonicalPath;
        }
    }

    QFile bundledScript(QStringLiteral(":/python/audio_metadata.py"));
    if (bundledScript.open(QIODevice::ReadOnly)) {
        const QByteArray contents = bundledScript.readAll();
        const QString digest = QString::fromLatin1(
            QCryptographicHash::hash(contents, QCryptographicHash::Sha256).toHex().left(16));
        QDir cacheDirectory(QStandardPaths::writableLocation(QStandardPaths::CacheLocation));
        if (cacheDirectory.mkpath(QStringLiteral("python"))
            && cacheDirectory.cd(QStringLiteral("python"))) {
            const QString extractedPath = cacheDirectory.absoluteFilePath(
                QStringLiteral("audio_metadata_%1.py").arg(digest));
            if (!QFileInfo::exists(extractedPath)) {
                QSaveFile output(extractedPath);
                if (!output.open(QIODevice::WriteOnly)
                    || output.write(contents) != contents.size()
                    || !output.commit()) {
                    return QStringLiteral("audio_metadata.py");
                }
            }
            return extractedPath;
        }
    }

    return QStringLiteral("audio_metadata.py");
}

QString normalizedExistingFile(const QString &filePath)
{
    const QFileInfo file(filePath.trimmed());
    if (!file.isFile()) {
        return QString();
    }
    const QString canonicalPath = file.canonicalFilePath();
    return canonicalPath.isEmpty() ? file.absoluteFilePath() : canonicalPath;
}
} // namespace

AudioFileInfo::AudioFileInfo(QObject *parent)
    : QObject(parent)
    , m_pythonProgram(defaultPythonProgram())
    , m_probeScript(defaultProbeScript())
{
    qRegisterMetaType<AudioFileMetadata>();
}

AudioFileInfo::~AudioFileInfo()
{
    ++m_generation;
    const auto processes = m_requests.keys();
    for (QProcess *process : processes) {
        if (process && process->state() != QProcess::NotRunning) {
            QObject::disconnect(process, nullptr, this, nullptr);
            process->kill();
            process->waitForFinished(100);
        }
    }
}

void AudioFileInfo::setPythonProgram(const QString &program)
{
    m_pythonProgram = program.trimmed();
}

QString AudioFileInfo::pythonProgram() const
{
    return m_pythonProgram;
}

void AudioFileInfo::setProbeScript(const QString &scriptPath)
{
    m_probeScript = scriptPath.trimmed();
}

QString AudioFileInfo::probeScript() const
{
    return m_probeScript;
}

bool AudioFileInfo::isInspecting() const
{
    return m_currentProcess && m_currentProcess->state() != QProcess::NotRunning;
}

QString AudioFileInfo::fileDialogFilter()
{
    return tr("오디오/비디오 파일 (*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.mp4);;모든 파일 (*.*)");
}

bool AudioFileInfo::isSupportedFile(const QString &filePath)
{
    static const QStringList extensions {
        QStringLiteral("wav"),
        QStringLiteral("mp3"),
        QStringLiteral("m4a"),
        QStringLiteral("aac"),
        QStringLiteral("flac"),
        QStringLiteral("ogg"),
        QStringLiteral("mp4")
    };
    return extensions.contains(QFileInfo(filePath).suffix(), Qt::CaseInsensitive);
}

void AudioFileInfo::inspect(const QString &filePath)
{
    const quint64 generation = ++m_generation;
    stopCurrentProcess();

    const QString normalizedPath = normalizedExistingFile(filePath);
    const QString reportedPath = normalizedPath.isEmpty()
        ? QFileInfo(filePath.trimmed()).absoluteFilePath()
        : normalizedPath;
    emit inspectionStarted(reportedPath);

    if (normalizedPath.isEmpty()) {
        reportFailure(generation, reportedPath, tr("선택한 파일을 찾을 수 없습니다."));
        return;
    }
    if (!isSupportedFile(normalizedPath)) {
        reportFailure(generation, normalizedPath, tr("지원하지 않는 파일 형식입니다."));
        return;
    }
    if (m_pythonProgram.isEmpty()) {
        reportFailure(generation, normalizedPath, tr("Python 실행 경로가 설정되지 않았습니다."));
        return;
    }

    const QFileInfo script(m_probeScript);
    if (!script.isFile()) {
        reportFailure(generation, normalizedPath, tr("오디오 메타데이터 도구를 찾을 수 없습니다."));
        return;
    }

    auto *process = new QProcess(this);
    process->setProcessChannelMode(QProcess::SeparateChannels);
    process->setProgram(m_pythonProgram);
    process->setArguments({script.absoluteFilePath(), QStringLiteral("--input"), normalizedPath});
    process->setWorkingDirectory(script.absolutePath());

    Request request;
    request.generation = generation;
    request.filePath = normalizedPath;
    m_requests.insert(process, request);
    m_currentProcess = process;

    connect(process, &QProcess::readyReadStandardOutput, this, [this, process]() {
        const auto iterator = m_requests.find(process);
        if (iterator != m_requests.end()) {
            iterator->standardOutput.append(process->readAllStandardOutput());
        }
    });
    connect(process, &QProcess::readyReadStandardError, this, [this, process]() {
        const auto iterator = m_requests.find(process);
        if (iterator != m_requests.end()) {
            iterator->standardError.append(process->readAllStandardError());
        }
    });
    connect(process, &QProcess::errorOccurred, this,
            [this, process](QProcess::ProcessError error) {
        if (error == QProcess::FailedToStart) {
            processFailedToStart(process);
        }
    });
    connect(process,
            qOverload<int, QProcess::ExitStatus>(&QProcess::finished),
            this,
            [this, process](int exitCode, QProcess::ExitStatus exitStatus) {
        finishProcess(process, exitCode, static_cast<int>(exitStatus));
    });
    process->start();
}

void AudioFileInfo::cancel()
{
    ++m_generation;
    stopCurrentProcess();
}

void AudioFileInfo::stopCurrentProcess()
{
    if (m_currentProcess && m_currentProcess->state() != QProcess::NotRunning) {
        m_currentProcess->kill();
    }
    m_currentProcess.clear();
}

void AudioFileInfo::finishProcess(QProcess *process, int exitCode, int exitStatus)
{
    auto iterator = m_requests.find(process);
    if (iterator == m_requests.end()) {
        process->deleteLater();
        return;
    }

    iterator->standardOutput.append(process->readAllStandardOutput());
    iterator->standardError.append(process->readAllStandardError());
    const Request request = iterator.value();
    m_requests.erase(iterator);
    if (m_currentProcess == process) {
        m_currentProcess.clear();
    }
    process->deleteLater();

    if (request.generation != m_generation) {
        return;
    }

    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(
        request.standardOutput.trimmed(), &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        reportFailure(request.generation, request.filePath,
                      tr("오디오 메타데이터 응답을 해석할 수 없습니다."));
        return;
    }

    const QJsonObject payload = document.object();
    if (!payload.value(QStringLiteral("ok")).isBool()) {
        reportFailure(request.generation, request.filePath,
                      tr("오디오 메타데이터 응답이 올바르지 않습니다."));
        return;
    }

    if (!payload.value(QStringLiteral("ok")).toBool()) {
        const QString detail = payload.value(QStringLiteral("error")).toString().trimmed();
        reportFailure(request.generation, request.filePath,
                      detail.isEmpty() ? tr("오디오 파일을 읽을 수 없습니다.") : detail);
        return;
    }

    if (exitStatus != static_cast<int>(QProcess::NormalExit) || exitCode != 0) {
        reportFailure(request.generation, request.filePath,
                      tr("오디오 메타데이터 조회 프로세스가 비정상 종료되었습니다."));
        return;
    }

    const QJsonValue durationValue = payload.value(QStringLiteral("duration_seconds"));
    if (!durationValue.isDouble()) {
        reportFailure(request.generation, request.filePath,
                      tr("오디오 재생 시간을 확인할 수 없습니다."));
        return;
    }

    const double durationSeconds = durationValue.toDouble();
    const double maximumSeconds = static_cast<double>(std::numeric_limits<qint64>::max()) / 1000.0;
    if (!std::isfinite(durationSeconds)
        || durationSeconds < 0.0
        || durationSeconds > maximumSeconds) {
        reportFailure(request.generation, request.filePath,
                      tr("오디오 재생 시간이 올바르지 않습니다."));
        return;
    }

    const QFileInfo file(request.filePath);
    if (!file.isFile()) {
        reportFailure(request.generation, request.filePath,
                      tr("메타데이터 조회 중 파일이 삭제되었습니다."));
        return;
    }

    AudioFileMetadata metadata;
    metadata.filePath = request.filePath;
    metadata.fileName = file.fileName();
    metadata.sizeBytes = file.size();
    metadata.durationMilliseconds = qRound64(durationSeconds * 1000.0);
    emit inspectionSucceeded(metadata);
}

void AudioFileInfo::processFailedToStart(QProcess *process)
{
    const auto iterator = m_requests.find(process);
    if (iterator == m_requests.end()) {
        return;
    }

    const Request request = iterator.value();
    m_requests.erase(iterator);
    if (m_currentProcess == process) {
        m_currentProcess.clear();
    }
    process->deleteLater();
    reportFailure(request.generation, request.filePath,
                  tr("Python 메타데이터 프로세스를 시작할 수 없습니다."));
}

void AudioFileInfo::reportFailure(quint64 generation, const QString &filePath,
                                  const QString &message)
{
    if (generation == m_generation) {
        emit inspectionFailed(filePath, message);
    }
}
