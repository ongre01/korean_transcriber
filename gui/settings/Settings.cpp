#include "Settings.h"

#include <QDir>
#include <QFileInfo>
#include <QObject>
#include <QSettings>
#include <QStandardPaths>

#include <cmath>

namespace {
constexpr auto kSettingsGroup = "Settings";

QString defaultOutputDirectory()
{
    QString base = QStandardPaths::writableLocation(QStandardPaths::DocumentsLocation);
    if (base.isEmpty()) {
        base = QDir::homePath();
    }
    return QDir(base).filePath(QStringLiteral("AudioTranscriber"));
}

double readFiniteDouble(const QSettings &settings, const QString &key, double fallback)
{
    bool ok = false;
    const double value = settings.value(key, fallback).toDouble(&ok);
    return ok && std::isfinite(value) ? value : fallback;
}

bool isPythonProgramAvailable(const QString &program)
{
    const QFileInfo file(program);
    if (file.isAbsolute() || program.contains(QLatin1Char('/'))
        || program.contains(QLatin1Char('\\'))) {
        return file.isFile();
    }
    return !QStandardPaths::findExecutable(program).isEmpty();
}
} // namespace

Settings Settings::load()
{
    Settings result;
    QSettings settings;
    settings.beginGroup(QLatin1String(kSettingsGroup));

    result.pythonPath = settings.value(QStringLiteral("pythonPath")).toString().trimmed();
    result.lastInputDirectory = settings.value(QStringLiteral("lastInputDirectory")).toString();
    result.outputDirectory = settings.value(
        QStringLiteral("outputDirectory"), defaultOutputDirectory()).toString();
    result.selectedMicrophoneId = settings.value(
        QStringLiteral("selectedMicrophoneId")).toByteArray();
    result.selectedDevice = settings.value(
        QStringLiteral("selectedDevice"), result.selectedDevice).toString().trimmed();
    result.diarizationEnabled = settings.value(
        QStringLiteral("diarizationEnabled"), result.diarizationEnabled).toBool();
    result.speakerCount = settings.value(
        QStringLiteral("speakerCount"), result.speakerCount).toString().trimmed();
    result.whisperModelDirectory = settings.value(
        QStringLiteral("whisperModelDirectory")).toString();
    result.windowSeconds = readFiniteDouble(
        settings, QStringLiteral("windowSeconds"), result.windowSeconds);
    result.overlapSeconds = readFiniteDouble(
        settings, QStringLiteral("overlapSeconds"), result.overlapSeconds);
    result.hotwordsFile = settings.value(QStringLiteral("hotwordsFile")).toString();
    result.initialPromptFile = settings.value(QStringLiteral("initialPromptFile")).toString();
    result.diarizationSegmentationModel = settings.value(
        QStringLiteral("diarizationSegmentationModel")).toString();
    result.diarizationEmbeddingModel = settings.value(
        QStringLiteral("diarizationEmbeddingModel")).toString();
    result.speakerThreshold = readFiniteDouble(
        settings, QStringLiteral("speakerThreshold"), result.speakerThreshold);
    result.minimumSpeechDuration = readFiniteDouble(
        settings, QStringLiteral("minimumSpeechDuration"), result.minimumSpeechDuration);
    result.minimumSilenceDuration = readFiniteDouble(
        settings, QStringLiteral("minimumSilenceDuration"), result.minimumSilenceDuration);
    result.diarizationDevice = settings.value(
        QStringLiteral("diarizationDevice"), result.diarizationDevice).toString().trimmed();
    result.diarizationFallbackToCpu = settings.value(
        QStringLiteral("diarizationFallbackToCpu"), result.diarizationFallbackToCpu).toBool();

    settings.endGroup();
    return result;
}

