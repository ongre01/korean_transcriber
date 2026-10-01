#include "AudioRecorder.h"

#include "WavWriter.h"

#include <QCoreApplication>
#include <QDateTime>
#include <QDir>
#include <QFileInfo>
#include <QIODevice>
#include <QStandardPaths>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>

#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
#include <QAudioDevice>
#include <QAudioSource>
#include <QMediaDevices>
#if QT_VERSION >= QT_VERSION_CHECK(6, 5, 0) && QT_CONFIG(permissions)
#include <QPermissions>
#endif
#else
#include <QAudioDeviceInfo>
#include <QAudioInput>
#endif

namespace {
int bytesPerSample(const QAudioFormat &format)
{
#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
    return format.bytesPerSample();
#else
    return format.sampleSize() > 0 ? format.sampleSize() / 8 : 0;
#endif
}

int bytesPerFrame(const QAudioFormat &format)
{
    const int sampleBytes = bytesPerSample(format);
    if (sampleBytes <= 0 || format.channelCount() <= 0) {
        return 0;
    }
    return sampleBytes * format.channelCount();
}

template<typename Integer>
Integer unalignedSample(const char *data)
{
    Integer value{};
    std::memcpy(&value, data, sizeof(value));
    return value;
}

float peakLevel(const QAudioFormat &format, const char *data, qint64 byteCount)
{
    if (!data || byteCount <= 0) {
        return 0.0f;
    }

    const int sampleBytes = bytesPerSample(format);
    if (sampleBytes <= 0) {
        return 0.0f;
    }

    const qint64 sampleCount = byteCount / sampleBytes;
    double peak = 0.0;

#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
    switch (format.sampleFormat()) {
    case QAudioFormat::UInt8:
        for (qint64 index = 0; index < sampleCount; ++index) {
            const int sample = static_cast<unsigned char>(data[index]);
            peak = std::max(peak, std::abs(sample - 128) / 128.0);
        }
        break;
    case QAudioFormat::Int16:
        for (qint64 index = 0; index < sampleCount; ++index) {
            const qint16 sample = unalignedSample<qint16>(data + index * 2);
            peak = std::max(peak, std::abs(static_cast<double>(sample)) / 32768.0);
        }
        break;
    case QAudioFormat::Int32:
        for (qint64 index = 0; index < sampleCount; ++index) {
            const qint32 sample = unalignedSample<qint32>(data + index * 4);
            peak = std::max(peak,
                            std::abs(static_cast<double>(sample)) / 2147483648.0);
        }
        break;
    case QAudioFormat::Float:
        for (qint64 index = 0; index < sampleCount; ++index) {
            const float sample = unalignedSample<float>(data + index * 4);
            if (std::isfinite(sample)) {
                peak = std::max(peak, std::abs(static_cast<double>(sample)));
            }
        }
        break;
    default:
        return 0.0f;
    }
#else
    if (format.sampleType() == QAudioFormat::UnSignedInt && format.sampleSize() == 8) {
        for (qint64 index = 0; index < sampleCount; ++index) {
            const int sample = static_cast<unsigned char>(data[index]);
            peak = std::max(peak, std::abs(sample - 128) / 128.0);
        }
    } else if (format.sampleType() == QAudioFormat::SignedInt
               && format.sampleSize() == 16) {
        for (qint64 index = 0; index < sampleCount; ++index) {
            const qint16 sample = unalignedSample<qint16>(data + index * 2);
            peak = std::max(peak, std::abs(static_cast<double>(sample)) / 32768.0);
        }
    } else if (format.sampleType() == QAudioFormat::SignedInt
               && format.sampleSize() == 32) {
        for (qint64 index = 0; index < sampleCount; ++index) {
            const qint32 sample = unalignedSample<qint32>(data + index * 4);
            peak = std::max(peak,
                            std::abs(static_cast<double>(sample)) / 2147483648.0);
        }
    } else if (format.sampleType() == QAudioFormat::Float
               && format.sampleSize() == 32) {
        for (qint64 index = 0; index < sampleCount; ++index) {
            const float sample = unalignedSample<float>(data + index * 4);
            if (std::isfinite(sample)) {
                peak = std::max(peak, std::abs(static_cast<double>(sample)));
            }
        }
    }
#endif

    return static_cast<float>(std::clamp(peak, 0.0, 1.0));
}

QString defaultRecordingDirectory()
{
    QString base = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    if (base.isEmpty()) {
        base = QDir::currentPath();
    }
    return QDir(base).filePath(QStringLiteral("recordings"));
}

QString audioErrorMessage(int error)
{
    switch (static_cast<QAudio::Error>(error)) {
    case QAudio::OpenError:
        return AudioRecorder::tr("선택한 마이크를 초기화할 수 없습니다.");
    case QAudio::IOError:
        return AudioRecorder::tr("마이크 입력을 읽을 수 없거나 장치가 제거되었습니다.");
    case QAudio::FatalError:
        return AudioRecorder::tr("마이크 녹음 중 복구할 수 없는 오류가 발생했습니다.");
#if QT_VERSION < QT_VERSION_CHECK(6, 11, 0)
    case QAudio::UnderrunError:
        return AudioRecorder::tr("마이크의 오디오 데이터를 제때 처리하지 못했습니다.");
#endif
    case QAudio::NoError:
        break;
    }
    return AudioRecorder::tr("마이크 녹음이 예기치 않게 중지되었습니다.");
}
} // namespace

