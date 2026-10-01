#include "input/AudioFileInfo.h"

#include <QFile>
#include <QFileInfo>
#include <QDataStream>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QTest>

class AudioFileInfoTest : public QObject
{
    Q_OBJECT

private slots:
    void supportedExtensionsAndFilter();
    void bundledProbeReadsWav();
    void successWithSpacesAndKoreanPath();
    void decodeFailure();
    void missingFile();
    void newerSelectionWins();
};

namespace {
QString pythonProgram()
{
    const QString configured = QString::fromLocal8Bit(qgetenv("PYTHON")).trimmed();
    return configured.isEmpty() ? QStringLiteral("python") : configured;
}

QString createFile(const QTemporaryDir &directory, const QString &name)
{
    const QString path = directory.filePath(name);
    QFile file(path);
    if (!file.open(QIODevice::WriteOnly) || file.write("fixture") < 0) {
        return QString();
    }
    file.close();
    return path;
}

QString createWavFile(const QTemporaryDir &directory, const QString &name)
{
    constexpr quint32 sampleRate = 16000;
    constexpr quint16 channelCount = 1;
    constexpr quint16 bitsPerSample = 16;
    constexpr quint32 sampleCount = sampleRate / 5;
    constexpr quint32 dataSize = sampleCount * channelCount * bitsPerSample / 8;

    const QString path = directory.filePath(name);
    QFile file(path);
    if (!file.open(QIODevice::WriteOnly)) {
        return QString();
    }

    QDataStream output(&file);
    output.setByteOrder(QDataStream::LittleEndian);
    output.writeRawData("RIFF", 4);
    output << quint32(36 + dataSize);
    output.writeRawData("WAVE", 4);
    output.writeRawData("fmt ", 4);
    output << quint32(16) << quint16(1) << channelCount << sampleRate;
    output << quint32(sampleRate * channelCount * bitsPerSample / 8);
    output << quint16(channelCount * bitsPerSample / 8) << bitsPerSample;
    output.writeRawData("data", 4);
    output << dataSize;
    file.write(QByteArray(dataSize, '\0'));
    file.close();
    return output.status() == QDataStream::Ok ? path : QString();
}
} // namespace

void AudioFileInfoTest::supportedExtensionsAndFilter()
{
    const QStringList supported {
        QStringLiteral("sample.wav"),
        QStringLiteral("sample.MP3"),
        QStringLiteral("sample.m4a"),
        QStringLiteral("sample.aac"),
        QStringLiteral("sample.flac"),
        QStringLiteral("sample.ogg"),
        QStringLiteral("sample.mp4")
    };
    for (const QString &file : supported) {
        QVERIFY2(AudioFileInfo::isSupportedFile(file), qPrintable(file));
        QVERIFY(AudioFileInfo::fileDialogFilter().contains(
            QStringLiteral("*.%1").arg(QFileInfo(file).suffix().toLower())));
    }
    QVERIFY(!AudioFileInfo::isSupportedFile(QStringLiteral("sample.txt")));
}

void AudioFileInfoTest::bundledProbeReadsWav()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString inputPath = createWavFile(directory, QStringLiteral("bundled probe.wav"));
    QVERIFY(!inputPath.isEmpty());

    AudioFileInfo info;
    info.setPythonProgram(pythonProgram());
    QVERIFY2(QFileInfo(info.probeScript()).isFile(), qPrintable(info.probeScript()));
    QSignalSpy successSpy(&info, &AudioFileInfo::inspectionSucceeded);
    QSignalSpy failureSpy(&info, &AudioFileInfo::inspectionFailed);

    info.inspect(inputPath);
    QTRY_VERIFY_WITH_TIMEOUT(successSpy.count() + failureSpy.count() > 0, 5000);
    if (!failureSpy.isEmpty()) {
        QFAIL(qPrintable(failureSpy.first().at(1).toString()));
    }
    QCOMPARE(successSpy.count(), 1);
    const AudioFileMetadata metadata =
        qvariant_cast<AudioFileMetadata>(successSpy.takeFirst().at(0));
    QCOMPARE(metadata.durationMilliseconds, qint64(200));
}

