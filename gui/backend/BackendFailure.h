#ifndef BACKENDFAILURE_H
#define BACKENDFAILURE_H

#include <QLatin1String>
#include <QString>

// What kind of processing a failed backend run could not complete.  The GUI
// shows one user message per kind; the raw message and stderr stay in the log.
enum class BackendFailureKind
{
    Synchronization,
    Decode,
    Conversion,
    Model,
    ResultSave,
    PythonEnvironment,
    InputFile,
    Diarization,
    Device,
    Other
};

namespace BackendFailure {

struct DocumentedPrefix
{
    BackendFailureKind kind;
    QLatin1String prefix;
};

// Error message prefixes of engine/backend_protocol.md, "실패 분류".  The
// Python contract tests compare this table with the document.
inline constexpr DocumentedPrefix documentedPrefixes[] = {
    {BackendFailureKind::Synchronization, QLatin1String("Time alignment is unresolved for")},
    {BackendFailureKind::Synchronization, QLatin1String("Multi-input time alignment failed")},
    {BackendFailureKind::Decode, QLatin1String("Multi-input audio could not be prepared.")},
    {BackendFailureKind::Decode, QLatin1String("Multi-input audio decoding failed")},
    {BackendFailureKind::Decode, QLatin1String("Audio decoding failed")},
    {BackendFailureKind::Conversion, QLatin1String("Multi-input speech detection failed")},
    {BackendFailureKind::Conversion, QLatin1String("Channel quality analysis failed")},
    {BackendFailureKind::Conversion, QLatin1String("Channel selection failed")},
    {BackendFailureKind::Conversion, QLatin1String("Merged audio could not be created")},
    {BackendFailureKind::Conversion, QLatin1String("Merged audio could not be decoded")},
    {BackendFailureKind::Conversion,
     QLatin1String("Merged audio does not match the reference recording timeline")},
    {BackendFailureKind::Conversion, QLatin1String("Multi-input pre-processing failed")},
    {BackendFailureKind::Model, QLatin1String("Model directory not found")},
    {BackendFailureKind::Model, QLatin1String("Whisper model could not be loaded")},
    {BackendFailureKind::Model, QLatin1String("Whisper generation configuration failed")},
    {BackendFailureKind::Model, QLatin1String("Initial prompt validation failed")},
    {BackendFailureKind::ResultSave, QLatin1String("Result files could not be saved")},
};

inline BackendFailureKind classify(const QString &message)
{
    const QString trimmed = message.trimmed();
    for (const DocumentedPrefix &documented : documentedPrefixes) {
        if (trimmed.startsWith(documented.prefix)) {
            return documented.kind;
        }
    }

    // Messages without a documented prefix: process start failures and the
    // single-input messages the GUI has always recognized by keyword.
    const QString normalized = trimmed.toCaseFolded();
    if (normalized.contains(QStringLiteral("required python module"))
        || normalized.contains(QStringLiteral("no module named"))
        || normalized.contains(QStringLiteral("failed to start backend"))
        || normalized.contains(QStringLiteral("python program"))) {
        return BackendFailureKind::PythonEnvironment;
    }
    if (normalized.contains(QStringLiteral("input file not found"))
        || normalized.contains(QStringLiteral("does not identify an existing file"))) {
        return BackendFailureKind::InputFile;
    }
    if (normalized.contains(QStringLiteral("audio decoding"))
        || normalized.contains(QStringLiteral("decode"))) {
        return BackendFailureKind::Decode;
    }
    if (normalized.contains(QStringLiteral("diarization"))
        || normalized.contains(QStringLiteral("segmentation"))
        || normalized.contains(QStringLiteral("embedding"))) {
        return BackendFailureKind::Diarization;
    }
    if (normalized.contains(QStringLiteral("model directory"))
        || normalized.contains(QStringLiteral("whisper model"))
        || normalized.contains(QStringLiteral("whisper pipeline"))) {
        return BackendFailureKind::Model;
    }
    if (normalized.contains(QStringLiteral("device selection"))
        || normalized.contains(QStringLiteral("openvino device"))
        || normalized.contains(QStringLiteral("npu"))) {
        return BackendFailureKind::Device;
    }
    return BackendFailureKind::Other;
}

} // namespace BackendFailure

#endif // BACKENDFAILURE_H
