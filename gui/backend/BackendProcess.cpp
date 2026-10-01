#include "BackendProcess.h"

#include <QDir>
#include <QFileInfo>
#include <QRegularExpression>
#include <QStringList>

namespace {

QString stateName(BackendState state)
{
    switch (state) {
    case BackendState::Preparing:
        return QStringLiteral("preparing");
    case BackendState::LoadingModel:
        return QStringLiteral("loading_model");
    case BackendState::DecodingAudio:
        return QStringLiteral("decoding_audio");
    case BackendState::Transcribing:
        return QStringLiteral("transcribing");
    case BackendState::Diarization:
        return QStringLiteral("diarization");
    case BackendState::SavingResult:
        return QStringLiteral("saving_result");
    }
    return {};
}

QString quotedCommandArgument(const QString &argument)
{
    if (!argument.isEmpty()
        && !argument.contains(QRegularExpression(QStringLiteral("[\\s\\\"]")))) {
        return argument;
    }

    QString escaped = argument;
    escaped.replace(QStringLiteral("\\\""), QStringLiteral("\\\\\""));
    return QStringLiteral("\"") + escaped + QStringLiteral("\"");
}

QString commandForLog(const QString &program, const QStringList &arguments)
{
    QStringList parts;
    parts.reserve(arguments.size() + 1);
    parts.append(quotedCommandArgument(program));
    for (const QString &argument : arguments) {
        parts.append(quotedCommandArgument(argument));
    }
    return parts.join(QLatin1Char(' '));
}

} // namespace

BackendProcess::BackendProcess(QObject *parent)
    : QObject(parent)
{
    m_process.setProcessChannelMode(QProcess::SeparateChannels);
    m_cancellationTimer.setSingleShot(true);

    connect(&m_process, &QProcess::started, this, &BackendProcess::started);
    connect(&m_process, &QProcess::readyReadStandardOutput,
            this, &BackendProcess::readStandardOutput);
    connect(&m_process, &QProcess::readyReadStandardError,
            this, &BackendProcess::readStandardError);
    connect(&m_process, &QProcess::errorOccurred,
            this, &BackendProcess::processError);
    connect(&m_process,
            qOverload<int, QProcess::ExitStatus>(&QProcess::finished),
            this, &BackendProcess::processFinished);
    connect(&m_cancellationTimer, &QTimer::timeout,
            this, &BackendProcess::escalateCancellation);
}

BackendProcess::~BackendProcess()
{
    m_cancellationTimer.stop();
    // Destruction cannot rely on a later event-loop iteration.  Disconnecting
    // first prevents terminal signals from reaching a partially destroyed owner.
    m_process.disconnect(this);
    if (!isRunning()) {
        return;
    }

    m_process.terminate();
    m_process.kill();
}

void BackendProcess::setPythonProgram(const QString &program)
{
    m_pythonProgram = program;
}

QString BackendProcess::pythonProgram() const
{
    return m_pythonProgram;
}

void BackendProcess::setBridgeScript(const QString &scriptPath)
{
    m_bridgeScript = scriptPath;
}

QString BackendProcess::bridgeScript() const
{
    return m_bridgeScript;
}

void BackendProcess::setWorkingDirectory(const QString &directory)
{
    m_workingDirectory = directory;
}

QString BackendProcess::workingDirectory() const
{
    return m_workingDirectory;
}