class AudioRecorder::CaptureSink final : public QIODevice
{
public:
    explicit CaptureSink(AudioRecorder *recorder)
        : QIODevice(recorder)
        , m_recorder(recorder)
    {
    }

protected:
    qint64 readData(char *, qint64) override
    {
        return -1;
    }

    qint64 writeData(const char *data, qint64 maxSize) override
    {
        if (!m_recorder || !m_recorder->m_writer) {
            setErrorString(QStringLiteral("No WAV writer is available."));
            return -1;
        }

        const qint64 written = m_recorder->m_writer->write(data, maxSize);
        if (written != maxSize) {
            setErrorString(m_recorder->m_writer->errorString());
            return -1;
        }

        m_recorder->captured(data, written);
        return written;
    }

private:
    AudioRecorder *m_recorder;
};

struct AudioRecorder::PlatformState
{
#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
    QMediaDevices *mediaDevices = nullptr;
    std::unique_ptr<QAudioSource> source;
#else
    std::unique_ptr<QAudioInput> source;
#endif
};

AudioRecorder::AudioRecorder(QObject *parent)
    : QObject(parent)
    , m_platform(std::make_unique<PlatformState>())
    , m_outputDirectory(defaultRecordingDirectory())
{
#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
    m_platform->mediaDevices = new QMediaDevices(this);
    connect(m_platform->mediaDevices, &QMediaDevices::audioInputsChanged,
            this, &AudioRecorder::refreshInputDevices);
#endif
    refreshInputDevices();
}

AudioRecorder::~AudioRecorder()
{
    m_startPending = false;
    if (m_recording) {
        m_stopping = true;
        if (m_platform->source) {
            m_platform->source->stop();
        }
        disposeCapture(false);
    } else {
        disposeCapture(true);
    }
}

QList<AudioInputDevice> AudioRecorder::inputDevices() const
{
    return m_inputDevices;
}

QByteArray AudioRecorder::selectedInputDeviceId() const
{
    return m_selectedDeviceId;
}

bool AudioRecorder::setInputDevice(const QByteArray &deviceId)
{
    if (m_recording || m_startPending) {
        return false;
    }

    const auto device = std::find_if(
        m_inputDevices.cbegin(), m_inputDevices.cend(),
        [&deviceId](const AudioInputDevice &candidate) {
            return candidate.id == deviceId;
        });
    if (device == m_inputDevices.cend()) {
        return false;
    }

    m_selectedDeviceId = deviceId;
    return true;
}

QString AudioRecorder::outputDirectory() const
{
    return m_outputDirectory;
}

bool AudioRecorder::setOutputDirectory(const QString &directory)
{
    if (m_recording || m_startPending || directory.trimmed().isEmpty()) {
        return false;
    }
    m_outputDirectory = QDir::cleanPath(QFileInfo(directory).absoluteFilePath());
    return true;
}

