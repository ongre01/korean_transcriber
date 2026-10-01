#include "audio/WavWriter.h"

#include <QAudioFormat>
#include <QDataStream>
#include <QFile>
#include <QFileInfo>
#include <QProcess>
#include <QStandardPaths>
#include <QTemporaryDir>
#include <QTest>

#include <cstring>

namespace {
QAudioFormat audioFormat(int sampleRate,
                         int channelCount = 1,
                         QAudioFormat::SampleFormat sampleFormat = QAudioFormat::Int16)
{
    QAudioFormat format;
    format.setSampleRate(sampleRate);
    format.setChannelCount(channelCount);
    format.setSampleFormat(sampleFormat);
    return format;
}

struct WaveHeader
{
    QByteArray riff;
    quint32 riffSize = 0;
    QByteArray wave;
    QByteArray formatChunk;
    quint32 formatSize = 0;
    quint16 formatTag = 0;
    quint16 channelCount = 0;
    quint32 sampleRate = 0;
    quint32 byteRate = 0;
    quint16 blockAlign = 0;
    quint16 bitsPerSample = 0;
    QByteArray dataChunk;
    quint32 dataSize = 0;
};

WaveHeader readHeader(const QString &path)
{
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return {};
    }

    WaveHeader header;
    QDataStream input(&file);
    input.setByteOrder(QDataStream::LittleEndian);
    char identifier[4];
    input.readRawData(identifier, 4);
    header.riff = QByteArray(identifier, 4);
    input >> header.riffSize;
    input.readRawData(identifier, 4);
    header.wave = QByteArray(identifier, 4);
    input.readRawData(identifier, 4);
    header.formatChunk = QByteArray(identifier, 4);
    input >> header.formatSize;
    input >> header.formatTag;
    input >> header.channelCount;
    input >> header.sampleRate;
    input >> header.byteRate;
    input >> header.blockAlign;
    input >> header.bitsPerSample;
    input.readRawData(identifier, 4);
    header.dataChunk = QByteArray(identifier, 4);
    input >> header.dataSize;
    return header;
}

class FailingWriteDevice final : public QIODevice
{
public:
    FailingWriteDevice()
    {
        QIODevice::open(QIODevice::ReadWrite);
    }

    bool isSequential() const override { return false; }
    qint64 size() const override { return m_bytes.size(); }

    bool seek(qint64 position) override
    {
        if (position < 0 || position > m_bytes.size()) {
            return false;
        }
        return QIODevice::seek(position);
    }

protected:
    qint64 readData(char *data, qint64 maxSize) override
    {
        const qint64 available = qMin(maxSize, m_bytes.size() - pos());
        if (available <= 0) {
            return 0;
        }
        std::memcpy(data, m_bytes.constData() + pos(), size_t(available));
        return available;
    }

    qint64 writeData(const char *data, qint64 maxSize) override
    {
        if (pos() >= 44) {
            setErrorString(QStringLiteral("simulated write failure"));
            return -1;
        }
        const qint64 end = pos() + maxSize;
        if (end > m_bytes.size()) {
            m_bytes.resize(int(end));
        }
        std::memcpy(m_bytes.data() + pos(), data, size_t(maxSize));
        return maxSize;
    }

private:
    QByteArray m_bytes;
};
} // namespace

class WavWriterTest : public QObject
{
    Q_OBJECT

private slots:
    void writesInt16Pcm_data();
    void writesInt16Pcm();
    void writesFloatFormatHeader();
    void finalizesEmptyWav();
    void rejectsUnsupportedFormat();
    void rejectsUnwritableDestination();
    void reportsMidStreamWriteFailure();
    void rejectsUnalignedDataAndRemovesOutput();
    void rejectsRiffOverflowBeforeWriting();
    void pythonDecoderReadsWav();
};

void WavWriterTest::writesInt16Pcm_data()
{
    QTest::addColumn<int>("sampleRate");
    QTest::newRow("44.1-kHz") << 44100;
    QTest::newRow("48-kHz") << 48000;
}

