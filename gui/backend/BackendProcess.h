#ifndef BACKENDPROCESS_H
#define BACKENDPROCESS_H

#include "BackendEvent.h"
#include "TranscribeOptions.h"

#include <QByteArray>
#include <QObject>
#include <QProcess>
#include <QString>

class BackendProcess : public QObject
{
    Q_OBJECT

public:
    explicit BackendProcess(QObject *parent = nullptr);

    void setPythonProgram(const QString &program);
    QString pythonProgram() const;

    void setBridgeScript(const QString &scriptPath);
    QString bridgeScript() const;

    void setWorkingDirectory(const QString &directory);
    QString workingDirectory() const;

    void start(const TranscribeOptions &options);
    void cancel();

    bool isRunning() const;
    QString textResultFile() const;
    QString srtResultFile() const;
    QByteArray standardErrorOutput() const;

signals:
    void started();
    // -1 represents protocol progress value null (indeterminate progress).
    void progressChanged(int progress);
    void progressTimeChanged(double processedSeconds, double totalSeconds);
    void stateChanged(QString state);
    // speaker is one-based; 0 represents an unassigned speaker.
    void segmentReceived(double start, double end, int speaker, QString text);
    void completed();
    void errorOccurred(QString message);
    void standardErrorReceived(QByteArray data);

private:
    void resetRunState();
    void readStandardOutput();
    void readStandardError();
    void consumeCompleteLines();
    void consumeLine(QByteArray line);
    void dispatchEvent(const BackendEvent &event);
    void processFinished(int exitCode, QProcess::ExitStatus exitStatus);
    void processError(QProcess::ProcessError error);
    void failRun(const QString &message, bool terminateProcess = true);

    QProcess m_process;
    QString m_pythonProgram = QStringLiteral("python");
    QString m_bridgeScript;
    QString m_workingDirectory;

    QByteArray m_stdoutBuffer;
    QByteArray m_standardError;
    BackendProtocol::EventStreamValidator m_streamValidator;

    QString m_textResultFile;
    QString m_srtResultFile;
    QString m_backendError;
    bool m_receivedCompleted = false;
    bool m_receivedError = false;
    bool m_protocolFailed = false;
    bool m_terminalSignalEmitted = false;
};

#endif // BACKENDPROCESS_H
