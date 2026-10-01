#include "mainwindow.h"
#include "ui_mainwindow.h"

#include <QCoreApplication>
#include <QDir>
#include <QFileDialog>
#include <QFileInfo>
#include <QStatusBar>
#include <QStringList>
#include <QTextCursor>
#include <QSignalBlocker>

#include <algorithm>

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

QString engineAssetPath(const QString &relativePath)
{
    QStringList candidates;
    candidates << QDir::current().absoluteFilePath(
        QStringLiteral("engine/%1").arg(relativePath));

    QDir applicationDirectory(QCoreApplication::applicationDirPath());
    for (int depth = 0; depth < 6; ++depth) {
        candidates << applicationDirectory.absoluteFilePath(
            QStringLiteral("engine/%1").arg(relativePath));
        if (!applicationDirectory.cdUp()) {
            break;
        }
    }

    for (const QString &candidate : candidates) {
        const QFileInfo asset(candidate);
        if (asset.exists()) {
            const QString canonicalPath = asset.canonicalFilePath();
            return canonicalPath.isEmpty() ? asset.absoluteFilePath() : canonicalPath;
        }
    }

    return QDir::cleanPath(candidates.constFirst());
}

BackendDevice backendDevice(const QString &name)
{
    if (name.compare(QStringLiteral("NPU"), Qt::CaseInsensitive) == 0) {
        return BackendDevice::Npu;
    }
    if (name.compare(QStringLiteral("CPU"), Qt::CaseInsensitive) == 0) {
        return BackendDevice::Cpu;
    }
    if (name.compare(QStringLiteral("GPU"), Qt::CaseInsensitive) == 0) {
        return BackendDevice::Gpu;
    }
    return BackendDevice::Auto;
}

QString formattedTimestamp(double seconds)
{
    const qint64 totalSeconds = static_cast<qint64>(seconds);
    const qint64 hours = totalSeconds / 3600;
    const qint64 minutes = totalSeconds / 60 % 60;
    const qint64 remainingSeconds = totalSeconds % 60;
    return QStringLiteral("%1:%2:%3")
        .arg(hours, 2, 10, QLatin1Char('0'))
        .arg(minutes, 2, 10, QLatin1Char('0'))
        .arg(remainingSeconds, 2, 10, QLatin1Char('0'));
}

QString formattedTranscriptSegment(const TranscriptSegment &segment)
{
    QString heading = QStringLiteral("[%1]").arg(formattedTimestamp(segment.startTime));
    if (segment.speaker) {
        heading += MainWindow::tr(" Speaker %1").arg(*segment.speaker);
    }
    return heading + QLatin1Char('\n') + segment.text;
}
} // namespace

MainWindow::MainWindow(QWidget *parent)
    : QMainWindow(parent)
    , ui(new Ui::MainWindow)
    , m_audioRecorder(new AudioRecorder(this))
    , m_audioFileInfo(new AudioFileInfo(this))
    , m_backendProcess(new BackendProcess(this))
{
    ui->setupUi(this);

    const QString bridgeScript = engineAssetPath(QStringLiteral("backend_bridge.py"));
    m_backendProcess->setPythonProgram(m_audioFileInfo->pythonProgram());
    m_backendProcess->setBridgeScript(bridgeScript);
    m_backendProcess->setWorkingDirectory(QFileInfo(bridgeScript).absolutePath());

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
    connect(ui->microphoneComboBox,
            qOverload<int>(&QComboBox::currentIndexChanged),
            this, &MainWindow::handleMicrophoneSelection);
    connect(this, &MainWindow::recordingStartRequested,
            m_audioRecorder,
            [this]() {
                if (m_state == AppState::Recording) {
                    m_audioRecorder->startRecording();
                }
            },
            Qt::QueuedConnection);
    connect(this, &MainWindow::recordingStopRequested,
            m_audioRecorder, &AudioRecorder::stopRecording);
    connect(m_audioRecorder, &AudioRecorder::inputDevicesChanged,
            this, &MainWindow::updateMicrophoneUi);
    connect(m_audioRecorder, &AudioRecorder::recordingTimeChanged,
            this, [this](qint64 milliseconds) {
                m_recordingDurationMilliseconds = milliseconds;
                ui->recordingTimeLabel->setText(formattedDuration(milliseconds));
            });
    connect(m_audioRecorder, &AudioRecorder::recordingLevelChanged,
            this, [this](float level) {
                ui->inputLevelProgressBar->setValue(
                    qBound(0, qRound(level * 100.0f), 100));
            });
    connect(m_audioRecorder, &AudioRecorder::recordingStopped,
            this, &MainWindow::recordingFinished);
    connect(m_audioRecorder, &AudioRecorder::errorOccurred,
            this, &MainWindow::recordingFailed);
    connect(m_audioFileInfo, &AudioFileInfo::inspectionSucceeded,
            this, &MainWindow::inputFileInspectionSucceeded);
    connect(m_audioFileInfo, &AudioFileInfo::inspectionFailed,
            this, &MainWindow::inputFileInspectionFailed);
    connect(m_backendProcess, &BackendProcess::segmentReceived,
            this, &MainWindow::transcriptionSegmentReceived);
    connect(m_backendProcess, &BackendProcess::completed,
            this, &MainWindow::processingCompleted);
    connect(m_backendProcess, &BackendProcess::errorOccurred,
            this, &MainWindow::processingFailed);

    updateMicrophoneUi();
    updateInputFileUi();
    updateUiForState();
}

