#ifndef MAINWINDOW_H
#define MAINWINDOW_H

#include "app/AppState.h"
#include "audio/AudioRecorder.h"
#include "backend/BackendProcess.h"
#include "backend/TranscriptSegment.h"
#include "input/AudioFileInfo.h"

#include <QMainWindow>
#include <QString>
#include <QVector>

QT_BEGIN_NAMESPACE
namespace Ui {
class MainWindow;
}
QT_END_NAMESPACE

class QCloseEvent;

class MainWindow : public QMainWindow
{
    Q_OBJECT

public:
    explicit MainWindow(QWidget *parent = nullptr);
    ~MainWindow() override;

    AppState appState() const noexcept;
    QString currentInputFile() const;
    bool hasValidInput() const;
    AudioRecorder *audioRecorder() const noexcept;
    AudioFileInfo *audioFileInfo() const noexcept;
    BackendProcess *backendProcess() const noexcept;

public slots:
    void setCurrentInputFile(const QString &filePath);
    void selectInputFile(const QString &filePath);
    void setAppState(AppState state, const QString &message = QString());
    void processingCompleted();
    void processingFailed(const QString &message);

signals:
    void appStateChanged(AppState state);
    void inputFileSelectionRequested();
    void recordingStartRequested();
    void recordingStopRequested();
    void transcriptionStartRequested();
    void cancellationRequested();
    void inputFileErrorOccurred(const QString &message);

private slots:
    void handleRecordStart();
    void handleRecordStop();
    void handleFileSelection();
    void handleTranscriptionStart();
    void handleCancellation();
    void handleResultSave();
    void handleOpenResultFolder();
    void updateSpeakerCountEnabled();
    void updateMicrophoneUi();
    void handleMicrophoneSelection(int index);
    void recordingTimeChanged(qint64 milliseconds);
    void recordingLevelChanged(float level);
    void recordingFinished(const QString &filePath);
    void recordingFailed(const QString &message);
    void inputFileInspectionSucceeded(const AudioFileMetadata &metadata);
    void inputFileInspectionFailed(const QString &filePath, const QString &message);
    void processingCancelled();
    void backendStateChanged(const QString &state);
    void backendProgressChanged(int progress);
    void backendProgressTimeChanged(double processedSeconds, double totalSeconds);
    void transcriptionSegmentReceived(double start, double end,
                                      int speaker, const QString &text);

private:
    void clearTranscript();
    void resetProcessingIndicators();
    void updateTranscriptUi();
    void updateInputFileUi();
    void updateResultOutputUi();
    void updateUiForState();
    bool hasAvailableResult() const;
    void showResultError(const QString &message);

protected:
    void closeEvent(QCloseEvent *event) override;

private:
    Ui::MainWindow *ui;
    AudioRecorder *m_audioRecorder;
    AudioFileInfo *m_audioFileInfo;
    BackendProcess *m_backendProcess;
    AppState m_state = AppState::Idle;
    QString m_currentInputFile;
    QString m_pendingInputFile;
    qint64 m_inputDurationMilliseconds = -1;
    qint64 m_recordingDurationMilliseconds = 0;
    bool m_inputInspectionPending = false;
    bool m_cancellationPending = false;
    bool m_closeRequested = false;
    // Preserve the option used for the active/completed run. The checkbox can
    // be changed after completion without changing how that result is shown.
    bool m_diarizationEnabledForRun = false;
    QString m_backendState;
    QString m_resultOutputFile;
    double m_processedSeconds = -1.0;
    double m_totalSeconds = -1.0;
    QString m_stateMessage;
    QVector<TranscriptSegment> m_transcriptSegments;
};
#endif // MAINWINDOW_H