void AudioFileInfoTest::successWithSpacesAndKoreanPath()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString inputPath = createFile(directory, QStringLiteral("회의 녹음 파일.wav"));
    QVERIFY(!inputPath.isEmpty());

    AudioFileInfo info;
    info.setPythonProgram(pythonProgram());
    info.setProbeScript(QFINDTESTDATA("fixtures/mock_audio_metadata.py"));
    QSignalSpy successSpy(&info, &AudioFileInfo::inspectionSucceeded);
    QSignalSpy failureSpy(&info, &AudioFileInfo::inspectionFailed);

    info.inspect(inputPath);
    QTRY_VERIFY_WITH_TIMEOUT(successSpy.count() + failureSpy.count() > 0, 5000);
    if (!failureSpy.isEmpty()) {
        QFAIL(qPrintable(failureSpy.first().at(1).toString()));
    }
    QCOMPARE(successSpy.count(), 1);
    QCOMPARE(failureSpy.count(), 0);
    const AudioFileMetadata metadata =
        qvariant_cast<AudioFileMetadata>(successSpy.takeFirst().at(0));
    QCOMPARE(QFileInfo(metadata.filePath).canonicalFilePath(),
             QFileInfo(inputPath).canonicalFilePath());
    QCOMPARE(metadata.fileName, QStringLiteral("회의 녹음 파일.wav"));
    QCOMPARE(metadata.sizeBytes, qint64(7));
    QCOMPARE(metadata.durationMilliseconds, qint64(65250));
}

void AudioFileInfoTest::decodeFailure()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString inputPath = createFile(directory, QStringLiteral("corrupt.wav"));

    AudioFileInfo info;
    info.setPythonProgram(pythonProgram());
    info.setProbeScript(QFINDTESTDATA("fixtures/mock_audio_metadata.py"));
    QSignalSpy failureSpy(&info, &AudioFileInfo::inspectionFailed);

    info.inspect(inputPath);
    QTRY_COMPARE_WITH_TIMEOUT(failureSpy.count(), 1, 5000);
    QVERIFY(failureSpy.takeFirst().at(1).toString().contains(QStringLiteral("decode failure")));
}

void AudioFileInfoTest::missingFile()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());

    AudioFileInfo info;
    info.setProbeScript(QFINDTESTDATA("fixtures/mock_audio_metadata.py"));
    QSignalSpy failureSpy(&info, &AudioFileInfo::inspectionFailed);

    info.inspect(directory.filePath(QStringLiteral("deleted.wav")));
    QCOMPARE(failureSpy.count(), 1);
    QVERIFY(failureSpy.takeFirst().at(1).toString().contains(QStringLiteral("찾을 수 없습니다")));
}

void AudioFileInfoTest::newerSelectionWins()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString slowPath = createFile(directory, QStringLiteral("slow.wav"));
    const QString latestPath = createFile(directory, QStringLiteral("latest.wav"));

    AudioFileInfo info;
    info.setPythonProgram(pythonProgram());
    info.setProbeScript(QFINDTESTDATA("fixtures/mock_audio_metadata.py"));
    QSignalSpy successSpy(&info, &AudioFileInfo::inspectionSucceeded);
    QSignalSpy failureSpy(&info, &AudioFileInfo::inspectionFailed);

    info.inspect(slowPath);
    info.inspect(latestPath);
    QTRY_COMPARE_WITH_TIMEOUT(successSpy.count(), 1, 5000);
    QTest::qWait(800);
    QCOMPARE(successSpy.count(), 1);
    QCOMPARE(failureSpy.count(), 0);
    const AudioFileMetadata metadata =
        qvariant_cast<AudioFileMetadata>(successSpy.takeFirst().at(0));
    QCOMPARE(QFileInfo(metadata.filePath).canonicalFilePath(),
             QFileInfo(latestPath).canonicalFilePath());
}

QTEST_MAIN(AudioFileInfoTest)

#include "tst_audio_file_info.moc"
