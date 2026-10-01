#ifndef WAVWRITER_H
#define WAVWRITER_H

#include <QAudioFormat>
#include <QIODevice>
#include <QString>

#include <memory>

class QSaveFile;

// Streaming RIFF/WAVE writer for the raw bytes produced by QAudioSource.
// Call finalize() after capture stops. Calling close() before finalize() aborts
// the file so an incomplete recording is never reported as successful.
class WavWriter final : public QIODevice
{
    Q_OBJECT

public:
    explicit WavWriter(QObject *parent = nullptr);
    ~WavWriter() override;

    bool open(const QString &fileName, const QAudioFormat &format);

    // This overload is intended for seekable output devices and tests. The
    // device must already be open, writable, and empty. Ownership stays with
    // the caller and the device remains open after finalize().
    bool open(QIODevice *output, const QAudioFormat &format);

    bool finalize();
    void abort();
    void close() override;

    QString fileName() const;
    QAudioFormat audioFormat() const;
    quint64 dataSize() const;
    bool isFinalized() const;

    static bool isFormatSupported(const QAudioFormat &format,
                                  QString *reason = nullptr);

    // A classic RIFF chunk has a 32-bit size. The RIFF size also includes the
    // 36 bytes preceding the sample payload, so this is the largest data chunk
    // that can be represented without RF64.
    static constexpr quint64 maximumDataSize() noexcept
    {
        return quint64(0xffffffffu) - 36u;
    }

    bool isSequential() const override;

protected:
    qint64 readData(char *data, qint64 maxSize) override;
    qint64 writeData(const char *data, qint64 maxSize) override;

private:
    bool begin(QIODevice *output, const QAudioFormat &format);
    bool writeHeader(quint32 dataSize);
    void setFailure(const QString &message);
    void discardOutput();

    std::unique_ptr<QSaveFile> m_ownedFile;
    QIODevice *m_output = nullptr;
    QString m_fileName;
    QAudioFormat m_format;
    quint64 m_dataSize = 0;
    bool m_failed = false;
    bool m_finalized = false;
};

#endif // WAVWRITER_H
