#ifndef MULTIAUDIOINPUTDIALOG_H
#define MULTIAUDIOINPUTDIALOG_H

#include "MultiAudioInputModel.h"

#include <QByteArray>
#include <QDialog>
#include <QStringList>

class AudioFileInfo;
struct AudioFileMetadata;
class QComboBox;
class QDialogButtonBox;
class QJsonObject;
class QLabel;
class QProcess;
class QProgressBar;
class QPushButton;
class QTableView;
class QTemporaryFile;

class MultiAudioInputDialog final : public QDialog
{
    Q_OBJECT

public:
    explicit MultiAudioInputDialog(QWidget *parent = nullptr);
    ~MultiAudioInputDialog() override;

    MultiAudioInputModel *model() const noexcept;
    QVector<MultiAudioInput> inputs() const;
    void setInputs(const QVector<MultiAudioInput> &inputs);

    int referenceRow() const noexcept;
    void setReferenceRow(int row);

    void setInitialDirectory(const QString &directory);
    QString lastDirectory() const;
    void setAudioFileInfoConfiguration(const QString &pythonProgram,
                                       const QString &probeScript);
    void setAlignmentAnalyzerConfiguration(const QString &pythonProgram,
                                           const QString &scriptPath);
    bool isAlignmentAnalysisRunning() const;

    int addFiles(const QStringList &filePaths);

protected:
    void accept() override;
    void reject() override;

private slots:
    void chooseFiles();
    void removeSelectedFiles();
    void startAlignmentAnalysis();
    void cancelAlignmentAnalysis();
    void editManualOffset();
    void inspectionSucceeded(const AudioFileMetadata &metadata);
    void inspectionFailed(const QString &filePath, const QString &message);
    void rebuildReferenceCombo();
    void updateActions();

private:
    void enqueuePendingInspections();
    void startNextInspection();
    void purgeInspectionQueue();
    void updateSummary();
    void finishAlignmentAnalysis(QProcess *process, quint64 generation,
                                 int exitCode, int exitStatus);
    bool applyPreparationFailures(const QJsonObject &payload,
                                  QString *message);
    void alignmentProcessFailedToStart(QProcess *process, quint64 generation);
    void restoreAlignmentSnapshot();
    void cleanupAlignmentRun(QProcess *process);
    void setAlignmentMessage(const QString &message, bool error = false);
    int selectedInputRow() const;

    MultiAudioInputModel *m_model = nullptr;
    AudioFileInfo *m_audioFileInfo = nullptr;
    QTableView *m_tableView = nullptr;
    QPushButton *m_addButton = nullptr;
    QPushButton *m_removeButton = nullptr;
    QPushButton *m_analyzeAlignmentButton = nullptr;
    QPushButton *m_cancelAlignmentButton = nullptr;
    QPushButton *m_manualOffsetButton = nullptr;
    QComboBox *m_referenceComboBox = nullptr;
    QLabel *m_summaryLabel = nullptr;
    QLabel *m_alignmentMessageLabel = nullptr;
    QProgressBar *m_alignmentProgressBar = nullptr;
    QDialogButtonBox *m_buttonBox = nullptr;
    QString m_lastDirectory;
    QStringList m_inspectionQueue;
    QString m_currentInspection;
    bool m_rebuildingReferenceCombo = false;
    QString m_alignmentPythonProgram;
    QString m_alignmentScriptPath;
    QProcess *m_alignmentProcess = nullptr;
    QTemporaryFile *m_alignmentManifestFile = nullptr;
    quint64 m_alignmentGeneration = 0;
    QByteArray m_alignmentStandardOutput;
    QByteArray m_alignmentStandardError;
    QVector<int> m_alignmentRows;
    QVector<MultiAudioInput> m_alignmentSnapshot;
    int m_alignmentReferenceInputIndex = -1;
    int m_alignmentSnapshotReferenceRow = -1;
    bool m_alignmentCancellationPending = false;
};

#endif // MULTIAUDIOINPUTDIALOG_H
