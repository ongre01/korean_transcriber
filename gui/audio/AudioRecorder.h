#ifndef AUDIORECORDER_H
#define AUDIORECORDER_H

#include <QAudioFormat>
#include <QByteArray>
#include <QList>
#include <QObject>
#include <QString>

#include <memory>

struct AudioInputDevice
{
    QByteArray id;
    QString description;
    bool isDefault = false;
};

// Asynchronous microphone recorder. The platform audio API is deliberately
// hidden behind this class so the rest of the application does not depend on
// QAudioSource/QAudioInput or QAudioDevice/QAudioDeviceInfo.
class AudioRecorder final : public QObject
{
    Q_OBJECT

public:
    explicit AudioRecorder(QObject *parent = nullptr);
    ~AudioRecorder() override;

    QList<AudioInputDevice> inputDevices() const;
    QByteArray selectedInputDeviceId() const;
    bool setInputDevice(const QByteArray &deviceId);

    QString outputDirectory() const;
    bool setOutputDirectory(const QString &directory);

    bool startRecording();
    void stopRecording();

    bool isRecording() const noexcept;
    QString outputFile() const;
    QAudioFormat captureFormat() const;

    // Deterministic helpers used by the streaming capture path and unit tests.
    // They never retain the supplied PCM data.
    static float normalizedLevel(const QAudioFormat &format,
                                 const QByteArray &pcmBytes);
    static qint64 durationForBytes(const QAudioFormat &format,
                                   quint64 byteCount);

signals:
    void inputDevicesChanged();
    void recordingStarted();
    void recordingStopped(const QString &file);
    void recordingLevelChanged(float level);
    void recordingTimeChanged(qint64 milliseconds);
    void errorOccurred(const QString &message);

private:
    class CaptureSink;
    struct PlatformState;

    void refreshInputDevices();
    bool beginCapture();
    void captured(const char *data, qint64 byteCount);
    void captureStateChanged(int state);
    void failRecording(const QString &message);
    void disposeCapture(bool abortWriter);
    QString nextOutputFile() const;

    std::unique_ptr<PlatformState> m_platform;
    std::unique_ptr<CaptureSink> m_sink;
    std::unique_ptr<class WavWriter> m_writer;
    QList<AudioInputDevice> m_inputDevices;
    QByteArray m_selectedDeviceId;
    QByteArray m_activeDeviceId;
    QString m_outputDirectory;
    QString m_outputFile;
    QAudioFormat m_captureFormat;
    quint64 m_captureGeneration = 0;
    qint64 m_lastTimeMilliseconds = -1;
    bool m_recording = false;
    bool m_startPending = false;
    bool m_stopping = false;
};

#endif // AUDIORECORDER_H
