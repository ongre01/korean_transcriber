#include "WavWriter.h"

#include <QDataStream>
#include <QFileDevice>
#include <QSaveFile>

#include <limits>

namespace {
struct WaveFormatFields
{
    quint16 formatTag = 0;
    quint16 channelCount = 0;
    quint32 sampleRate = 0;
    quint32 byteRate = 0;
    quint16 blockAlign = 0;
    quint16 bitsPerSample = 0;
};

QString outputError(QIODevice *output, const QString &fallback)
{
    if (output && !output->errorString().isEmpty()
        && output->errorString() != QStringLiteral("Unknown error")) {
        return output->errorString();
    }
    return fallback;
}

bool describeFormat(const QAudioFormat &format,
                    WaveFormatFields *fields,
                    QString *reason)
{
    auto reject = [reason](const QString &message) {
        if (reason) {
            *reason = message;
        }
        return false;
    };

    if (format.sampleRate() <= 0) {
        return reject(QStringLiteral("The sample rate must be positive."));
    }
    if (format.channelCount() <= 0
        || format.channelCount() > std::numeric_limits<quint16>::max()) {
        return reject(QStringLiteral("The channel count is outside the WAV range."));
    }

    quint16 formatTag = 1; // WAVE_FORMAT_PCM
    quint16 bitsPerSample = 0;
    switch (format.sampleFormat()) {
    case QAudioFormat::UInt8:
        bitsPerSample = 8;
        break;
    case QAudioFormat::Int16:
        bitsPerSample = 16;
        break;
    case QAudioFormat::Int32:
        bitsPerSample = 32;
        break;
    case QAudioFormat::Float:
        formatTag = 3; // WAVE_FORMAT_IEEE_FLOAT
        bitsPerSample = 32;
        break;
    default:
        return reject(QStringLiteral("The Qt audio sample format is not supported by WAV."));
    }

    const quint64 blockAlign = quint64(format.channelCount()) * bitsPerSample / 8u;
    const quint64 byteRate = quint64(format.sampleRate()) * blockAlign;
    if (blockAlign == 0 || blockAlign > std::numeric_limits<quint16>::max()) {
        return reject(QStringLiteral("The WAV block alignment is out of range."));
    }
    if (byteRate > std::numeric_limits<quint32>::max()) {
        return reject(QStringLiteral("The WAV byte rate is out of range."));
    }

    if (fields) {
        fields->formatTag = formatTag;
        fields->channelCount = quint16(format.channelCount());
        fields->sampleRate = quint32(format.sampleRate());
        fields->byteRate = quint32(byteRate);
        fields->blockAlign = quint16(blockAlign);
        fields->bitsPerSample = bitsPerSample;
    }
    if (reason) {
        reason->clear();
    }
    return true;
}
} // namespace

WavWriter::WavWriter(QObject *parent)
    : QIODevice(parent)
{
}

WavWriter::~WavWriter()
{
    if (QIODevice::isOpen() || m_output) {
        abort();
    }
}

bool WavWriter::open(const QString &fileName, const QAudioFormat &format)
{
    if (QIODevice::isOpen() || m_output) {
        setErrorString(QStringLiteral("A WAV output is already open."));
        return false;
    }
    if (fileName.trimmed().isEmpty()) {
        setErrorString(QStringLiteral("The WAV output path is empty."));
        return false;
    }

    QString formatError;
    if (!isFormatSupported(format, &formatError)) {
        setErrorString(formatError);
        return false;
    }

    auto file = std::make_unique<QSaveFile>(fileName);
    file->setDirectWriteFallback(false);
    if (!file->open(QIODevice::WriteOnly)) {
        setErrorString(QStringLiteral("Could not create WAV file '%1': %2")
                           .arg(fileName, file->errorString()));
        return false;
    }

    m_fileName = fileName;
    m_ownedFile = std::move(file);
    if (!begin(m_ownedFile.get(), format)) {
        discardOutput();
        return false;
    }
    return true;
}

bool WavWriter::open(QIODevice *output, const QAudioFormat &format)
{
    if (QIODevice::isOpen() || m_output) {
        setErrorString(QStringLiteral("A WAV output is already open."));
        return false;
    }
    if (!output) {
        setErrorString(QStringLiteral("The WAV output device is null."));
        return false;
    }
    if (!output->isOpen() || !output->isWritable()) {
        setErrorString(QStringLiteral("The WAV output device must be open and writable."));
        return false;
    }
    if (output->isSequential()) {
        setErrorString(QStringLiteral("The WAV output device must support seeking."));
        return false;
    }
    if (output->size() != 0) {
        setErrorString(QStringLiteral("The WAV output device must be empty."));
        return false;
    }

    QString formatError;
    if (!isFormatSupported(format, &formatError)) {
        setErrorString(formatError);
        return false;
    }

    m_fileName.clear();
    if (!begin(output, format)) {
        m_output = nullptr;
        return false;
    }
    return true;
}

bool WavWriter::begin(QIODevice *output, const QAudioFormat &format)
{
    m_output = output;
    m_format = format;
    m_dataSize = 0;
    m_failed = false;
    m_finalized = false;
    setErrorString(QString());

    if (!m_output->seek(0)) {
        setFailure(outputError(m_output,
                               QStringLiteral("Could not seek in the WAV output.")));
        return false;
    }
    if (!writeHeader(0)) {
        return false;
    }
    if (!QIODevice::open(QIODevice::WriteOnly)) {
        setFailure(QStringLiteral("Could not open the WAV stream."));
        return false;
    }
    return true;
}