void WavWriterTest::writesInt16Pcm()
{
    QFETCH(int, sampleRate);
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString path = directory.filePath(QStringLiteral("recording.wav"));
    const QByteArray samples(320, '\x5a');

    WavWriter writer;
    QVERIFY2(writer.open(path, audioFormat(sampleRate)), qPrintable(writer.errorString()));
    QCOMPARE(writer.write(samples), qint64(samples.size()));
    QCOMPARE(writer.dataSize(), quint64(samples.size()));
    QVERIFY2(writer.finalize(), qPrintable(writer.errorString()));
    QVERIFY(writer.isFinalized());

    const WaveHeader header = readHeader(path);
    QCOMPARE(header.riff, QByteArray("RIFF", 4));
    QCOMPARE(header.riffSize, quint32(36 + samples.size()));
    QCOMPARE(header.wave, QByteArray("WAVE", 4));
    QCOMPARE(header.formatChunk, QByteArray("fmt ", 4));
    QCOMPARE(header.formatSize, quint32(16));
    QCOMPARE(header.formatTag, quint16(1));
    QCOMPARE(header.channelCount, quint16(1));
    QCOMPARE(header.sampleRate, quint32(sampleRate));
    QCOMPARE(header.byteRate, quint32(sampleRate * 2));
    QCOMPARE(header.blockAlign, quint16(2));
    QCOMPARE(header.bitsPerSample, quint16(16));
    QCOMPARE(header.dataChunk, QByteArray("data", 4));
    QCOMPARE(header.dataSize, quint32(samples.size()));
    QCOMPARE(QFileInfo(path).size(), qint64(44 + samples.size()));

    QFile output(path);
    QVERIFY(output.open(QIODevice::ReadOnly));
    QVERIFY(output.seek(44));
    QCOMPARE(output.readAll(), samples);
}

void WavWriterTest::writesFloatFormatHeader()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString path = directory.filePath(QStringLiteral("stereo-float.wav"));
    const QByteArray samples(32, '\0');

    WavWriter writer;
    QVERIFY2(writer.open(path, audioFormat(48000, 2, QAudioFormat::Float)),
             qPrintable(writer.errorString()));
    QCOMPARE(writer.write(samples), qint64(samples.size()));
    QVERIFY2(writer.finalize(), qPrintable(writer.errorString()));

    const WaveHeader header = readHeader(path);
    QCOMPARE(header.formatTag, quint16(3));
    QCOMPARE(header.channelCount, quint16(2));
    QCOMPARE(header.sampleRate, quint32(48000));
    QCOMPARE(header.byteRate, quint32(48000 * 8));
    QCOMPARE(header.blockAlign, quint16(8));
    QCOMPARE(header.bitsPerSample, quint16(32));
    QCOMPARE(header.dataSize, quint32(samples.size()));
}

void WavWriterTest::finalizesEmptyWav()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString path = directory.filePath(QStringLiteral("empty.wav"));

    WavWriter writer;
    QVERIFY2(writer.open(path, audioFormat(44100)), qPrintable(writer.errorString()));
    QVERIFY2(writer.finalize(), qPrintable(writer.errorString()));

    const WaveHeader header = readHeader(path);
    QCOMPARE(header.riffSize, quint32(36));
    QCOMPARE(header.dataSize, quint32(0));
    QCOMPARE(QFileInfo(path).size(), qint64(44));
}

void WavWriterTest::rejectsUnsupportedFormat()
{
    QAudioFormat format;
    format.setSampleRate(48000);
    format.setChannelCount(1);
    format.setSampleFormat(QAudioFormat::Unknown);

    QString reason;
    QVERIFY(!WavWriter::isFormatSupported(format, &reason));
    QVERIFY(!reason.isEmpty());

    QTemporaryDir directory;
    WavWriter writer;
    QVERIFY(!writer.open(directory.filePath(QStringLiteral("unsupported.wav")), format));
    QVERIFY(!writer.errorString().isEmpty());
}

