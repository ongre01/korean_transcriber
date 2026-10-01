#include "mainwindow.h"
#include "ui_mainwindow.h"

#include <QDir>
#include <QFileDialog>
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

QString formattedDuration(qint64 milliseconds)
{
    if (milliseconds < 0) {
        return MainWindow::tr("미확인");
    }

    const qint64 totalSeconds = milliseconds / 1000;
    const qint64 hours = totalSeconds / 3600;
    const qint64 minutes = totalSeconds / 60 % 60;
    const qint64 seconds = totalSeconds % 60;
    return QStringLiteral("%1:%2:%3")
        .arg(hours, 2, 10, QLatin1Char('0'))
        .arg(minutes, 2, 10, QLatin1Char('0'))
        .arg(seconds, 2, 10, QLatin1Char('0'));
}

bool sameFilePath(const QString &left, const QString &right)
{
    const QFileInfo leftInfo(left);
    const QFileInfo rightInfo(right);
    const QString leftPath = leftInfo.canonicalFilePath().isEmpty()
        ? leftInfo.absoluteFilePath()
        : leftInfo.canonicalFilePath();
    const QString rightPath = rightInfo.canonicalFilePath().isEmpty()
        ? rightInfo.absoluteFilePath()
        : rightInfo.canonicalFilePath();
#ifdef Q_OS_WIN
    return leftPath.compare(rightPath, Qt::CaseInsensitive) == 0;
#else
    return leftPath == rightPath;
#endif
}
} // namespace

MainWindow::MainWindow(QWidget *parent)
    : QMainWindow(parent)
    , ui(new Ui::MainWindow)
    , m_audioFileInfo(new AudioFileInfo(this))
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
    connect(m_audioFileInfo, &AudioFileInfo::inspectionSucceeded,
            this, &MainWindow::inputFileInspectionSucceeded);
    connect(m_audioFileInfo, &AudioFileInfo::inspectionFailed,
            this, &MainWindow::inputFileInspectionFailed);

    updateInputFileUi();
    updateUiForState();
}

MainWindow::~MainWindow()
{
    delete m_audioFileInfo;
    m_audioFileInfo = nullptr;
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

AudioFileInfo *MainWindow::audioFileInfo() const noexcept
{
    return m_audioFileInfo;
}

void MainWindow::setCurrentInputFile(const QString &filePath)
{
    m_audioFileInfo->cancel();
    m_pendingInputFile.clear();
    m_inputInspectionPending = false;
    m_inputDurationMilliseconds = -1;
    m_currentInputFile = filePath.trimmed();
    if (!hasValidInput()) {
        m_currentInputFile.clear();
    } else {
        m_currentInputFile = QFileInfo(m_currentInputFile).absoluteFilePath();
    }

    updateInputFileUi();
    setAppState(hasValidInput() ? AppState::InputReady : AppState::Idle);
}

void MainWindow::selectInputFile(const QString &filePath)
{
    const QString selectedPath = filePath.trimmed();
    if (selectedPath.isEmpty()
        || m_state == AppState::Recording
        || m_state == AppState::Processing) {
        return;
    }

    m_pendingInputFile = QFileInfo(selectedPath).absoluteFilePath();
    m_inputInspectionPending = true;
    ui->fileModeRadioButton->setChecked(true);
    updateInputFileUi();
    setAppState(hasValidInput() ? AppState::InputReady : AppState::Idle,
                tr("파일 정보를 확인하는 중입니다..."));
    m_audioFileInfo->inspect(m_pendingInputFile);
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
    if (m_inputInspectionPending) {
        return;
    }

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

    emit inputFileSelectionRequested();

    QString initialDirectory;
    if (hasValidInput()) {
        initialDirectory = QFileInfo(m_currentInputFile).absolutePath();
    } else {
        initialDirectory = QDir::homePath();
    }

    const QString selectedFile = QFileDialog::getOpenFileName(
        this,
        tr("음성 파일 선택"),
        initialDirectory,
        AudioFileInfo::fileDialogFilter());
    if (!selectedFile.isEmpty()) {
        selectInputFile(selectedFile);
    }
}

void MainWindow::handleTranscriptionStart()
{
    if (!m_currentInputFile.isEmpty() && !hasValidInput()) {
        m_currentInputFile.clear();
        m_inputDurationMilliseconds = -1;
        updateInputFileUi();
        const QString message = tr("입력 파일이 삭제되었거나 이동되었습니다.");
        setAppState(AppState::Error, message);
        emit inputFileErrorOccurred(message);
        return;
    }

    const AppActionPolicy policy = appActionPolicy(
        m_state, hasValidInput() && !m_inputInspectionPending);
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

void MainWindow::inputFileInspectionSucceeded(const AudioFileMetadata &metadata)
{
    if (!m_inputInspectionPending
        || !sameFilePath(metadata.filePath, m_pendingInputFile)) {
        return;
    }

    m_currentInputFile = metadata.filePath;
    m_inputDurationMilliseconds = metadata.durationMilliseconds;
    m_pendingInputFile.clear();
    m_inputInspectionPending = false;
    updateInputFileUi();
    setAppState(AppState::InputReady, tr("입력 파일 준비 완료"));
}

void MainWindow::inputFileInspectionFailed(const QString &filePath, const QString &message)
{
    if (!m_inputInspectionPending
        || !sameFilePath(filePath, m_pendingInputFile)) {
        return;
    }

    m_pendingInputFile.clear();
    m_inputInspectionPending = false;
    updateInputFileUi();
    const QString userMessage = tr("오디오 파일을 읽을 수 없습니다: %1").arg(message);
    setAppState(AppState::Error, userMessage);
    emit inputFileErrorOccurred(userMessage);
}

void MainWindow::updateInputFileUi()
{
    if (m_inputInspectionPending) {
        const QFileInfo pendingFile(m_pendingInputFile);
        ui->fileNameValueLabel->setText(pendingFile.fileName());
        ui->filePathValueLabel->setText(pendingFile.absoluteFilePath());
        ui->fileDurationValueLabel->setText(tr("확인 중..."));
        ui->fileSizeValueLabel->setText(
            pendingFile.isFile() ? formattedFileSize(pendingFile.size()) : tr("—"));
        return;
    }

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
    ui->fileDurationValueLabel->setText(formattedDuration(m_inputDurationMilliseconds));
    ui->fileSizeValueLabel->setText(formattedFileSize(inputFile.size()));
}

void MainWindow::updateUiForState()
{
    const bool hasInput = hasValidInput() && !m_inputInspectionPending;
    const AppActionPolicy policy = appActionPolicy(m_state, hasInput);
    const bool controlsEnabled = m_state != AppState::Recording
        && m_state != AppState::Processing
        && !m_inputInspectionPending;

    ui->recordStartButton->setEnabled(
        policy.canStartRecording && !m_inputInspectionPending);
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