bool WavWriter::finalize()
{
    if (m_finalized) {
        return true;
    }
    if (!QIODevice::isOpen() || !m_output) {
        setErrorString(QStringLiteral("No WAV output is open."));
        return false;
    }
    if (m_failed) {
        discardOutput();
        return false;
    }

    WaveFormatFields fields;
    QString formatError;
    if (!describeFormat(m_format, &fields, &formatError)) {
        setFailure(formatError);
        discardOutput();
        return false;
    }
    if (m_dataSize % fields.blockAlign != 0) {
        setFailure(QStringLiteral("The PCM payload does not end on an audio frame boundary."));
        discardOutput();
        return false;
    }
    if (!m_output->seek(0)) {
        setFailure(outputError(m_output,
                               QStringLiteral("Could not seek to the WAV header.")));
        discardOutput();
        return false;
    }
    if (!writeHeader(quint32(m_dataSize))) {
        discardOutput();
        return false;
    }

    if (auto *fileDevice = qobject_cast<QFileDevice *>(m_output)) {
        if (!fileDevice->flush()) {
            setFailure(QStringLiteral("Could not flush the WAV file: %1")
                           .arg(fileDevice->errorString()));
            discardOutput();
            return false;
        }
    }

    QIODevice::close();
    if (m_ownedFile) {
        QSaveFile *file = m_ownedFile.get();
        if (!file->commit()) {
            setFailure(QStringLiteral("Could not finalize WAV file '%1': %2")
                           .arg(m_fileName, file->errorString()));
            m_output = nullptr;
            m_ownedFile.reset();
            return false;
        }
        m_ownedFile.reset();
    }

    m_output = nullptr;
    m_finalized = true;
    return true;
}

void WavWriter::abort()
{
    if (!m_failed) {
        setErrorString(QStringLiteral("WAV writing was aborted."));
    }
    discardOutput();
}

void WavWriter::close()
{
    if (QIODevice::isOpen() || m_output) {
        abort();
        return;
    }
    QIODevice::close();
}

QString WavWriter::fileName() const
{
    return m_fileName;
}

QAudioFormat WavWriter::audioFormat() const
{
    return m_format;
}

quint64 WavWriter::dataSize() const
{
    return m_dataSize;
}

bool WavWriter::isFinalized() const
{
    return m_finalized;
}

bool WavWriter::isFormatSupported(const QAudioFormat &format, QString *reason)
{
    return describeFormat(format, nullptr, reason);
}

bool WavWriter::isSequential() const
{
    return true;
}

qint64 WavWriter::readData(char *, qint64)
{
    setErrorString(QStringLiteral("A WavWriter is write-only."));
    return -1;
}

qint64 WavWriter::writeData(const char *data, qint64 maxSize)
{
    if (maxSize < 0 || (maxSize > 0 && !data)) {
        setFailure(QStringLiteral("Invalid PCM data was supplied."));
        return -1;
    }
    if (maxSize == 0) {
        return 0;
    }
    if (m_failed || !m_output) {
        if (!m_failed) {
            setFailure(QStringLiteral("No WAV output is available."));
        }
        return -1;
    }

    const quint64 requested = quint64(maxSize);
    if (requested > maximumDataSize() - m_dataSize) {
        setFailure(QStringLiteral("The recording exceeds the classic RIFF size limit."));
        return -1;
    }

    const qint64 written = m_output->write(data, maxSize);
    if (written != maxSize) {
        if (written > 0) {
            m_dataSize += quint64(written);
        }
        setFailure(outputError(m_output,
                               QStringLiteral("Could not write PCM data to the WAV output.")));
        return -1;
    }

    m_dataSize += requested;
    return written;
}

bool WavWriter::writeHeader(quint32 dataSize)
{
    WaveFormatFields fields;
    QString formatError;
    if (!describeFormat(m_format, &fields, &formatError)) {
        setFailure(formatError);
        return false;
    }

    QByteArray header;
    QDataStream stream(&header, QIODevice::WriteOnly);
    stream.setByteOrder(QDataStream::LittleEndian);
    stream.writeRawData("RIFF", 4);
    stream << quint32(36u + dataSize);
    stream.writeRawData("WAVE", 4);
    stream.writeRawData("fmt ", 4);
    stream << quint32(16u);
    stream << fields.formatTag;
    stream << fields.channelCount;
    stream << fields.sampleRate;
    stream << fields.byteRate;
    stream << fields.blockAlign;
    stream << fields.bitsPerSample;
    stream.writeRawData("data", 4);
    stream << dataSize;

    if (stream.status() != QDataStream::Ok || header.size() != 44) {
        setFailure(QStringLiteral("Could not construct the WAV header."));
        return false;
    }

    const qint64 written = m_output->write(header);
    if (written != header.size()) {
        setFailure(outputError(m_output,
                               QStringLiteral("Could not write the WAV header.")));
        return false;
    }
    return true;
}

void WavWriter::setFailure(const QString &message)
{
    m_failed = true;
    setErrorString(message.isEmpty() ? QStringLiteral("WAV writing failed.") : message);
}

void WavWriter::discardOutput()
{
    QIODevice::close();
    if (m_ownedFile) {
        m_ownedFile->cancelWriting();
        m_ownedFile.reset();
    }
    m_output = nullptr;
    m_finalized = false;
}
