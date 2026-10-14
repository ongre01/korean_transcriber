#include "EnginePaths.h"
#include "EnginePathsConfig.h"

#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QStringList>

namespace {
QString existingPath(const QString &path)
{
    const QFileInfo entry(path);
    if (!entry.exists()) {
        return {};
    }
    const QString canonical = entry.canonicalFilePath();
    return canonical.isEmpty() ? entry.absoluteFilePath() : canonical;
}

QString normalizedPath(const QString &path)
{
    const QString normalized = QDir::cleanPath(QDir::fromNativeSeparators(path));
#ifdef Q_OS_WIN
    return normalized.toCaseFolded();
#else
    return normalized;
#endif
}

QString whisperRelativePath()
{
    return QStringLiteral("models/whisper-large-v3-turbo-int8");
}
} // namespace

EnginePaths EnginePaths::discover()
{
    return discover(QCoreApplication::applicationDirPath(),
                    QString::fromUtf8(AUDIOTRANSCRIBER_SOURCE_ENGINE_DIR),
                    QDir::currentPath());
}

EnginePaths EnginePaths::discover(const QString &applicationDirectory,
                                 const QString &sourceEngineDirectory,
                                 const QString &workingDirectory)
{
    EnginePaths result;
    result.m_applicationDirectory = applicationDirectory;
    result.m_sourceEngineDirectory = sourceEngineDirectory;

    QStringList candidates;
    if (!applicationDirectory.isEmpty()) {
        QDir ancestor(applicationDirectory);
        do {
            candidates.append(ancestor.absoluteFilePath(QStringLiteral("engine")));
        } while (ancestor.cdUp());
    }
    if (!sourceEngineDirectory.isEmpty()) {
        candidates.append(sourceEngineDirectory);
    }
    if (!workingDirectory.isEmpty()) {
        candidates.append(QDir(workingDirectory).absoluteFilePath(QStringLiteral("engine")));
    }

    for (const QString &candidate : candidates) {
        if (QFileInfo(QDir(candidate).filePath(QStringLiteral("backend_bridge.py"))).isFile()) {
            result.m_engineDirectory = existingPath(candidate);
            break;
        }
    }
    return result;
}

QString EnginePaths::engineDirectory() const
{
    return m_engineDirectory;
}

QString EnginePaths::assetPath(const QString &relativePath) const
{
    return m_engineDirectory.isEmpty()
        ? QString()
        : existingPath(QDir(m_engineDirectory).filePath(relativePath));
}

QString EnginePaths::defaultPythonProgram(const QString &environmentOverride) const
{
    const QString configured = environmentOverride.trimmed();
    if (!configured.isEmpty()) {
        return configured;
    }
#ifdef Q_OS_WIN
    const QString python = assetPath(QStringLiteral(".venv/Scripts/python.exe"));
#else
    const QString python = assetPath(QStringLiteral(".venv/bin/python"));
#endif
    return !python.isEmpty() && QFileInfo(python).isFile() ? python : QStringLiteral("python");
}

bool EnginePaths::isLegacyWhisperDefault(const QString &path) const
{
    const QString candidate = normalizedPath(path.trimmed());
    const QString suffix = QStringLiteral("/engine/") + whisperRelativePath();
    if (!candidate.endsWith(suffix)) {
        return false;
    }

    // The previous finder persisted its first candidate even when absent. This
    // was usually the executable's debug/engine directory in a shadow build.
    if (!m_applicationDirectory.isEmpty()
        && candidate == normalizedPath(QDir(m_applicationDirectory).filePath(
               QStringLiteral("engine/") + whisperRelativePath()))) {
        return true;
    }
    if (!m_sourceEngineDirectory.isEmpty()) {
        QDir projectDirectory(m_sourceEngineDirectory);
        projectDirectory.cdUp();
        const QString buildDirectory = normalizedPath(projectDirectory.filePath(
            QStringLiteral("gui/build"))) + QLatin1Char('/');
        return candidate.startsWith(buildDirectory);
    }
    return false;
}
