#ifndef TRANSCRIBEOPTIONS_H
#define TRANSCRIBEOPTIONS_H

#include <QString>
#include <QStringList>

#include <optional>

enum class BackendDevice
{
    Auto,
    Npu,
    Cpu,
    Gpu
};

inline QString backendDeviceArgument(BackendDevice device)
{
    switch (device) {
    case BackendDevice::Auto:
        return QStringLiteral("AUTO");
    case BackendDevice::Npu:
        return QStringLiteral("NPU");
    case BackendDevice::Cpu:
        return QStringLiteral("CPU");
    case BackendDevice::Gpu:
        return QStringLiteral("GPU");
    }
    return QStringLiteral("AUTO");
}

struct TranscribeOptions
{
    QString inputFile;
    BackendDevice device = BackendDevice::Auto;
    bool diarizationEnabled = false;
    // No value means Auto. The GUI contract otherwise permits 2, 3, 4, or 5.
    std::optional<int> speakerCount;

    QString outputDirectory;
    QString outputSuffix;
    QString modelDirectory;
    QString modelLabel;
    int beams = 1;
    QString language = QStringLiteral("<|ko|>");
    double windowSeconds = 120.0;
    double overlapSeconds = 4.0;
    QString hotwordsFile;
    QString initialPromptFile;

    QString diarizationSegmentationModel;
    QString diarizationEmbeddingModel;
    BackendDevice diarizationDevice = BackendDevice::Npu;
    bool diarizationFallbackToCpu = true;
    double speakerThreshold = 0.5;

    bool isValid(QString *errorMessage = nullptr) const
    {
        const auto fail = [errorMessage](const QString &message) {
            if (errorMessage) {
                *errorMessage = message;
            }
            return false;
        };

        if (inputFile.trimmed().isEmpty()) {
            return fail(QStringLiteral("inputFile must not be empty"));
        }
        if (modelDirectory.trimmed().isEmpty()) {
            return fail(QStringLiteral("modelDirectory must not be empty"));
        }
        if (beams < 1) {
            return fail(QStringLiteral("beams must be at least 1"));
        }
        if (windowSeconds < 30.0) {
            return fail(QStringLiteral("windowSeconds must be at least 30"));
        }
        if (overlapSeconds < 0.0 || overlapSeconds >= windowSeconds / 2.0) {
            return fail(QStringLiteral("overlapSeconds is outside the supported range"));
        }
        if (speakerCount && *speakerCount != 2 && *speakerCount != 3
                && *speakerCount != 4 && *speakerCount != 5) {
            return fail(QStringLiteral("speakerCount must be Auto, 2, 3, 4, or 5"));
        }
        if (speakerThreshold <= 0.0 || speakerThreshold > 1.0) {
            return fail(QStringLiteral("speakerThreshold must be in (0, 1]"));
        }
        if (diarizationDevice == BackendDevice::Auto) {
            return fail(QStringLiteral("diarizationDevice must be NPU, CPU, or GPU"));
        }
        if (errorMessage) {
            errorMessage->clear();
        }
        return true;
    }

    QStringList toBridgeArguments() const
    {
        QStringList arguments {
            QStringLiteral("--input"), inputFile,
            QStringLiteral("--device"), backendDeviceArgument(device),
            QStringLiteral("--model-dir"), modelDirectory,
            QStringLiteral("--beams"), QString::number(beams),
            QStringLiteral("--language"), language,
            QStringLiteral("--window-seconds"), QString::number(windowSeconds, 'g', 15),
            QStringLiteral("--overlap-seconds"), QString::number(overlapSeconds, 'g', 15)
        };

        if (!outputDirectory.isEmpty()) {
            arguments << QStringLiteral("--output-dir") << outputDirectory;
        }
        if (!outputSuffix.isEmpty()) {
            arguments << QStringLiteral("--output-suffix") << outputSuffix;
        }
        if (!modelLabel.isEmpty()) {
            arguments << QStringLiteral("--model-label") << modelLabel;
        }
        if (!hotwordsFile.isEmpty()) {
            arguments << QStringLiteral("--hotwords-file") << hotwordsFile;
        }
        if (!initialPromptFile.isEmpty()) {
            arguments << QStringLiteral("--initial-prompt-file") << initialPromptFile;
        }

        if (diarizationEnabled) {
            arguments << QStringLiteral("--diarization")
                      << QStringLiteral("--num-speakers")
                      << (speakerCount ? QString::number(*speakerCount) : QStringLiteral("auto"))
                      << QStringLiteral("--speaker-threshold")
                      << QString::number(speakerThreshold, 'g', 15)
                      << QStringLiteral("--diarization-device")
                      << backendDeviceArgument(diarizationDevice);
            if (!diarizationSegmentationModel.isEmpty()) {
                arguments << QStringLiteral("--diarization-segmentation-model")
                          << diarizationSegmentationModel;
            }
            if (!diarizationEmbeddingModel.isEmpty()) {
                arguments << QStringLiteral("--diarization-embedding-model")
                          << diarizationEmbeddingModel;
            }
            if (!diarizationFallbackToCpu) {
                arguments << QStringLiteral("--diarization-no-fallback");
            }
        }
        return arguments;
    }
};

#endif // TRANSCRIBEOPTIONS_H