void WavWriterTest::rejectsUnwritableDestination()
{
    WavWriter writer;
    QVERIFY(!writer.open(QStringLiteral(":/unwritable.wav"), audioFormat(48000)));
    QVERIFY(!writer.errorString().isEmpty());
}

void WavWriterTest::reportsMidStreamWriteFailure()
{
    FailingWriteDevice output;
    WavWriter writer;
    QVERIFY2(writer.open(&output, audioFormat(48000)), qPrintable(writer.errorString()));

    const QByteArray samples(4, '\0');
    QCOMPARE(writer.write(samples), qint64(-1));
    QVERIFY(writer.errorString().contains(QStringLiteral("simulated")));
    QVERIFY(!writer.finalize());
    QVERIFY(!writer.isFinalized());
}

void WavWriterTest::rejectsUnalignedDataAndRemovesOutput()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString path = directory.filePath(QStringLiteral("partial-frame.wav"));

    WavWriter writer;
    QVERIFY2(writer.open(path, audioFormat(48000)), qPrintable(writer.errorString()));
    QCOMPARE(writer.write(QByteArray(1, '\0')), qint64(1));
    QVERIFY(!writer.finalize());
    QVERIFY(writer.errorString().contains(QStringLiteral("frame")));
    QVERIFY(!QFileInfo::exists(path));
}

void WavWriterTest::rejectsRiffOverflowBeforeWriting()
{
    FailingWriteDevice output;
    WavWriter writer;
    QVERIFY2(writer.open(&output, audioFormat(48000)), qPrintable(writer.errorString()));

    char sample = 0;
    const qint64 tooLarge = qint64(WavWriter::maximumDataSize()) + 1;
    QCOMPARE(writer.write(&sample, tooLarge), qint64(-1));
    QVERIFY(writer.errorString().contains(QStringLiteral("RIFF")));
    QCOMPARE(writer.dataSize(), quint64(0));
    QVERIFY(!writer.finalize());
}

void WavWriterTest::pythonDecoderReadsWav()
{
    QString python = qEnvironmentVariable("PYTHON").trimmed();
    if (python.isEmpty()) {
        python = QStandardPaths::findExecutable(QStringLiteral("python"));
    }
    QVERIFY2(!python.isEmpty(), "A Python interpreter is required for the decode test");

    const QString transcriber = QFINDTESTDATA("../../engine/transcribe_npu.py");
    QVERIFY2(!transcriber.isEmpty(), "engine/transcribe_npu.py was not found");

    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString path = directory.filePath(QStringLiteral("python-decode.wav"));
    const int sampleCount = 4800;

    WavWriter writer;
    QVERIFY2(writer.open(path, audioFormat(48000)), qPrintable(writer.errorString()));
    QCOMPARE(writer.write(QByteArray(sampleCount * 2, '\0')), qint64(sampleCount * 2));
    QVERIFY2(writer.finalize(), qPrintable(writer.errorString()));

    const QString script = QStringLiteral(
        "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
        "import transcribe_npu; audio = transcribe_npu.decode_audio_16k_mono(Path(sys.argv[2])); "
        "assert audio.ndim == 1 and audio.dtype.name == 'float32' and len(audio) > 0; "
        "print(len(audio))");
    QProcess process;
    process.start(python,
                  {QStringLiteral("-c"),
                   script,
                   QFileInfo(transcriber).absolutePath(),
                   path});
    QVERIFY2(process.waitForStarted(5000), qPrintable(process.errorString()));
    QVERIFY2(process.waitForFinished(15000), "Python WAV decode timed out");
    QCOMPARE(process.exitStatus(), QProcess::NormalExit);
    QVERIFY2(process.exitCode() == 0,
             qPrintable(QString::fromUtf8(process.readAllStandardError())));
    QVERIFY(!process.readAllStandardOutput().trimmed().isEmpty());
}

QTEST_GUILESS_MAIN(WavWriterTest)

#include "tst_wav_writer.moc"