void BackendProcess::start(const TranscribeOptions &options)
{
    if (isRunning()) {
        emit errorOccurred(QStringLiteral("A backend process is already running."));
        return;
    }

    resetRunState();

    QString validationError;
    if (!options.isValid(&validationError)) {
        failRun(QStringLiteral("Invalid transcription options: %1").arg(validationError),
                false);
        return;
    }
    if (m_pythonProgram.trimmed().isEmpty()) {
        failRun(QStringLiteral("Python program must not be empty."), false);
        return;
    }
    if (m_bridgeScript.trimmed().isEmpty()) {
        failRun(QStringLiteral("Backend bridge script must not be empty."), false);
        return;
    }

    QString processDirectory = m_workingDirectory;
    QFileInfo scriptInfo(m_bridgeScript);
    if (processDirectory.trimmed().isEmpty()) {
        processDirectory = scriptInfo.absolutePath();
    }

    QString scriptArgument = m_bridgeScript;
    if (scriptInfo.isRelative()) {
        scriptArgument = QDir(processDirectory).absoluteFilePath(m_bridgeScript);
    }

    QStringList arguments {QDir::cleanPath(scriptArgument)};
    arguments.append(options.toBridgeArguments());

    m_process.setProgram(m_pythonProgram);
    m_process.setArguments(arguments);
    m_process.setWorkingDirectory(QDir::cleanPath(processDirectory));
    emit commandStarted(commandForLog(m_pythonProgram, arguments),
                        m_process.workingDirectory());
    m_process.start();
}

void BackendProcess::cancel()
{
    if (m_terminalSignalEmitted || m_cancellationRequested) {
        return;
    }

    m_cancellationRequested = true;
    m_stdoutBuffer.clear();
    if (!isRunning()) {
        finishCancellation();
        return;
    }

    m_cancellationTimer.start(m_cancellationGracePeriodMilliseconds);
    m_process.terminate();
}

void BackendProcess::setCancellationGracePeriod(int milliseconds)
{
    m_cancellationGracePeriodMilliseconds = milliseconds < 0 ? 0 : milliseconds;
}

int BackendProcess::cancellationGracePeriod() const noexcept
{
    return m_cancellationGracePeriodMilliseconds;
}

bool BackendProcess::isRunning() const
{
    return m_process.state() != QProcess::NotRunning;
}

QString BackendProcess::textResultFile() const
{
    return m_textResultFile;
}

QString BackendProcess::srtResultFile() const
{
    return m_srtResultFile;
}

QByteArray BackendProcess::standardErrorOutput() const
{
    return m_standardError;
}

void BackendProcess::resetRunState()
{
    m_cancellationTimer.stop();
    m_stdoutBuffer.clear();
    m_standardError.clear();
    m_streamValidator = BackendProtocol::EventStreamValidator();
    m_textResultFile.clear();
    m_srtResultFile.clear();
    m_backendError.clear();
    m_receivedCompleted = false;
    m_receivedError = false;
    m_protocolFailed = false;
    m_terminalSignalEmitted = false;
    m_cancellationRequested = false;
}

void BackendProcess::readStandardOutput()
{
    const QByteArray data = m_process.readAllStandardOutput();
    if (m_cancellationRequested) {
        m_stdoutBuffer.clear();
        return;
    }
    m_stdoutBuffer.append(data);
    consumeCompleteLines();
}

void BackendProcess::readStandardError()
{
    const QByteArray data = m_process.readAllStandardError();
    if (data.isEmpty()) {
        return;
    }
    m_standardError.append(data);
    // Diagnostics remain useful even for a user-cancelled process: Python can
    // have already written a traceback or termination detail before exiting.
    emit standardErrorReceived(data);
}

void BackendProcess::consumeCompleteLines()
{
    qsizetype newline = m_stdoutBuffer.indexOf('\n');
    while (newline >= 0) {
        QByteArray line = m_stdoutBuffer.left(newline);
        m_stdoutBuffer.remove(0, newline + 1);
        if (line.endsWith('\r')) {
            line.chop(1);
        }
        consumeLine(line);
        if (m_protocolFailed) {
            m_stdoutBuffer.clear();
            return;
        }
        newline = m_stdoutBuffer.indexOf('\n');
    }
}