void Settings::save() const
{
    QSettings settings;
    settings.beginGroup(QLatin1String(kSettingsGroup));

    settings.setValue(QStringLiteral("pythonPath"), pythonPath.trimmed());
    settings.setValue(QStringLiteral("lastInputDirectory"), lastInputDirectory);
    settings.setValue(QStringLiteral("outputDirectory"), outputDirectory);
    settings.setValue(QStringLiteral("selectedMicrophoneId"), selectedMicrophoneId);
    settings.setValue(QStringLiteral("selectedDevice"), selectedDevice);
    settings.setValue(QStringLiteral("diarizationEnabled"), diarizationEnabled);
    settings.setValue(QStringLiteral("speakerCount"), speakerCount);
    settings.setValue(QStringLiteral("whisperModelDirectory"), whisperModelDirectory);
    settings.setValue(QStringLiteral("windowSeconds"), windowSeconds);
    settings.setValue(QStringLiteral("overlapSeconds"), overlapSeconds);
    settings.setValue(QStringLiteral("hotwordsFile"), hotwordsFile);
    settings.setValue(QStringLiteral("initialPromptFile"), initialPromptFile);
    settings.setValue(QStringLiteral("diarizationSegmentationModel"), diarizationSegmentationModel);
    settings.setValue(QStringLiteral("diarizationEmbeddingModel"), diarizationEmbeddingModel);
    settings.setValue(QStringLiteral("speakerThreshold"), speakerThreshold);
    settings.setValue(QStringLiteral("minimumSpeechDuration"), minimumSpeechDuration);
    settings.setValue(QStringLiteral("minimumSilenceDuration"), minimumSilenceDuration);
    settings.setValue(QStringLiteral("diarizationDevice"), diarizationDevice);
    settings.setValue(QStringLiteral("diarizationFallbackToCpu"), diarizationFallbackToCpu);
    settings.endGroup();
    settings.sync();
}

bool Settings::validateForRun(bool runWithDiarization, QString *errorMessage) const
{
    const auto fail = [errorMessage](const QString &message) {
        if (errorMessage) {
            *errorMessage = message;
        }
        return false;
    };

    const QString python = pythonPath.trimmed();
    if (python.isEmpty()) {
        return fail(QObject::tr("Python 실행 경로가 설정되지 않았습니다."));
    }
    if (!isPythonProgramAvailable(python)) {
        return fail(QObject::tr("Python 실행 파일을 찾을 수 없습니다: %1").arg(python));
    }
    if (!QFileInfo(whisperModelDirectory.trimmed()).isDir()) {
        return fail(QObject::tr("Whisper 모델 폴더를 찾을 수 없습니다: %1")
                        .arg(whisperModelDirectory));
    }
    if (outputDirectory.trimmed().isEmpty()) {
        return fail(QObject::tr("결과 출력 폴더가 설정되지 않았습니다."));
    }
    const QFileInfo output(outputDirectory);
    if (output.exists() && !output.isDir()) {
        return fail(QObject::tr("결과 출력 경로가 폴더가 아닙니다: %1")
                        .arg(outputDirectory));
    }
    if (!std::isfinite(windowSeconds) || windowSeconds < 30.0) {
        return fail(QObject::tr("Window Seconds는 30 이상이어야 합니다."));
    }
    if (!std::isfinite(overlapSeconds) || overlapSeconds < 0.0
        || overlapSeconds >= windowSeconds / 2.0) {
        return fail(QObject::tr("Overlap Seconds는 0 이상이고 Window Seconds의 절반보다 작아야 합니다."));
    }
    if (!hotwordsFile.trimmed().isEmpty() && !QFileInfo(hotwordsFile).isFile()) {
        return fail(QObject::tr("Hotwords 파일을 찾을 수 없습니다: %1").arg(hotwordsFile));
    }
    if (!initialPromptFile.trimmed().isEmpty() && !QFileInfo(initialPromptFile).isFile()) {
        return fail(QObject::tr("Initial Prompt 파일을 찾을 수 없습니다: %1")
                        .arg(initialPromptFile));
    }
    if (runWithDiarization) {
        if (!std::isfinite(speakerThreshold) || speakerThreshold <= 0.0
            || speakerThreshold > 1.0) {
            return fail(QObject::tr("Cluster Threshold는 0보다 크고 1 이하여야 합니다."));
        }
        if (!std::isfinite(minimumSpeechDuration) || minimumSpeechDuration < 0.0
            || !std::isfinite(minimumSilenceDuration) || minimumSilenceDuration < 0.0) {
            return fail(QObject::tr("최소 화자 지속 시간은 0 이상이어야 합니다."));
        }
        if (!diarizationSegmentationModel.trimmed().isEmpty()
            && !QFileInfo(diarizationSegmentationModel).isFile()) {
            return fail(QObject::tr("화자 분리 Segmentation 모델을 찾을 수 없습니다: %1")
                            .arg(diarizationSegmentationModel));
        }
        if (!diarizationEmbeddingModel.trimmed().isEmpty()
            && !QFileInfo(diarizationEmbeddingModel).isFile()) {
            return fail(QObject::tr("화자 분리 Embedding 모델을 찾을 수 없습니다: %1")
                            .arg(diarizationEmbeddingModel));
        }
    }

    if (errorMessage) {
        errorMessage->clear();
    }
    return true;
}