bool AudioRecorder::startRecording()
{
    if (m_recording || m_startPending) {
        return false;
    }

    refreshInputDevices();
    if (m_inputDevices.isEmpty()) {
        emit errorOccurred(tr("사용 가능한 마이크가 없습니다."));
        return false;
    }

    const auto selected = std::find_if(
        m_inputDevices.cbegin(), m_inputDevices.cend(),
        [this](const AudioInputDevice &device) {
            return device.id == m_selectedDeviceId;
        });
    if (selected == m_inputDevices.cend()) {
        emit errorOccurred(tr("선택한 마이크를 찾을 수 없습니다."));
        return false;
    }

#if QT_VERSION >= QT_VERSION_CHECK(6, 5, 0) && QT_CONFIG(permissions)
    QCoreApplication *application = QCoreApplication::instance();
    if (!application) {
        emit errorOccurred(tr("마이크 권한을 확인할 애플리케이션이 없습니다."));
        return false;
    }

    const QMicrophonePermission microphonePermission;
    const Qt::PermissionStatus permissionStatus =
        application->checkPermission(microphonePermission);
    if (permissionStatus == Qt::PermissionStatus::Denied) {
        emit errorOccurred(tr("마이크 접근 권한이 거부되었습니다."));
        return false;
    }
    if (permissionStatus == Qt::PermissionStatus::Undetermined) {
        m_startPending = true;
        application->requestPermission(
            microphonePermission, this,
            [this](QPermission permission) {
                if (!m_startPending) {
                    return;
                }
                m_startPending = false;
                if (permission.status() != Qt::PermissionStatus::Granted) {
                    emit errorOccurred(tr("마이크 접근 권한이 거부되었습니다."));
                    return;
                }
                beginCapture();
            });
        return true;
    }
#endif

    return beginCapture();
}

void AudioRecorder::stopRecording()
{
    if (m_startPending) {
        m_startPending = false;
    }
    if (!m_recording) {
        return;
    }

    m_stopping = true;
    const int captureError = m_platform->source
        ? static_cast<int>(m_platform->source->error())
        : static_cast<int>(QAudio::NoError);
    if (m_platform->source) {
        m_platform->source->stop();
    }
    if (m_sink && m_sink->isOpen()) {
        m_sink->close();
    }

    const qint64 finalTime = durationForBytes(
        m_captureFormat, m_writer ? m_writer->dataSize() : 0);
    if (finalTime != m_lastTimeMilliseconds) {
        m_lastTimeMilliseconds = finalTime;
        emit recordingTimeChanged(finalTime);
    }
    emit recordingLevelChanged(0.0f);

    if (captureError != static_cast<int>(QAudio::NoError)) {
        const QString message = audioErrorMessage(captureError);
        m_recording = false;
        disposeCapture(true);
        m_outputFile.clear();
        m_stopping = false;
        emit errorOccurred(message);
        return;
    }

    const QString completedFile = m_outputFile;
    bool finalized = false;
    QString failure;
    if (m_writer) {
        finalized = m_writer->finalize();
        if (!finalized) {
            failure = m_writer->errorString();
        }
    }

    m_recording = false;
    disposeCapture(!finalized);
    m_stopping = false;

    if (!finalized) {
        m_outputFile.clear();
        emit errorOccurred(tr("WAV 파일을 마무리할 수 없습니다: %1").arg(failure));
        return;
    }

    emit recordingStopped(completedFile);
}

bool AudioRecorder::isRecording() const noexcept
{
    return m_recording;
}

QString AudioRecorder::outputFile() const
{
    return m_outputFile;
}

QAudioFormat AudioRecorder::captureFormat() const
{
    return m_captureFormat;
}

float AudioRecorder::normalizedLevel(const QAudioFormat &format,
                                     const QByteArray &pcmBytes)
{
    return peakLevel(format, pcmBytes.constData(), pcmBytes.size());
}

qint64 AudioRecorder::durationForBytes(const QAudioFormat &format,
                                       quint64 byteCount)
{
    const int frameBytes = bytesPerFrame(format);
    if (frameBytes <= 0 || format.sampleRate() <= 0) {
        return 0;
    }

    const quint64 frameCount = byteCount / static_cast<quint64>(frameBytes);
    return static_cast<qint64>(frameCount * 1000u
                               / static_cast<quint64>(format.sampleRate()));
}

