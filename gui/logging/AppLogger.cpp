#include "AppLogger.h"

#include <QDate>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QStandardPaths>
#include <QStringList>

namespace {
QString logFileName(const QDate &date)
{
    return QStringLiteral("app_%1.log").arg(date.toString(QStringLiteral("yyyyMMdd")));
}
} // namespace

AppLogger::AppLogger(QString directory)
    : m_directory(directory.trimmed().isEmpty()
                      ? defaultLogDirectory()
                      : QDir::cleanPath(QFileInfo(directory).absoluteFilePath()))
{
}

QString AppLogger::logDirectory() const
{
    return m_directory;
}

QString AppLogger::currentLogFilePath() const
{
    return QDir(m_directory).filePath(logFileName(QDate::currentDate()));
}

QString AppLogger::lastError() const
{
    return m_lastError;
}

bool AppLogger::info(const QString &event, const QString &detail)
{
    return append(QStringLiteral("INFO"), event, detail);
}

bool AppLogger::error(const QString &event, const QString &detail)
{
    return append(QStringLiteral("ERROR"), event, detail);
}

bool AppLogger::diagnostic(const QString &event, const QByteArray &data)
{
    return append(QStringLiteral("DIAGNOSTIC"), event, QString::fromUtf8(data));
}

bool AppLogger::append(const QString &level, const QString &event, const QString &detail)
{
    QDir directory(m_directory);
    if (!directory.exists() && !directory.mkpath(QStringLiteral("."))) {
        m_lastError = QStringLiteral("Could not create log directory: %1").arg(m_directory);
        return false;
    }

    QFile file(currentLogFilePath());
    if (!file.open(QIODevice::WriteOnly | QIODevice::Append)) {
        m_lastError = file.errorString();
        return false;
    }

    const QString timestamp = QDateTime::currentDateTime().toString(
        QStringLiteral("yyyy-MM-dd HH:mm:ss.zzz"));
    const QString prefix = QStringLiteral("%1 [%2] %3")
                               .arg(timestamp, level, event.trimmed());
    const QString normalizedDetail = detail;
    const QStringList lines = normalizedDetail.split(QLatin1Char('\n'));

    QByteArray output;
    if (normalizedDetail.isEmpty()) {
        output = (prefix + QLatin1Char('\n')).toUtf8();
    } else {
        for (const QString &line : lines) {
            output += (prefix + QStringLiteral(" | ") + line + QLatin1Char('\n')).toUtf8();
        }
    }

    if (file.write(output) != output.size()) {
        m_lastError = file.errorString();
        return false;
    }
    m_lastError.clear();
    return true;
}

QString AppLogger::defaultLogDirectory()
{
    QString base = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    if (base.isEmpty()) {
        base = QDir::currentPath();
    }
    return QDir(base).filePath(QStringLiteral("logs"));
}
