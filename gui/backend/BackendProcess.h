#ifndef BACKENDPROCESS_H
#define BACKENDPROCESS_H

#include "BackendEvent.h"
#include "TranscribeOptions.h"

#include <QByteArray>
#include <QObject>
#include <QProcess>
#include <QString>
#include <QTimer>

class BackendProcess : public QObject
{
    Q_OBJECT

public:
    explicit BackendProcess(QObject *parent = nullptr);
    ~BackendProcess() override;

    void setPythonProgram(const QString &program);
    QString pythonProgram() const;

    void setBridgeScript(const QString &scriptPath);
    QString bridgeScript() const;

    void setWorkingDirectory(const QString &directory);
    QString workingDirectory() const;

    void start(const TranscribeOptions &options);
    void cancel();
    void setCancellationGracePeriod(int milliseconds);
    int cancellationGracePeriod() const noexcept;

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
    void cancelled();
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
    void escalateCancellation();
    void finishCancellation();
    void failRun(const QString &message, bool terminateProcess = true);

    QProcess m_process;
    QTimer m_cancellationTimer;
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
    bool m_cancellationRequested = false;
    int m_cancellationGracePeriodMilliseconds = 3000;
};

#endif // BACKENDPROCESS_H