void AudioRecorder::refreshInputDevices()
{
    QList<AudioInputDevice> devices;

#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
    const QList<QAudioDevice> platformDevices = QMediaDevices::audioInputs();
    devices.reserve(platformDevices.size());
    for (const QAudioDevice &device : platformDevices) {
        devices.append({device.id(), device.description(), device.isDefault()});
    }
#else
    const QList<QAudioDeviceInfo> platformDevices =
        QAudioDeviceInfo::availableDevices(QAudio::AudioInput);
    devices.reserve(platformDevices.size());
    const QString defaultName = QAudioDeviceInfo::defaultInputDevice().deviceName();
    for (const QAudioDeviceInfo &device : platformDevices) {
        const QString name = device.deviceName();
        devices.append({name.toUtf8(), name, name == defaultName});
    }
#endif

    m_inputDevices = devices;
    const bool selectionStillExists = std::any_of(
        devices.cbegin(), devices.cend(), [this](const AudioInputDevice &device) {
            return device.id == m_selectedDeviceId;
        });
    if (!selectionStillExists) {
        m_selectedDeviceId.clear();
        const auto defaultDevice = std::find_if(
            devices.cbegin(), devices.cend(), [](const AudioInputDevice &device) {
                return device.isDefault;
            });
        if (defaultDevice != devices.cend()) {
            m_selectedDeviceId = defaultDevice->id;
        } else if (!devices.isEmpty()) {
            m_selectedDeviceId = devices.constFirst().id;
        }
    }

    emit inputDevicesChanged();

    if (m_recording) {
        const bool activeDeviceExists = std::any_of(
            devices.cbegin(), devices.cend(), [this](const AudioInputDevice &device) {
                return device.id == m_activeDeviceId;
            });
        if (!activeDeviceExists) {
            failRecording(tr("녹음 중인 마이크가 제거되었습니다."));
        }
    }
}

bool AudioRecorder::beginCapture()
{
    if (m_recording || m_startPending) {
        return false;
    }

#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
    QAudioDevice selectedDevice;
    const QList<QAudioDevice> devices = QMediaDevices::audioInputs();
    for (const QAudioDevice &device : devices) {
        if (device.id() == m_selectedDeviceId) {
            selectedDevice = device;
            break;
        }
    }
    if (selectedDevice.isNull()) {
        emit errorOccurred(tr("선택한 마이크를 찾을 수 없습니다."));
        return false;
    }

    QAudioFormat preferred = selectedDevice.preferredFormat();
    QAudioFormat format = preferred;
    QAudioFormat monoInt16 = preferred;
    monoInt16.setChannelCount(1);
    monoInt16.setSampleFormat(QAudioFormat::Int16);
    if (selectedDevice.isFormatSupported(monoInt16)) {
        format = monoInt16;
    }
#else
    QAudioDeviceInfo selectedDevice;
    const QList<QAudioDeviceInfo> devices =
        QAudioDeviceInfo::availableDevices(QAudio::AudioInput);
    for (const QAudioDeviceInfo &device : devices) {
        if (device.deviceName().toUtf8() == m_selectedDeviceId) {
            selectedDevice = device;
            break;
        }
    }
    if (selectedDevice.isNull()) {
        emit errorOccurred(tr("선택한 마이크를 찾을 수 없습니다."));
        return false;
    }

    QAudioFormat preferred = selectedDevice.preferredFormat();
    QAudioFormat format = preferred;
    QAudioFormat monoInt16 = preferred;
    monoInt16.setChannelCount(1);
    monoInt16.setCodec(QStringLiteral("audio/pcm"));
    monoInt16.setSampleSize(16);
    monoInt16.setByteOrder(QAudioFormat::LittleEndian);
    monoInt16.setSampleType(QAudioFormat::SignedInt);
    if (selectedDevice.isFormatSupported(monoInt16)) {
        format = monoInt16;
    }
#endif

    QString unsupportedReason;
    if (!WavWriter::isFormatSupported(format, &unsupportedReason)) {
        emit errorOccurred(tr("마이크의 캡처 형식을 WAV로 저장할 수 없습니다: %1")
                               .arg(unsupportedReason));
        return false;
    }

    QDir directory(m_outputDirectory);
    if (!directory.exists() && !directory.mkpath(QStringLiteral("."))) {
        emit errorOccurred(tr("녹음 폴더를 만들 수 없습니다: %1")
                               .arg(QDir::toNativeSeparators(m_outputDirectory)));
        return false;
    }

    m_outputFile = nextOutputFile();
    m_writer = std::make_unique<WavWriter>();
    if (!m_writer->open(m_outputFile, format)) {
        const QString error = m_writer->errorString();
        m_writer.reset();
        m_outputFile.clear();
        emit errorOccurred(error);
        return false;
    }

    m_sink = std::make_unique<CaptureSink>(this);
    if (!m_sink->open(QIODevice::WriteOnly)) {
        const QString error = m_sink->errorString();
        disposeCapture(true);
        m_outputFile.clear();
        emit errorOccurred(tr("오디오 캡처 스트림을 열 수 없습니다: %1").arg(error));
        return false;
    }

#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
    m_platform->source = std::make_unique<QAudioSource>(selectedDevice, format);
#else
    m_platform->source = std::make_unique<QAudioInput>(selectedDevice, format);
#endif
    const quint64 captureGeneration = ++m_captureGeneration;
#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
    connect(m_platform->source.get(), &QAudioSource::stateChanged,
            this, [this, captureGeneration](QAudio::State state) {
                if (m_captureGeneration != captureGeneration) {
                    return;
                }
                captureStateChanged(static_cast<int>(state));
            }, Qt::QueuedConnection);
#else
    connect(m_platform->source.get(), &QAudioInput::stateChanged,
            this, [this, captureGeneration](QAudio::State state) {
                if (m_captureGeneration != captureGeneration) {
                    return;
                }
                captureStateChanged(static_cast<int>(state));
            }, Qt::QueuedConnection);
#endif

    m_captureFormat = format;
    m_activeDeviceId = m_selectedDeviceId;
    m_lastTimeMilliseconds = 0;
    m_recording = true;
    m_platform->source->start(m_sink.get());
    if (m_platform->source->error() != QAudio::NoError) {
        const QString message = audioErrorMessage(
            static_cast<int>(m_platform->source->error()));
        failRecording(message);
        return false;
    }

    emit recordingTimeChanged(0);
    emit recordingLevelChanged(0.0f);
    emit recordingStarted();
    return true;
}

