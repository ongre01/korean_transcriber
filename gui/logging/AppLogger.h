#ifndef APPLOGGER_H
#define APPLOGGER_H

#include <QByteArray>
#include <QString>

// Small, failure-tolerant application logger.  A new file is selected for
// each call so a process that crosses midnight writes to the correct day.
class AppLogger final
{
public:
    explicit AppLogger(QString directory = QString());

    QString logDirectory() const;
    QString currentLogFilePath() const;
    QString lastError() const;

    bool info(const QString &event, const QString &detail = QString());
    bool error(const QString &event, const QString &detail = QString());
    bool diagnostic(const QString &event, const QByteArray &data);

private:
    bool append(const QString &level, const QString &event, const QString &detail);
    static QString defaultLogDirectory();

    QString m_directory;
    QString m_lastError;
};

#endif // APPLOGGER_H
