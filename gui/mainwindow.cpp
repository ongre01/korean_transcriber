#include "mainwindow.h"
#include "ui_mainwindow.h"

#include <QFileInfo>
#include <QStatusBar>

namespace {
QString statusText(AppState state)
{
    switch (state) {
    case AppState::Idle:
        return MainWindow::tr("준비");
    case AppState::Recording:
        return MainWindow::tr("녹음 중...");
    case AppState::InputReady:
        return MainWindow::tr("입력 준비 완료");
    case AppState::Processing:
        return MainWindow::tr("처리 중...");
    case AppState::Completed:
        return MainWindow::tr("완료");
    case AppState::Error:
        return MainWindow::tr("오류");
    }

    return QString();
}

QString formattedFileSize(qint64 byteCount)
{
    constexpr qint64 kibibyte = 1024;
    constexpr qint64 mebibyte = kibibyte * 1024;

    if (byteCount >= mebibyte) {
        return MainWindow::tr("%1 MB").arg(byteCount / static_cast<double>(mebibyte), 0, 'f', 1);
    }
    if (byteCount >= kibibyte) {
        return MainWindow::tr("%1 KB").arg(byteCount / static_cast<double>(kibibyte), 0, 'f', 1);
    }
    return MainWindow::tr("%1 bytes").arg(byteCount);
}
} // namespace

MainWindow::MainWindow(QWidget *parent)
    : QMainWindow(parent)
    , ui(new Ui::MainWindow)
{
    ui->setupUi(this);

    connect(ui->recordStartButton, &QPushButton::clicked,
            this, &MainWindow::handleRecordStart);
    connect(ui->recordStopButton, &QPushButton::clicked,
            this, &MainWindow::handleRecordStop);
    connect(ui->fileSelectButton, &QPushButton::clicked,
            this, &MainWindow::handleFileSelection);
    connect(ui->transcribeButton, &QPushButton::clicked,
            this, &MainWindow::handleTranscriptionStart);
    connect(ui->cancelButton, &QPushButton::clicked,
            this, &MainWindow::handleCancellation);
    connect(ui->diarizationCheckBox, &QCheckBox::toggled,
            this, &MainWindow::updateSpeakerCountEnabled);

    updateInputFileUi();
    updateUiForState();
}

MainWindow::~MainWindow()
{
    delete ui;
}

AppState MainWindow::appState() const noexcept
{
    return m_state;
}

QString MainWindow::currentInputFile() const
{
    return m_currentInputFile;
}

bool MainWindow::hasValidInput() const
{
    const QFileInfo inputFile(m_currentInputFile);
    return inputFile.exists() && inputFile.isFile();
}

void MainWindow::setCurrentInputFile(const QString &filePath)
{
    m_currentInputFile = filePath.trimmed();
    if (!hasValidInput()) {
        m_currentInputFile.clear();
    }

    updateInputFileUi();
    setAppState(hasValidInput() ? AppState::InputReady : AppState::Idle);
}

void MainWindow::setAppState(AppState state, const QString &message)
{
    const bool changed = m_state != state;
    m_state = state;
    m_stateMessage = message;
    updateUiForState();

    if (changed) {
        emit appStateChanged(m_state);
    }
}

void MainWindow::processingCompleted()
{
    if (m_state == AppState::Processing) {
        setAppState(AppState::Completed);
    }
}

void MainWindow::processingFailed(const QString &message)
{
    if (m_state == AppState::Processing) {
        setAppState(AppState::Error, message);
    }
}

void MainWindow::handleRecordStart()
{
    const AppActionPolicy policy = appActionPolicy(m_state, hasValidInput());
    if (!policy.canStartRecording) {
        return;
    }

    ui->recordingModeRadioButton->setChecked(true);
    setAppState(AppState::Recording);
    emit recordingStartRequested();
}

void MainWindow::handleRecordStop()
{
    const AppActionPolicy policy = appActionPolicy(m_state, hasValidInput());
    if (!policy.canStopRecording) {
        return;
    }

    setAppState(hasValidInput() ? AppState::InputReady : AppState::Idle);
    emit recordingStopRequested();
}

void MainWindow::handleFileSelection()
{
    const AppActionPolicy policy = appActionPolicy(m_state, hasValidInput());
    if (!policy.canSelectFile) {
        return;
    }

    ui->fileModeRadioButton->setChecked(true);
    emit inputFileSelectionRequested();
}

void MainWindow::handleTranscriptionStart()
{
    const AppActionPolicy policy = appActionPolicy(m_state, hasValidInput());
    if (!policy.canStartTranscription) {
        return;
    }

    setAppState(AppState::Processing);
    emit transcriptionStartRequested();
}

void MainWindow::handleCancellation()
{
    const AppActionPolicy policy = appActionPolicy(m_state, hasValidInput());
    if (!policy.canCancel) {
        return;
    }

    setAppState(hasValidInput() ? AppState::InputReady : AppState::Idle);
    emit cancellationRequested();
}

void MainWindow::updateSpeakerCountEnabled()
{
    const bool optionsEnabled = m_state != AppState::Recording
        && m_state != AppState::Processing;
    ui->speakerCountComboBox->setEnabled(
        optionsEnabled && ui->diarizationCheckBox->isChecked());
}

void MainWindow::updateInputFileUi()
{
    if (!hasValidInput()) {
        ui->fileNameValueLabel->setText(tr("선택된 파일 없음"));
        ui->filePathValueLabel->setText(tr("—"));
        ui->fileDurationValueLabel->setText(tr("—"));
        ui->fileSizeValueLabel->setText(tr("—"));
        return;
    }

    const QFileInfo inputFile(m_currentInputFile);
    ui->fileNameValueLabel->setText(inputFile.fileName());
    ui->filePathValueLabel->setText(inputFile.absoluteFilePath());
    ui->fileDurationValueLabel->setText(tr("미확인"));
    ui->fileSizeValueLabel->setText(formattedFileSize(inputFile.size()));
}

void MainWindow::updateUiForState()
{
    const bool hasInput = hasValidInput();
    const AppActionPolicy policy = appActionPolicy(m_state, hasInput);
    const bool controlsEnabled = m_state != AppState::Recording
        && m_state != AppState::Processing;

    ui->recordStartButton->setEnabled(policy.canStartRecording);
    ui->fileSelectButton->setEnabled(policy.canSelectFile);
    ui->recordStopButton->setEnabled(policy.canStopRecording);
    ui->transcribeButton->setEnabled(policy.canStartTranscription);
    ui->cancelButton->setEnabled(policy.canCancel);

    ui->recordingModeRadioButton->setEnabled(controlsEnabled);
    ui->fileModeRadioButton->setEnabled(controlsEnabled);
    ui->microphoneComboBox->setEnabled(controlsEnabled);
    ui->diarizationCheckBox->setEnabled(controlsEnabled);
    ui->deviceComboBox->setEnabled(controlsEnabled);
    updateSpeakerCountEnabled();

    ui->saveResultButton->setEnabled(m_state == AppState::Completed);
    ui->openResultFolderButton->setEnabled(m_state == AppState::Completed);

    if (m_state == AppState::Completed) {
        ui->processingProgressBar->setValue(100);
    } else if (m_state != AppState::Processing) {
        ui->processingProgressBar->setValue(0);
    }

    const QString stateLabel = statusText(m_state);
    ui->processingStateLabel->setText(stateLabel);
    ui->statusLabel->setText(stateLabel);
    statusBar()->showMessage(m_stateMessage.isEmpty() ? stateLabel : m_stateMessage);
}
