#ifndef MULTIAUDIOINPUTDIALOG_H
#define MULTIAUDIOINPUTDIALOG_H

#include "MultiAudioInputModel.h"

#include <QDialog>
#include <QStringList>

class AudioFileInfo;
struct AudioFileMetadata;
class QComboBox;
class QDialogButtonBox;
class QLabel;
class QPushButton;
class QTableView;

class MultiAudioInputDialog final : public QDialog
{
    Q_OBJECT

public:
    explicit MultiAudioInputDialog(QWidget *parent = nullptr);

    MultiAudioInputModel *model() const noexcept;
    QVector<MultiAudioInput> inputs() const;
    void setInputs(const QVector<MultiAudioInput> &inputs);

    int referenceRow() const noexcept;
    void setReferenceRow(int row);

    void setInitialDirectory(const QString &directory);
    QString lastDirectory() const;
    void setAudioFileInfoConfiguration(const QString &pythonProgram,
                                       const QString &probeScript);

    int addFiles(const QStringList &filePaths);

protected:
    void accept() override;

private slots:
    void chooseFiles();
    void removeSelectedFiles();
    void inspectionSucceeded(const AudioFileMetadata &metadata);
    void inspectionFailed(const QString &filePath, const QString &message);
    void rebuildReferenceCombo();
    void updateActions();

private:
    void enqueuePendingInspections();
    void startNextInspection();
    void purgeInspectionQueue();
    void updateSummary();

    MultiAudioInputModel *m_model = nullptr;
    AudioFileInfo *m_audioFileInfo = nullptr;
    QTableView *m_tableView = nullptr;
    QPushButton *m_addButton = nullptr;
    QPushButton *m_removeButton = nullptr;
    QComboBox *m_referenceComboBox = nullptr;
    QLabel *m_summaryLabel = nullptr;
    QDialogButtonBox *m_buttonBox = nullptr;
    QString m_lastDirectory;
    QStringList m_inspectionQueue;
    QString m_currentInspection;
    bool m_rebuildingReferenceCombo = false;
};

#endif // MULTIAUDIOINPUTDIALOG_H
