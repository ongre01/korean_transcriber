#ifndef MULTIAUDIOMANIFESTFILE_H
#define MULTIAUDIOMANIFESTFILE_H

#include <QTemporaryFile>

class QJsonObject;
class QString;

// Owns the temporary manifest passed to backend_bridge.py. The file remains
// available until this object is destroyed, which must be after QProcess has
// stopped reading it.
class MultiAudioManifestFile final
{
public:
    MultiAudioManifestFile();

    bool write(const QJsonObject &manifest, QString *errorMessage = nullptr);
    QString fileName() const;

private:
    QTemporaryFile m_file;
};

#endif // MULTIAUDIOMANIFESTFILE_H