MainWindow::~MainWindow()
{
    if (m_audioRecorder) {
        m_audioRecorder->stopRecording();
    }
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

AudioRecorder *MainWindow::audioRecorder() const noexcept
{
    return m_audioRecorder;
}

AudioFileInfo *MainWindow::audioFileInfo() const noexcept
{
    return m_audioFileInfo;
}

BackendProcess *MainWindow::backendProcess() const noexcept
{
    return m_backendProcess;
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
    m_recordingDurationMilliseconds = 0;
    ui->recordingTimeLabel->setText(formattedDuration(0));
    ui->inputLevelProgressBar->setValue(0);
    setAppState(AppState::Recording);
    emit recordingStartRequested();
}

void MainWindow::handleRecordStop()
{
    const AppActionPolicy policy = appActionPolicy(m_state, hasValidInput());
    if (!policy.canStopRecording) {
        return;
    }

    emit recordingStopRequested();
    // A real recording finishes synchronously and recordingFinished() or
    // recordingFailed() chooses the resulting state. A very fast start/stop
    // can cancel before the queued start runs, in which case no recorder signal
    // is emitted and the previous input state is restored here.
    if (m_state == AppState::Recording) {
        setAppState(hasValidInput() ? AppState::InputReady : AppState::Idle);
    }
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

    TranscribeOptions options;
    options.inputFile = m_currentInputFile;
    options.device = backendDevice(ui->deviceComboBox->currentText());
    options.modelDirectory = engineAssetPath(
        QStringLiteral("models/whisper-large-v3-turbo-int8"));

    clearTranscript();
    setAppState(AppState::Processing);
    emit transcriptionStartRequested();
    m_backendProcess->start(options);
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

void MainWindow::updateMicrophoneUi()
{
    const QSignalBlocker blocker(ui->microphoneComboBox);
    ui->microphoneComboBox->clear();

    const QList<AudioInputDevice> devices = m_audioRecorder->inputDevices();
    if (devices.isEmpty()) {
        ui->microphoneComboBox->addItem(tr("사용 가능한 마이크 없음"));
    } else {
        const QByteArray selectedId = m_audioRecorder->selectedInputDeviceId();
        int selectedIndex = 0;
        for (const AudioInputDevice &device : devices) {
            const QString label = device.isDefault
                ? tr("%1 (기본)").arg(device.description)
                : device.description;
            ui->microphoneComboBox->addItem(label, device.id);
            if (device.id == selectedId) {
                selectedIndex = ui->microphoneComboBox->count() - 1;
            }
        }
        ui->microphoneComboBox->setCurrentIndex(selectedIndex);
    }

    const bool controlsEnabled = m_state != AppState::Recording
        && m_state != AppState::Processing
        && !m_inputInspectionPending;
    ui->microphoneComboBox->setEnabled(controlsEnabled && !devices.isEmpty());
}

void MainWindow::handleMicrophoneSelection(int index)
{
    if (index < 0) {
        return;
    }
    m_audioRecorder->setInputDevice(
        ui->microphoneComboBox->itemData(index).toByteArray());
}

void MainWindow::recordingFinished(const QString &filePath)
{
    const QFileInfo recordedFile(filePath);
    if (!recordedFile.exists() || !recordedFile.isFile()) {
        recordingFailed(tr("녹음 파일이 생성되지 않았습니다."));
        return;
    }

    m_audioFileInfo->cancel();
    m_pendingInputFile.clear();
    m_inputInspectionPending = false;
    m_currentInputFile = recordedFile.absoluteFilePath();
    m_inputDurationMilliseconds = m_recordingDurationMilliseconds;
    updateInputFileUi();
    setAppState(AppState::InputReady, tr("녹음 파일 준비 완료"));
}

void MainWindow::recordingFailed(const QString &message)
{
    ui->inputLevelProgressBar->setValue(0);
    if (m_state == AppState::Recording) {
        setAppState(AppState::Error, message);
    } else {
        statusBar()->showMessage(message);
    }
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

void MainWindow::transcriptionSegmentReceived(double start, double end,
                                              int speaker, const QString &text)
{
    if (m_state != AppState::Processing) {
        return;
    }

    TranscriptSegment segment;
    segment.startTime = start;
    segment.endTime = end;
    if (speaker > 0) {
        segment.speaker = speaker;
    }
    segment.text = text;

    const auto insertionPoint = std::upper_bound(
        m_transcriptSegments.begin(), m_transcriptSegments.end(), segment.startTime,
        [](double startTime, const TranscriptSegment &existing) {
            return startTime < existing.startTime;
        });
    const bool appendToEnd = insertionPoint == m_transcriptSegments.end();
    const bool hasPreviousSegments = !m_transcriptSegments.isEmpty();
    m_transcriptSegments.insert(insertionPoint, segment);

    if (!appendToEnd) {
        updateTranscriptUi();
        return;
    }

    QTextCursor cursor(ui->resultTextEdit->document());
    cursor.movePosition(QTextCursor::End);
    if (hasPreviousSegments) {
        cursor.insertText(QStringLiteral("\n\n"));
    }
    cursor.insertText(formattedTranscriptSegment(segment));
}

void MainWindow::clearTranscript()
{
    m_transcriptSegments.clear();
    ui->resultTextEdit->clear();
}

void MainWindow::updateTranscriptUi()
{
    QStringList blocks;
    blocks.reserve(m_transcriptSegments.size());
    for (const TranscriptSegment &segment : m_transcriptSegments) {
        blocks.append(formattedTranscriptSegment(segment));
    }

    // setPlainText intentionally prevents transcript text that resembles HTML
    // from being interpreted as rich text.
    ui->resultTextEdit->setPlainText(blocks.join(QStringLiteral("\n\n")));
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
    ui->microphoneComboBox->setEnabled(
        controlsEnabled && !m_audioRecorder->inputDevices().isEmpty());
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