void AudioRecorder::captured(const char *data, qint64 byteCount)
{
    if (!m_recording || !m_writer || byteCount <= 0) {
        return;
    }

    emit recordingLevelChanged(peakLevel(m_captureFormat, data, byteCount));

    const qint64 milliseconds = durationForBytes(m_captureFormat, m_writer->dataSize());
    if (milliseconds != m_lastTimeMilliseconds) {
        m_lastTimeMilliseconds = milliseconds;
        emit recordingTimeChanged(milliseconds);
    }
}

void AudioRecorder::captureStateChanged(int state)
{
    if (!m_recording || m_stopping || state != static_cast<int>(QAudio::StoppedState)) {
        return;
    }

    const int error = m_platform->source
        ? static_cast<int>(m_platform->source->error())
        : static_cast<int>(QAudio::FatalError);
    failRecording(audioErrorMessage(error));
}

void AudioRecorder::failRecording(const QString &message)
{
    if (!m_recording) {
        emit errorOccurred(message);
        return;
    }

    m_stopping = true;
    if (m_platform->source) {
        m_platform->source->stop();
    }
    m_recording = false;
    disposeCapture(true);
    m_outputFile.clear();
    m_stopping = false;
    emit recordingLevelChanged(0.0f);
    emit errorOccurred(message);
}

void AudioRecorder::disposeCapture(bool abortWriter)
{
    if (m_sink && m_sink->isOpen()) {
        m_sink->close();
    }
    m_sink.reset();

    if (m_writer) {
        if (abortWriter) {
            m_writer->abort();
        } else if (!m_writer->isFinalized()) {
            m_writer->finalize();
        }
        m_writer.reset();
    }

    m_platform->source.reset();
    m_activeDeviceId.clear();
}

QString AudioRecorder::nextOutputFile() const
{
    const QString stem = QStringLiteral("recording_%1")
                             .arg(QDateTime::currentDateTime().toString(
                                 QStringLiteral("yyyyMMdd_HHmmss")));
    QDir directory(m_outputDirectory);
    QString candidate = directory.filePath(stem + QStringLiteral(".wav"));
    int suffix = 1;
    while (QFileInfo::exists(candidate)) {
        candidate = directory.filePath(
            QStringLiteral("%1_%2.wav").arg(stem).arg(suffix++, 3, 10, QLatin1Char('0')));
    }
    return QFileInfo(candidate).absoluteFilePath();
}
