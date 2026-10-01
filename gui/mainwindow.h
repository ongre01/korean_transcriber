#ifndef MAINWINDOW_H
#define MAINWINDOW_H

#include "app/AppState.h"

#include <QMainWindow>
#include <QString>

QT_BEGIN_NAMESPACE
namespace Ui {
class MainWindow;
}
QT_END_NAMESPACE

class MainWindow : public QMainWindow
{
    Q_OBJECT

public:
    explicit MainWindow(QWidget *parent = nullptr);
    ~MainWindow() override;

    AppState appState() const noexcept;
    QString currentInputFile() const;
    bool hasValidInput() const;

public slots:
    void setCurrentInputFile(const QString &filePath);
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

private slots:
    void handleRecordStart();
    void handleRecordStop();
    void handleFileSelection();
    void handleTranscriptionStart();
    void handleCancellation();
    void updateSpeakerCountEnabled();

private:
    void updateInputFileUi();
    void updateUiForState();

    Ui::MainWindow *ui;
    AppState m_state = AppState::Idle;
    QString m_currentInputFile;
    QString m_stateMessage;
};
#endif // MAINWINDOW_H
