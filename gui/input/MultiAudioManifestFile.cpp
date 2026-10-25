#include "MultiAudioManifestFile.h"

#include <QDir>
#include <QJsonDocument>
#include <QJsonObject>
#include <QString>

MultiAudioManifestFile::MultiAudioManifestFile()
    : m_file(QDir(QDir::tempPath()).filePath(
          QStringLiteral("korean_transcriber_run_XXXXXX.json")))
{
    m_file.setAutoRemove(true);
}

bool MultiAudioManifestFile::write(const QJsonObject &manifest,
                                   QString *errorMessage)
{
    const auto fail = [this, errorMessage](const QString &message) {
        if (m_file.isOpen()) {
            m_file.close();
        }
        m_file.remove();
        if (errorMessage) {
            *errorMessage = message;
        }
        return false;
    };

    if (!m_file.open()) {
        return fail(m_file.errorString());
    }

    const QByteArray data = QJsonDocument(manifest).toJson(QJsonDocument::Compact);
    if (m_file.write(data) != data.size()) {
        return fail(m_file.errorString());
    }
    if (!m_file.flush()) {
        return fail(m_file.errorString());
    }
    m_file.close();

    if (errorMessage) {
        errorMessage->clear();
    }
    return true;
}

QString MultiAudioManifestFile::fileName() const
{
    return m_file.fileName();
}
