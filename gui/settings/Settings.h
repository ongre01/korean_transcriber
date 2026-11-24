#ifndef SETTINGS_H
#define SETTINGS_H

#include <QByteArray>
#include <QString>

class EnginePaths;

class Settings
{
public:
    static Settings load();
    void save() const;
    void applyEngineDefaults(const EnginePaths &paths, const QString &pythonOverride = {});
    // Resolve the default command on PATH, then the Windows per-user install.
    // Explicit user paths are preserved; call again before launch after an install.
    QString resolvedNemotronExecutable() const;
    // An empty model selection can use the installed, nonempty decoder/encoder pair.
    // Explicit user paths are preserved, including paths that are unavailable.
    QString resolvedNemotronModelFile() const;

    bool validateForRun(bool diarizationEnabled, QString *errorMessage = nullptr) const;

    QString pythonPath;
    QString lastInputDirectory;
    QString outputDirectory;
    QByteArray selectedMicrophoneId;
    QString transcriptionEngine = QStringLiteral("whisper");
    QString selectedDevice = QStringLiteral("AUTO");
    bool diarizationEnabled = false;
    QString speakerCount = QStringLiteral("Auto");

    QString nemotronExecutable = QStringLiteral("nemo-speech");
    QString nemotronModelFile;
    QString nemotronConfigFile;

    // Whisper-only options are retained when another engine is selected.
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
