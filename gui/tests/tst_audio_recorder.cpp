#include "audio/AudioRecorder.h"

#include <QAudioFormat>
#include <QTemporaryDir>
#include <QTest>

#include <cmath>
#include <cstring>
#include <limits>

namespace {
QAudioFormat audioFormat(int sampleRate = 48000,
                         int channelCount = 1,
                         QAudioFormat::SampleFormat sampleFormat = QAudioFormat::Int16)
{
    QAudioFormat format;
    format.setSampleRate(sampleRate);
    format.setChannelCount(channelCount);
    format.setSampleFormat(sampleFormat);
    return format;
}

template<typename Sample>
QByteArray sampleBytes(std::initializer_list<Sample> samples)
{
    QByteArray bytes(int(samples.size() * sizeof(Sample)), Qt::Uninitialized);
    std::memcpy(bytes.data(), samples.begin(), size_t(bytes.size()));
    return bytes;
}
} // namespace

class AudioRecorderTest : public QObject
{
    Q_OBJECT

private slots:
    void exposesSafeInitialStateAndDeviceSelection();
    void outputDirectoryCanOnlyBeChangedToANonEmptyPath();
    void int16LevelHandlesSilenceAndExtremes();
    void floatLevelIsNormalizedAndFinite();
    void durationUsesTheActualCaptureFormat();
};

void AudioRecorderTest::exposesSafeInitialStateAndDeviceSelection()
{
    AudioRecorder recorder;
    QVERIFY(!recorder.isRecording());
    QVERIFY(recorder.outputFile().isEmpty());
    QVERIFY(!recorder.outputDirectory().isEmpty());

    const QList<AudioInputDevice> devices = recorder.inputDevices();
    if (devices.isEmpty()) {
        QVERIFY(recorder.selectedInputDeviceId().isEmpty());
        QVERIFY(!recorder.setInputDevice(QByteArrayLiteral("missing")));
        return;
    }

    QVERIFY(!recorder.selectedInputDeviceId().isEmpty());
    QVERIFY(recorder.setInputDevice(devices.constLast().id));
    QCOMPARE(recorder.selectedInputDeviceId(), devices.constLast().id);
    QVERIFY(!recorder.setInputDevice(QByteArrayLiteral("missing")));
}

void AudioRecorderTest::outputDirectoryCanOnlyBeChangedToANonEmptyPath()
{
    AudioRecorder recorder;
    QTemporaryDir directory;
    QVERIFY(directory.isValid());

    QVERIFY(!recorder.setOutputDirectory(QString()));
    QVERIFY(recorder.setOutputDirectory(directory.path()));
    QCOMPARE(recorder.outputDirectory(), directory.path());
}

void AudioRecorderTest::int16LevelHandlesSilenceAndExtremes()
{
    const QAudioFormat format = audioFormat();
    QCOMPARE(AudioRecorder::normalizedLevel(
                 format, sampleBytes<qint16>({0, 0, 0, 0})),
             0.0f);

    const float positiveMaximum = AudioRecorder::normalizedLevel(
        format, sampleBytes<qint16>({std::numeric_limits<qint16>::max()}));
    QVERIFY(std::abs(positiveMaximum - 32767.0f / 32768.0f) < 0.00001f);

    QCOMPARE(AudioRecorder::normalizedLevel(
                 format,
                 sampleBytes<qint16>({std::numeric_limits<qint16>::min()})),
             1.0f);
}

void AudioRecorderTest::floatLevelIsNormalizedAndFinite()
{
    const QAudioFormat format = audioFormat(44100, 2, QAudioFormat::Float);
    QCOMPARE(AudioRecorder::normalizedLevel(
                 format, sampleBytes<float>({0.0f, -0.25f, 0.5f, 1.5f})),
             1.0f);

    QCOMPARE(AudioRecorder::normalizedLevel(
                 format,
                 sampleBytes<float>({std::numeric_limits<float>::quiet_NaN(), 0.2f})),
             0.2f);
}

void AudioRecorderTest::durationUsesTheActualCaptureFormat()
{
    const QAudioFormat stereo48k = audioFormat(48000, 2);
    QCOMPARE(AudioRecorder::durationForBytes(stereo48k, 48000u * 2u * 2u),
             qint64(1000));
    QCOMPARE(AudioRecorder::durationForBytes(stereo48k, 48000u * 2u * 2u + 3u),
             qint64(1000));

    QAudioFormat invalid;
    QCOMPARE(AudioRecorder::durationForBytes(invalid, 1234), qint64(0));
}

QTEST_GUILESS_MAIN(AudioRecorderTest)

#include "tst_audio_recorder.moc"
