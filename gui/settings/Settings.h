#ifndef SETTINGS_H
#define SETTINGS_H

#include <QByteArray>
#include <QString>

class Settings
{
public:
    static Settings load();
    void save() const;

    bool validateForRun(bool diarizationEnabled, QString *errorMessage = nullptr) const;

    QString pythonPath;
    QString lastInputDirectory;
    QString outputDirectory;
    QByteArray selectedMicrophoneId;
    QString selectedDevice = QStringLiteral("AUTO");
    bool diarizationEnabled = false;
    QString speakerCount = QStringLiteral("Auto");

    // The bridge requires a model directory. The remaining fields are optional
    // overrides: an empty path preserves the engine's built-in default.
    QString whisperModelDirectory;
    int beams = 1;
    double windowSeconds = 120.0;
    double overlapSeconds = 4.0;
    QString hotwordsFile;
    QString initialPromptFile;
    bool skipSilence = true;
    double silenceThresholdDb = -45.0;
    double silenceMinimumSpeechDuration = 0.3;
    double silenceMinimumDuration = 0.5;
    double silencePaddingDuration = 0.2;
    QString diarizationSegmentationModel;
    QString diarizationEmbeddingModel;
    double speakerThreshold = 0.5;
    double minimumSpeechDuration = 0.3;
    double minimumSilenceDuration = 0.5;
    QString diarizationDevice = QStringLiteral("NPU");
    bool diarizationFallbackToCpu = true;
};

#endif // SETTINGS_H
