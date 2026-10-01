#ifndef AUDIOFILEINFO_H
#define AUDIOFILEINFO_H

#include <QByteArray>
#include <QHash>
#include <QMetaType>
#include <QObject>
#include <QPointer>
#include <QString>

class QProcess;

struct AudioFileMetadata
{
    QString filePath;
    QString fileName;
    qint64 sizeBytes = 0;
    qint64 durationMilliseconds = -1;
};

Q_DECLARE_METATYPE(AudioFileMetadata)

class AudioFileInfo : public QObject
{
    Q_OBJECT

public:
    explicit AudioFileInfo(QObject *parent = nullptr);
    ~AudioFileInfo() override;

    void setPythonProgram(const QString &program);
    QString pythonProgram() const;

    void setProbeScript(const QString &scriptPath);
    QString probeScript() const;

    bool isInspecting() const;

    static QString fileDialogFilter();
    static bool isSupportedFile(const QString &filePath);

public slots:
    void inspect(const QString &filePath);
    void cancel();

signals:
    void inspectionStarted(const QString &filePath);
    void inspectionSucceeded(const AudioFileMetadata &metadata);
    void inspectionFailed(const QString &filePath, const QString &message);

private:
    struct Request
    {
        quint64 generation = 0;
        QString filePath;
        QByteArray standardOutput;
        QByteArray standardError;
    };

    void stopCurrentProcess();
    void finishProcess(QProcess *process, int exitCode, int exitStatus);
    void processFailedToStart(QProcess *process);
    void reportFailure(quint64 generation, const QString &filePath,
                       const QString &message);

    QString m_pythonProgram;
    QString m_probeScript;
    quint64 m_generation = 0;
    QPointer<QProcess> m_currentProcess;
    QHash<QProcess *, Request> m_requests;
};

#endif // AUDIOFILEINFO_H