void BackendProcess::consumeLine(QByteArray line)
{
    if (m_cancellationRequested || m_protocolFailed || m_terminalSignalEmitted) {
        return;
    }

    BackendEvent event;
    QString errorMessage;
    if (!BackendProtocol::parseEventLine(line, &event, &errorMessage)) {
        failRun(QStringLiteral("Backend protocol error: %1").arg(errorMessage));
        return;
    }
    if (!m_streamValidator.accept(event, &errorMessage)) {
        failRun(QStringLiteral("Backend protocol error: %1").arg(errorMessage));
        return;
    }
    dispatchEvent(event);
}

void BackendProcess::dispatchEvent(const BackendEvent &event)
{
    switch (event.type) {
    case BackendEventType::State:
        emit stateChanged(stateName(event.state));
        break;
    case BackendEventType::Progress:
        emit progressChanged(event.progress.value_or(-1));
        if (event.processedSeconds && event.totalSeconds) {
            emit progressTimeChanged(*event.processedSeconds, *event.totalSeconds);
        }
        break;
    case BackendEventType::Segment:
        emit segmentReceived(event.segment.startTime,
                             event.segment.endTime,
                             event.segment.speaker.value_or(0),
                             event.segment.text);
        break;
    case BackendEventType::Warning:
        emit warningOccurred(event.warningMessage);
        break;
    case BackendEventType::Completed:
        m_receivedCompleted = true;
        m_textResultFile = event.textFile;
        m_srtResultFile = event.srtFile;
        break;
    case BackendEventType::Error:
        m_receivedError = true;
        m_backendError = event.errorMessage;
        break;
    }
}

void BackendProcess::processFinished(int exitCode, QProcess::ExitStatus exitStatus)
{
    readStandardOutput();
    readStandardError();

    if (m_cancellationRequested) {
        finishCancellation();
        emit stopped();
        return;
    }

    if (!m_protocolFailed && !m_stdoutBuffer.isEmpty()) {
        QByteArray finalLine = m_stdoutBuffer;
        m_stdoutBuffer.clear();
        if (finalLine.endsWith('\r')) {
            finalLine.chop(1);
        }
        consumeLine(finalLine);
    }

    if (m_terminalSignalEmitted) {
        emit stopped();
        return;
    }
    if (exitStatus != QProcess::NormalExit) {
        failRun(QStringLiteral("Backend process crashed (exit code %1).").arg(exitCode),
                false);
        emit stopped();
        return;
    }

    QString errorMessage;
    if (!m_streamValidator.finish(exitCode, &errorMessage)) {
        failRun(QStringLiteral("Backend protocol error: %1").arg(errorMessage), false);
        emit stopped();
        return;
    }

    m_terminalSignalEmitted = true;
    if (m_receivedCompleted) {
        emit completed();
    } else if (m_receivedError) {
        emit errorOccurred(m_backendError);
    }
    emit stopped();
}

void BackendProcess::processError(QProcess::ProcessError error)
{
    if (m_cancellationRequested) {
        if (!isRunning()) {
            finishCancellation();
        }
        return;
    }

    if (m_terminalSignalEmitted) {
        return;
    }

    const QString message = error == QProcess::FailedToStart
        ? QStringLiteral("Failed to start backend process: %1").arg(m_process.errorString())
        : QStringLiteral("Backend process error: %1").arg(m_process.errorString());
    failRun(message, error != QProcess::Crashed);
}

void BackendProcess::escalateCancellation()
{
    if (m_cancellationRequested && !m_terminalSignalEmitted && isRunning()) {
        m_process.kill();
    }
}

void BackendProcess::finishCancellation()
{
    if (m_terminalSignalEmitted) {
        return;
    }

    m_cancellationTimer.stop();
    m_stdoutBuffer.clear();
    m_terminalSignalEmitted = true;
    emit cancelled();
}

void BackendProcess::failRun(const QString &message, bool terminateProcess)
{
    if (m_terminalSignalEmitted) {
        return;
    }

    m_protocolFailed = true;
    m_terminalSignalEmitted = true;
    emit errorOccurred(message);

    if (terminateProcess && isRunning()) {
        m_process.terminate();
    }
}
