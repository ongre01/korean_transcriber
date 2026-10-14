#ifndef ENGINEPATHS_H
#define ENGINEPATHS_H

#include <QString>

// Locate a single backend installation so scripts, models and its virtual
// environment cannot accidentally come from different working directories.
class EnginePaths
{
public:
    static EnginePaths discover();
    static EnginePaths discover(const QString &applicationDirectory,
                                const QString &sourceEngineDirectory,
                                const QString &workingDirectory);

    QString engineDirectory() const;
    QString assetPath(const QString &relativePath) const;
    QString defaultPythonProgram(const QString &environmentOverride = {}) const;
    bool isLegacyWhisperDefault(const QString &path) const;

private:
    QString m_engineDirectory;
    QString m_applicationDirectory;
    QString m_sourceEngineDirectory;
};

#endif // ENGINEPATHS_H
