#include "mainwindow.h"
#include "logging/LogDialog.h"
#include "settings/SettingsDialog.h"
#include "ui_mainwindow.h"

#include <QAction>
#include <QCoreApplication>
#include <QCloseEvent>
#include <QDesktopServices>
#include <QDir>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QMessageBox>
#include <QMenu>
#include <QMenuBar>
#include <QPlainTextEdit>
#include <QSaveFile>
#include <QStatusBar>
#include <QStringList>
#include <QTextCursor>
#include <QSignalBlocker>
#include <QStandardPaths>
#include <QTimer>
#include <QUrl>

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

QString processingStateText(const QString &state)
{
    if (state == QStringLiteral("preparing")) {
        return MainWindow::tr("준비 중...");
    }
    if (state == QStringLiteral("loading_model")) {
        return MainWindow::tr("모델 불러오는 중...");
    }
    if (state == QStringLiteral("decoding_audio")) {
        return MainWindow::tr("오디오 디코딩 중...");
    }
    if (state == QStringLiteral("transcribing")) {
        return MainWindow::tr("전사 중...");
    }
    if (state == QStringLiteral("diarization")) {
        return MainWindow::tr("화자 분리 중...");
    }
    if (state == QStringLiteral("saving_result")) {
        return MainWindow::tr("결과 저장 중...");
    }
    return MainWindow::tr("처리 중...");
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

QString userHotwordsFilePath()
{
    QString directory = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    if (directory.isEmpty()) {
        directory = QStandardPaths::writableLocation(QStandardPaths::DocumentsLocation);
    }
    return directory.isEmpty()
        ? QString()
        : QDir(directory).filePath(QStringLiteral("hotwords.txt"));
}

QString userInitialPromptFilePath()
{
    QString directory = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    if (directory.isEmpty()) {
        directory = QStandardPaths::writableLocation(QStandardPaths::DocumentsLocation);
    }
    return directory.isEmpty()
        ? QString()
        : QDir(directory).filePath(QStringLiteral("initial_prompt.txt"));
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

QString formattedTranscriptSegment(const TranscriptSegment &segment,
                                   bool showSpeaker)
{
    QString heading = QStringLiteral("[%1]").arg(formattedTimestamp(segment.startTime));
    if (showSpeaker) {
        if (segment.speaker) {
            heading += MainWindow::tr(" Speaker %1").arg(*segment.speaker);
        } else {
            heading += MainWindow::tr(" Speaker ?");
        }
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
    , m_hotwordsSaveTimer(new QTimer(this))
    , m_initialPromptSaveTimer(new QTimer(this))
{
    ui->setupUi(this);
    m_hotwordsSaveTimer->setSingleShot(true);
    m_hotwordsSaveTimer->setInterval(400);
    m_initialPromptSaveTimer->setSingleShot(true);
    m_initialPromptSaveTimer->setInterval(400);

    m_settings = Settings::load();
    if (m_settings.pythonPath.isEmpty()) {
        m_settings.pythonPath = m_audioFileInfo->pythonProgram();
    }
    if (m_settings.whisperModelDirectory.isEmpty()) {
        m_settings.whisperModelDirectory = engineAssetPath(
            QStringLiteral("models/whisper-large-v3-turbo-int8"));
    }

    const QString bridgeScript = engineAssetPath(QStringLiteral("backend_bridge.py"));
    m_backendProcess->setBridgeScript(bridgeScript);
    m_backendProcess->setWorkingDirectory(QFileInfo(bridgeScript).absolutePath());
    applySettingsToUi();

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
    connect(ui->saveResultButton, &QPushButton::clicked,
            this, &MainWindow::handleResultSave);
    connect(ui->openResultFolderButton, &QPushButton::clicked,
            this, &MainWindow::handleOpenResultFolder);
    connect(ui->diarizationCheckBox, &QCheckBox::toggled,
            this, &MainWindow::updateSpeakerCountEnabled);
    connect(ui->diarizationCheckBox, &QCheckBox::toggled, this,
            [this](bool) { saveSettings(); });
    connect(ui->speakerCountComboBox, &QComboBox::currentTextChanged, this,
            [this](const QString &) { saveSettings(); });
    connect(ui->deviceComboBox, &QComboBox::currentTextChanged, this,
            [this](const QString &) { saveSettings(); });
    connect(ui->hotwordsTextEdit, &QPlainTextEdit::textChanged,
            this, &MainWindow::scheduleHotwordsSave);
    connect(ui->initialPromptTextEdit, &QPlainTextEdit::textChanged,
            this, &MainWindow::scheduleInitialPromptSave);
    connect(m_hotwordsSaveTimer, &QTimer::timeout, this, [this]() {
        QString errorMessage;
        if (!saveHotwordsText(&errorMessage)) {
            logError(tr("Hotwords Save Error"), errorMessage);
            ui->hotwordsStatusLabel->setText(tr("자동 저장 실패: %1").arg(errorMessage));
        }
    });
    connect(m_initialPromptSaveTimer, &QTimer::timeout, this, [this]() {
        QString errorMessage;
        if (!saveInitialPromptText(&errorMessage)) {
            logError(tr("Initial Prompt Save Error"), errorMessage);
            ui->initialPromptStatusLabel->setText(
                tr("자동 저장 실패: %1").arg(errorMessage));
        }
    });
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
    connect(this, &MainWindow::cancellationRequested,
            m_backendProcess, &BackendProcess::cancel);
    connect(m_audioRecorder, &AudioRecorder::inputDevicesChanged,
            this, &MainWindow::updateMicrophoneUi);
    connect(m_audioRecorder, &AudioRecorder::recordingTimeChanged,
            this, &MainWindow::recordingTimeChanged);
    connect(m_audioRecorder, &AudioRecorder::recordingLevelChanged,
            this, &MainWindow::recordingLevelChanged);
    connect(m_audioRecorder, &AudioRecorder::recordingStarted,
            this, &MainWindow::recordingStarted);
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
    connect(m_backendProcess, &BackendProcess::stateChanged,
            this, &MainWindow::backendStateChanged);
    connect(m_backendProcess, &BackendProcess::progressChanged,
            this, &MainWindow::backendProgressChanged);
    connect(m_backendProcess, &BackendProcess::progressTimeChanged,
            this, &MainWindow::backendProgressTimeChanged);
    connect(m_backendProcess, &BackendProcess::completed,
            this, &MainWindow::processingCompleted);
    connect(m_backendProcess, &BackendProcess::cancelled,
            this, &MainWindow::processingCancelled);
    connect(m_backendProcess, &BackendProcess::warningOccurred, this,
            [this](const QString &message) {
                logInfo(tr("Backend Warning"), message);
                if (message.contains(QStringLiteral("핫워드"))) {
                    ui->hotwordsStatusLabel->setText(message);
                } else {
                    ui->initialPromptStatusLabel->setText(message);
                }
                statusBar()->showMessage(message);
            });
    connect(m_backendProcess, &BackendProcess::errorOccurred,
            this, &MainWindow::processingFailed);
    connect(m_backendProcess, &BackendProcess::standardErrorReceived,
            this, &MainWindow::backendStandardErrorReceived);
    connect(m_backendProcess, &BackendProcess::stopped,
            this, &MainWindow::backendProcessStopped);
    connect(m_backendProcess, &BackendProcess::commandStarted, this,
            [this](const QString &command, const QString &workingDirectory) {
                logInfo(tr("Python Command"),
                        tr("%1\nWorking directory: %2")
                            .arg(command, QDir::toNativeSeparators(workingDirectory)));
            });

    QMenu *settingsMenu = menuBar()->addMenu(tr("설정"));
    m_settingsAction = settingsMenu->addAction(tr("설정..."));
    connect(m_settingsAction, &QAction::triggered, this, &MainWindow::handleSettings);
    QMenu *toolsMenu = menuBar()->addMenu(tr("도구"));
    m_viewLogsAction = toolsMenu->addAction(tr("상세 로그 보기..."));
    connect(m_viewLogsAction, &QAction::triggered, this, &MainWindow::handleLogView);

    updateMicrophoneUi();
    updateInputFileUi();
    updateResultOutputUi();
    updateUiForState();
    logInfo(tr("Application Start"));
    logInfo(tr("Selected Audio Device"), selectedMicrophoneDescription());
}

MainWindow::~MainWindow()
{
    saveSettings();
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
    logInfo(tr("Input File Selected"), QDir::toNativeSeparators(m_pendingInputFile));
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
    if (m_state == AppState::Processing && !m_cancellationPending) {
        const QFileInfo resultFile(m_backendProcess->textResultFile());
        m_resultOutputFile = resultFile.isFile()
            ? resultFile.absoluteFilePath()
            : QString();
        logInfo(tr("Output File"),
                tr("TXT: %1\nSRT: %2")
                    .arg(QDir::toNativeSeparators(m_backendProcess->textResultFile()),
                         QDir::toNativeSeparators(m_backendProcess->srtResultFile())));
        updateResultOutputUi();
        setAppState(
            AppState::Completed,
            m_diarizationFallbackOccurred
                ? tr("화자 분리 NPU를 사용할 수 없어 CPU로 대체하여 완료했습니다.")
                : QString());
    }
}

void MainWindow::processingFailed(const QString &message)
{
    if (m_state == AppState::Processing && !m_cancellationPending) {
        logError(tr("Backend Error"), message);
        resetProcessingIndicators();
        const QString userMessage = backendUserMessage(message);
        setAppState(AppState::Error, userMessage);
        showOperationError(tr("전사 오류"), userMessage);
    }
}

void MainWindow::processingCancelled()
{
    const bool closeRequested = m_closeRequested;
    m_closeRequested = false;
    m_cancellationPending = false;

    if (m_state == AppState::Processing) {
        resetProcessingIndicators();
        setAppState(hasValidInput() ? AppState::InputReady : AppState::Idle);
    }

    if (closeRequested) {
        close();
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

    QString initialDirectory = m_settings.lastInputDirectory;
    if (hasValidInput()) {
        initialDirectory = QFileInfo(m_currentInputFile).absolutePath();
    } else if (initialDirectory.isEmpty() || !QFileInfo(initialDirectory).isDir()) {
        initialDirectory = QDir::homePath();
    }

    const QString selectedFile = QFileDialog::getOpenFileName(
        this,
        tr("음성 파일 선택"),
        initialDirectory,
        AudioFileInfo::fileDialogFilter());
    if (!selectedFile.isEmpty()) {
        m_settings.lastInputDirectory = QFileInfo(selectedFile).absolutePath();
        saveSettings();
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

    m_hotwordsSaveTimer->stop();
    QString hotwordsError;
    if (!saveHotwordsText(&hotwordsError)) {
        logError(tr("Hotwords Save Error"), hotwordsError);
        QMessageBox::warning(this, tr("Hotwords 저장 실패"), hotwordsError);
        return;
    }

    m_initialPromptSaveTimer->stop();
    QString initialPromptError;
    if (!saveInitialPromptText(&initialPromptError)) {
        logError(tr("Initial Prompt Save Error"), initialPromptError);
        QMessageBox::warning(this, tr("초기 프롬프트 저장 실패"), initialPromptError);
        return;
    }

    const bool diarizationEnabled = ui->diarizationCheckBox->isChecked();
    if (!validateSettingsForRun(diarizationEnabled)) {
        return;
    }

    TranscribeOptions options;
    options.inputFile = m_currentInputFile;
    options.device = backendDevice(ui->deviceComboBox->currentText());
    options.outputDirectory = m_settings.outputDirectory;
    options.modelDirectory = m_settings.whisperModelDirectory;
    options.windowSeconds = m_settings.windowSeconds;
    options.overlapSeconds = m_settings.overlapSeconds;
    options.hotwordsFile = m_settings.hotwordsFile;
    options.initialPromptFile = m_settings.initialPromptFile;
    options.diarizationEnabled = diarizationEnabled;
    options.speakerThreshold = m_settings.speakerThreshold;
    options.minimumSpeechDuration = m_settings.minimumSpeechDuration;
    options.minimumSilenceDuration = m_settings.minimumSilenceDuration;
    options.diarizationSegmentationModel = m_settings.diarizationSegmentationModel;
    options.diarizationEmbeddingModel = m_settings.diarizationEmbeddingModel;
    options.diarizationDevice = backendDevice(m_settings.diarizationDevice);
    options.diarizationFallbackToCpu = m_settings.diarizationFallbackToCpu;
    if (options.diarizationEnabled) {
        const QString speakerCount = ui->speakerCountComboBox->currentText().trimmed();
        if (speakerCount.compare(QStringLiteral("Auto"), Qt::CaseInsensitive) != 0) {
            bool isNumber = false;
            const int value = speakerCount.toInt(&isNumber);
            if (!isNumber) {
                const QString message = tr("화자 수 옵션이 올바르지 않습니다.");
                setAppState(AppState::Error, message);
                return;
            }
            options.speakerCount = value;
        }
    }

    QString optionsError;
    if (!options.isValid(&optionsError)) {
        const QString message = tr("전사 설정이 올바르지 않습니다: %1").arg(optionsError);
        QMessageBox::warning(this, tr("설정 확인"), message);
        statusBar()->showMessage(message);
        return;
    }

    m_diarizationEnabledForRun = options.diarizationEnabled;
    m_cancellationPending = false;
    m_diarizationFallbackOccurred = false;
    m_backendStderrTail.clear();
    clearTranscript();
    resetProcessingIndicators();
    setAppState(AppState::Processing);
    logInfo(tr("Transcription Start"),
            tr("Input: %1\nDevice: %2\nDiarization: %3")
                .arg(QDir::toNativeSeparators(options.inputFile),
                     backendDeviceArgument(options.device),
                     options.diarizationEnabled ? tr("ON") : tr("OFF")));
    logInfo(tr("Model"), QDir::toNativeSeparators(options.modelDirectory));
    emit transcriptionStartRequested();
    m_backendProcess->start(options);
}

void MainWindow::handleCancellation()
{
    const AppActionPolicy policy = appActionPolicy(m_state, hasValidInput());
    if (!policy.canCancel || m_cancellationPending) {
        return;
    }

    m_cancellationPending = true;
    m_backendState = QStringLiteral("cancelling");
    logInfo(tr("Transcription Cancel Requested"));
    ui->processingProgressBar->setRange(0, 0);
    updateUiForState();
    emit cancellationRequested();
}

void MainWindow::handleResultSave()
{
    if (m_state != AppState::Completed) {
        return;
    }
    if (!hasAvailableResult()) {
        updateUiForState();
        showResultError(tr("저장할 전사 결과 파일을 찾을 수 없습니다."));
        return;
    }

    const QFileInfo currentResult(m_resultOutputFile);
    QFileDialog saveDialog(this, tr("TXT 결과 저장"));
    saveDialog.setAcceptMode(QFileDialog::AcceptSave);
    saveDialog.setFileMode(QFileDialog::AnyFile);
    saveDialog.setNameFilter(tr("텍스트 파일 (*.txt)"));
    saveDialog.setDirectory(currentResult.absolutePath());
    saveDialog.selectFile(currentResult.fileName());
    // Use one explicit, translated overwrite prompt below. This also keeps the
    // behaviour consistent between native and non-native file dialogs.
    saveDialog.setOption(QFileDialog::DontUseNativeDialog);
    saveDialog.setOption(QFileDialog::DontConfirmOverwrite);
    if (saveDialog.exec() != QDialog::Accepted || saveDialog.selectedFiles().isEmpty()) {
        return;
    }
    QString targetPath = saveDialog.selectedFiles().constFirst();
    if (QFileInfo(targetPath).suffix().isEmpty()) {
        targetPath += QStringLiteral(".txt");
    }

    const QFileInfo targetFile(targetPath);
    if (targetFile.exists()) {
        const QMessageBox::StandardButton answer = QMessageBox::question(
            this,
            tr("파일 덮어쓰기"),
            tr("이미 같은 이름의 파일이 있습니다. 덮어쓰시겠습니까?\n%1")
                .arg(QDir::toNativeSeparators(targetFile.absoluteFilePath())),
            QMessageBox::Yes | QMessageBox::No,
            QMessageBox::No);
        if (answer != QMessageBox::Yes) {
            return;
        }
    }

    QSaveFile outputFile(targetFile.absoluteFilePath());
    if (!outputFile.open(QIODevice::WriteOnly)) {
        showResultError(
            tr("파일을 저장할 수 없습니다: %1").arg(outputFile.errorString()));
        return;
    }

    const QByteArray contents = ui->resultTextEdit->toPlainText().toUtf8();
    if (outputFile.write(contents) != contents.size()) {
        outputFile.cancelWriting();
        showResultError(
            tr("파일을 저장할 수 없습니다: %1").arg(outputFile.errorString()));
        return;
    }
    if (!outputFile.commit()) {
        showResultError(
            tr("파일을 저장할 수 없습니다: %1").arg(outputFile.errorString()));
        return;
    }

    m_resultOutputFile = QFileInfo(targetFile.absoluteFilePath()).absoluteFilePath();
    m_settings.outputDirectory = targetFile.absolutePath();
    saveSettings();
    updateResultOutputUi();
    updateUiForState();
    statusBar()->showMessage(
        tr("결과를 저장했습니다: %1")
            .arg(QDir::toNativeSeparators(m_resultOutputFile)));
}

void MainWindow::handleOpenResultFolder()
{
    if (m_state != AppState::Completed) {
        return;
    }
    if (!hasAvailableResult()) {
        updateUiForState();
        showResultError(tr("열 수 있는 전사 결과 파일을 찾을 수 없습니다."));
        return;
    }

    const QFileInfo resultFile(m_resultOutputFile);
    const QUrl folderUrl = QUrl::fromLocalFile(resultFile.absolutePath());
    if (!QDesktopServices::openUrl(folderUrl)) {
        showResultError(
            tr("결과 폴더를 열 수 없습니다: %1")
                .arg(QDir::toNativeSeparators(resultFile.absolutePath())));
    }
}

void MainWindow::handleSettings()
{
    if (m_state == AppState::Recording || m_state == AppState::Processing) {
        return;
    }

    SettingsDialog dialog(m_settings, this);
    if (dialog.exec() != QDialog::Accepted) {
        return;
    }

    m_settings = dialog.settings();
    applySettingsToUi();
    saveSettings();
    const QString message = tr("설정을 저장했습니다.");
    m_stateMessage = message;
    updateUiForState();
}

void MainWindow::handleLogView()
{
    auto *dialog = new LogDialog(m_logger.logDirectory(), this);
    dialog->setAttribute(Qt::WA_DeleteOnClose);
    dialog->show();
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
    if (m_audioRecorder->setInputDevice(
            ui->microphoneComboBox->itemData(index).toByteArray())) {
        saveSettings();
        logInfo(tr("Selected Audio Device"), selectedMicrophoneDescription());
    }
}

void MainWindow::recordingTimeChanged(qint64 milliseconds)
{
    if (m_state != AppState::Recording) {
        return;
    }

    m_recordingDurationMilliseconds = qMax<qint64>(0, milliseconds);
    ui->recordingTimeLabel->setText(
        formattedDuration(m_recordingDurationMilliseconds));
}

void MainWindow::recordingLevelChanged(float level)
{
    if (m_state != AppState::Recording) {
        return;
    }

    ui->inputLevelProgressBar->setValue(
        qBound(0, qRound(level * 100.0f), 100));
}

void MainWindow::recordingStarted()
{
    logInfo(tr("Recording Start"),
            tr("Device: %1\nOutput: %2")
                .arg(selectedMicrophoneDescription(),
                     QDir::toNativeSeparators(m_audioRecorder->outputFile())));
}

void MainWindow::recordingFinished(const QString &filePath)
{
    if (m_state != AppState::Recording) {
        return;
    }

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
    m_settings.lastInputDirectory = recordedFile.absolutePath();
    saveSettings();
    logInfo(tr("Recording Stop"), QDir::toNativeSeparators(m_currentInputFile));
    logInfo(tr("Input File"), QDir::toNativeSeparators(m_currentInputFile));
    updateInputFileUi();
    setAppState(AppState::InputReady, tr("녹음 파일 준비 완료"));
}

void MainWindow::recordingFailed(const QString &message)
{
    logError(tr("Recording Error"), message);
    ui->inputLevelProgressBar->setValue(0);
    const QString userMessage = recordingUserMessage(message);
    if (m_state == AppState::Recording) {
        setAppState(AppState::Error, userMessage);
    } else {
        statusBar()->showMessage(userMessage);
    }
    showOperationError(tr("녹음 오류"), userMessage);
}

void MainWindow::inputFileInspectionSucceeded(const AudioFileMetadata &metadata)
{
    if (!m_inputInspectionPending
        || !sameFilePath(metadata.filePath, m_pendingInputFile)) {
        return;
    }

    m_currentInputFile = metadata.filePath;
    m_inputDurationMilliseconds = metadata.durationMilliseconds;
    m_settings.lastInputDirectory = QFileInfo(metadata.filePath).absolutePath();
    saveSettings();
    logInfo(tr("Input File"),
            tr("Path: %1\nDuration: %2\nSize: %3")
                .arg(QDir::toNativeSeparators(metadata.filePath),
                     formattedDuration(metadata.durationMilliseconds),
                     formattedFileSize(metadata.sizeBytes)));
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
    logError(tr("Input File Error"),
             tr("File: %1\n%2").arg(QDir::toNativeSeparators(filePath), message));
    const QString userMessage = fileUserMessage(message);
    setAppState(AppState::Error, userMessage);
    emit inputFileErrorOccurred(userMessage);
    showOperationError(tr("입력 파일 오류"), userMessage);
}

void MainWindow::backendStateChanged(const QString &state)
{
    if (m_state != AppState::Processing || m_cancellationPending) {
        return;
    }

    m_backendState = state;
    logInfo(tr("Backend State"), state);
    if (state == QStringLiteral("loading_model")) {
        logInfo(tr("Model Loading"), QDir::toNativeSeparators(m_settings.whisperModelDirectory));
    } else if (state == QStringLiteral("transcribing")) {
        logInfo(tr("Transcription Processing Started"));
    } else if (state == QStringLiteral("diarization")) {
        logInfo(tr("Diarization Start"),
                tr("Device: %1; CPU fallback: %2")
                    .arg(m_settings.diarizationDevice,
                         m_settings.diarizationFallbackToCpu ? tr("enabled") : tr("disabled")));
    }
    ui->processingProgressBar->setRange(0, 0);
    updateUiForState();
}

void MainWindow::backendProgressChanged(int progress)
{
    if (m_state != AppState::Processing || m_cancellationPending) {
        return;
    }

    if (progress < 0) {
        ui->processingProgressBar->setRange(0, 0);
        return;
    }

    ui->processingProgressBar->setRange(0, 100);
    ui->processingProgressBar->setValue(progress);
}

void MainWindow::backendProgressTimeChanged(double processedSeconds, double totalSeconds)
{
    if (m_state != AppState::Processing || m_cancellationPending) {
        return;
    }

    m_processedSeconds = processedSeconds;
    m_totalSeconds = totalSeconds;
    ui->processingTimeLabel->setText(
        tr("%1 / %2")
            .arg(formattedDuration(qRound64(processedSeconds * 1000.0)))
            .arg(formattedDuration(qRound64(totalSeconds * 1000.0))));
}

void MainWindow::backendStandardErrorReceived(const QByteArray &data)
{
    if (data.isEmpty()) {
        return;
    }

    logInfo(tr("Backend stderr"), QString::fromUtf8(data));
    m_backendStderrTail += QString::fromUtf8(data);
    constexpr qsizetype maximumTailLength = 4096;
    if (m_backendStderrTail.size() > maximumTailLength) {
        m_backendStderrTail.remove(0, m_backendStderrTail.size() - maximumTailLength);
    }

    const QString diagnostics = m_backendStderrTail.toCaseFolded();
    const bool fallbackToCpu = diagnostics.contains(QStringLiteral("falling back to cpu"))
        || diagnostics.contains(QStringLiteral("falling back to openvino cpu"));
    if (!m_diarizationFallbackOccurred && fallbackToCpu) {
        m_diarizationFallbackOccurred = true;
        logInfo(tr("Diarization Device Fallback"),
                tr("NPU unavailable; speaker segmentation is continuing on CPU."));
    }
}

void MainWindow::backendProcessStopped()
{
    // A protocol/process error is signalled before QProcess has necessarily
    // exited.  Keep actions disabled until this terminal notification, then
    // apply the normal Error-state policy so the user can try again.
    if (m_state == AppState::Error) {
        updateUiForState();
    }
}

void MainWindow::transcriptionSegmentReceived(double start, double end,
                                              int speaker, const QString &text)
{
    if (m_state != AppState::Processing || m_cancellationPending) {
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
    cursor.insertText(formattedTranscriptSegment(segment, m_diarizationEnabledForRun));
}

void MainWindow::clearTranscript()
{
    m_transcriptSegments.clear();
    m_resultOutputFile.clear();
    ui->resultTextEdit->clear();
    updateResultOutputUi();
}

void MainWindow::resetProcessingIndicators()
{
    m_backendState.clear();
    m_processedSeconds = -1.0;
    m_totalSeconds = -1.0;
    ui->processingProgressBar->setRange(0, 100);
    ui->processingProgressBar->setValue(0);
    ui->processingTimeLabel->setText(tr("—"));
}

void MainWindow::updateTranscriptUi()
{
    QStringList blocks;
    blocks.reserve(m_transcriptSegments.size());
    for (const TranscriptSegment &segment : m_transcriptSegments) {
        blocks.append(formattedTranscriptSegment(segment, m_diarizationEnabledForRun));
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

void MainWindow::updateResultOutputUi()
{
    const QString outputPath = m_resultOutputFile.isEmpty()
        ? tr("—")
        : QDir::toNativeSeparators(m_resultOutputFile);
    ui->resultOutputPathLabel->setText(tr("출력 파일: %1").arg(outputPath));
    ui->resultOutputPathLabel->setToolTip(m_resultOutputFile);
}

bool MainWindow::hasAvailableResult() const
{
    const QFileInfo resultFile(m_resultOutputFile);
    return resultFile.exists() && resultFile.isFile();
}

void MainWindow::showResultError(const QString &message)
{
    logError(tr("Result Output Error"), message);
    statusBar()->showMessage(tr("결과 저장 오류: %1").arg(message));
}

void MainWindow::logInfo(const QString &event, const QString &detail)
{
    if (m_logger.info(event, detail) || m_logWriteFailureReported) {
        return;
    }

    m_logWriteFailureReported = true;
    statusBar()->showMessage(
        tr("로그 파일을 쓸 수 없습니다. 계속 작업할 수 있지만 상세 로그는 저장되지 않습니다."));
}

void MainWindow::logError(const QString &event, const QString &detail)
{
    if (m_logger.error(event, detail) || m_logWriteFailureReported) {
        return;
    }

    m_logWriteFailureReported = true;
    statusBar()->showMessage(
        tr("로그 파일을 쓸 수 없습니다. 계속 작업할 수 있지만 상세 로그는 저장되지 않습니다."));
}

QString MainWindow::selectedMicrophoneDescription() const
{
    const QByteArray selectedId = m_audioRecorder->selectedInputDeviceId();
    for (const AudioInputDevice &device : m_audioRecorder->inputDevices()) {
        if (device.id == selectedId) {
            return device.description;
        }
    }
    return tr("사용 가능한 마이크 없음");
}

QString MainWindow::detailLogHint() const
{
    if (m_logWriteFailureReported) {
        return tr("상세 로그를 저장하지 못했습니다. 로그 폴더의 쓰기 권한을 확인하세요.");
    }
    return tr("상세 로그는 도구 메뉴의 ‘상세 로그 보기’에서 확인할 수 있습니다.");
}

QString MainWindow::recordingUserMessage(const QString &detail) const
{
    const QString normalized = detail.toCaseFolded();
    if (normalized.contains(QStringLiteral("권한"))
        || normalized.contains(QStringLiteral("permission"))) {
        return tr("마이크 접근 권한이 없습니다. 운영체제의 마이크 권한을 확인하세요.");
    }
    if (normalized.contains(QStringLiteral("마이크"))
        || normalized.contains(QStringLiteral("microphone"))) {
        return tr("사용 가능한 마이크를 찾거나 초기화할 수 없습니다. 연결 상태와 선택한 장치를 확인하세요.");
    }
    if (normalized.contains(QStringLiteral("wav"))
        || normalized.contains(QStringLiteral("파일"))
        || normalized.contains(QStringLiteral("folder"))) {
        return tr("녹음 파일을 만들 수 없습니다. 저장 폴더의 경로와 쓰기 권한을 확인하세요.");
    }
    return tr("녹음을 시작하거나 유지할 수 없습니다. 오디오 장치 상태를 확인하세요.");
}

QString MainWindow::fileUserMessage(const QString &detail) const
{
    const QString normalized = detail.toCaseFolded();
    if (normalized.contains(QStringLiteral("찾을 수 없"))
        || normalized.contains(QStringLiteral("not found"))) {
        return tr("입력 파일을 찾을 수 없습니다. 파일이 이동되거나 삭제되지 않았는지 확인하세요.");
    }
    if (normalized.contains(QStringLiteral("지원하지 않는"))
        || normalized.contains(QStringLiteral("unsupported"))) {
        return tr("지원하지 않는 파일 형식입니다. 지원되는 오디오 또는 비디오 파일을 선택하세요.");
    }
    return tr("오디오 파일을 읽거나 디코딩할 수 없습니다. 파일이 손상되지 않았는지 확인하세요.");
}

QString MainWindow::backendUserMessage(const QString &detail) const
{
    const QString normalized = detail.toCaseFolded();
    if (normalized.contains(QStringLiteral("required python module"))
        || normalized.contains(QStringLiteral("no module named"))
        || normalized.contains(QStringLiteral("failed to start backend"))
        || normalized.contains(QStringLiteral("python program"))) {
        return tr("Python 실행 환경을 시작할 수 없습니다. Python 경로와 필수 모듈을 확인하세요.");
    }
    if (normalized.contains(QStringLiteral("input file not found"))) {
        return tr("입력 파일을 찾을 수 없습니다. 파일이 이동되거나 삭제되지 않았는지 확인하세요.");
    }
    if (normalized.contains(QStringLiteral("audio decoding"))
        || normalized.contains(QStringLiteral("decode"))) {
        return tr("오디오를 디코딩할 수 없습니다. 파일 형식과 손상 여부를 확인하세요.");
    }
    if (normalized.contains(QStringLiteral("diarization"))
        || normalized.contains(QStringLiteral("segmentation"))
        || normalized.contains(QStringLiteral("embedding"))) {
        return tr("화자 분리를 완료할 수 없습니다. 화자 분리 모델과 장치 설정을 확인하세요.");
    }
    if (normalized.contains(QStringLiteral("model directory"))
        || normalized.contains(QStringLiteral("whisper model"))
        || normalized.contains(QStringLiteral("whisper pipeline"))) {
        return tr("음성 인식 모델을 불러올 수 없습니다. 모델 폴더와 OpenVINO 설정을 확인하세요.");
    }
    if (normalized.contains(QStringLiteral("device selection"))
        || normalized.contains(QStringLiteral("openvino device"))
        || normalized.contains(QStringLiteral("npu"))) {
        return tr("선택한 AI 장치를 사용할 수 없습니다. 장치를 AUTO 또는 CPU로 바꿔 다시 시도하세요.");
    }
    return tr("음성 인식 처리 중 오류가 발생했습니다. 설정을 확인한 뒤 다시 시도하세요.");
}

void MainWindow::showOperationError(const QString &title, const QString &message)
{
    QMessageBox::warning(this, title,
                         tr("%1\n\n%2").arg(message, detailLogHint()));
}

void MainWindow::applySettingsToUi()
{
    m_audioFileInfo->setPythonProgram(m_settings.pythonPath);
    m_backendProcess->setPythonProgram(m_settings.pythonPath);

    const QSignalBlocker deviceBlocker(ui->deviceComboBox);
    const int deviceIndex = ui->deviceComboBox->findText(
        m_settings.selectedDevice, Qt::MatchFixedString);
    ui->deviceComboBox->setCurrentIndex(deviceIndex >= 0 ? deviceIndex : 0);

    const QSignalBlocker diarizationBlocker(ui->diarizationCheckBox);
    ui->diarizationCheckBox->setChecked(m_settings.diarizationEnabled);
    const QSignalBlocker speakerCountBlocker(ui->speakerCountComboBox);
    const int speakerCountIndex = ui->speakerCountComboBox->findText(
        m_settings.speakerCount, Qt::MatchFixedString);
    ui->speakerCountComboBox->setCurrentIndex(speakerCountIndex >= 0 ? speakerCountIndex : 0);

    if (!m_settings.selectedMicrophoneId.isEmpty()
        && !m_audioRecorder->setInputDevice(m_settings.selectedMicrophoneId)) {
        m_stateMessage = tr("저장된 마이크를 찾을 수 없습니다. 현재 사용 가능한 마이크를 다시 선택하세요.");
    }
    loadHotwordsText();
    loadInitialPromptText();
    updateSpeakerCountEnabled();
}

void MainWindow::saveSettings()
{
    if (!ui || !m_audioRecorder) {
        return;
    }

    m_settings.selectedMicrophoneId = m_audioRecorder->selectedInputDeviceId();
    m_settings.selectedDevice = ui->deviceComboBox->currentText();
    m_settings.diarizationEnabled = ui->diarizationCheckBox->isChecked();
    m_settings.speakerCount = ui->speakerCountComboBox->currentText();
    m_settings.save();
}

void MainWindow::scheduleHotwordsSave()
{
    m_hotwordsTextDirty = true;
    ui->hotwordsStatusLabel->setText(tr("입력 내용 저장 중..."));
    m_hotwordsSaveTimer->start();
}

void MainWindow::scheduleInitialPromptSave()
{
    m_initialPromptTextDirty = true;
    ui->initialPromptStatusLabel->setText(tr("입력 내용 저장 중..."));
    m_initialPromptSaveTimer->start();
}

void MainWindow::loadHotwordsText()
{
    const QSignalBlocker blocker(ui->hotwordsTextEdit);
    ui->hotwordsTextEdit->clear();
    m_hotwordsTextDirty = false;

    const QString filePath = m_settings.hotwordsFile.trimmed();
    if (filePath.isEmpty()) {
        ui->hotwordsStatusLabel->setText(tr("입력하면 자동 저장됩니다."));
        return;
    }

    QFile input(filePath);
    if (!input.open(QIODevice::ReadOnly)) {
        const QString errorMessage = tr("Hotwords 파일을 불러올 수 없습니다: %1")
                                         .arg(input.errorString());
        logError(tr("Hotwords Load Error"), errorMessage);
        ui->hotwordsStatusLabel->setText(errorMessage);
        return;
    }

    QString contents = QString::fromUtf8(input.readAll());
    if (contents.startsWith(QChar::ByteOrderMark)) {
        contents.remove(0, 1);
    }
    ui->hotwordsTextEdit->setPlainText(contents);
    ui->hotwordsStatusLabel->setText(tr("저장된 핫워드를 불러왔습니다."));
}

bool MainWindow::saveHotwordsText(QString *errorMessage)
{
    if (!m_hotwordsTextDirty) {
        if (errorMessage) {
            errorMessage->clear();
        }
        return true;
    }

    QString filePath = m_settings.hotwordsFile.trimmed();
    if (filePath.isEmpty()) {
        filePath = userHotwordsFilePath();
    }
    if (filePath.isEmpty()) {
        if (errorMessage) {
            *errorMessage = tr("Hotwords 파일을 저장할 사용자 데이터 폴더를 찾을 수 없습니다.");
        }
        return false;
    }

    const QString absolutePath = QDir::cleanPath(QFileInfo(filePath).absoluteFilePath());
    const QString directoryPath = QFileInfo(absolutePath).absolutePath();
    if (!QDir().mkpath(directoryPath)) {
        if (errorMessage) {
            *errorMessage = tr("Hotwords 파일 폴더를 만들 수 없습니다: %1")
                                .arg(QDir::toNativeSeparators(directoryPath));
        }
        return false;
    }

    QSaveFile output(absolutePath);
    if (!output.open(QIODevice::WriteOnly)) {
        if (errorMessage) {
            *errorMessage = tr("Hotwords 파일을 저장할 수 없습니다: %1").arg(output.errorString());
        }
        return false;
    }

    QString contents = ui->hotwordsTextEdit->toPlainText();
    if (!contents.isEmpty() && !contents.endsWith(QLatin1Char('\n'))) {
        contents.append(QLatin1Char('\n'));
    }
    const QByteArray encodedContents = contents.toUtf8();
    if (output.write(encodedContents) != encodedContents.size()) {
        output.cancelWriting();
        if (errorMessage) {
            *errorMessage = tr("Hotwords 파일을 저장할 수 없습니다: %1").arg(output.errorString());
        }
        return false;
    }
    if (!output.commit()) {
        if (errorMessage) {
            *errorMessage = tr("Hotwords 파일을 저장할 수 없습니다: %1").arg(output.errorString());
        }
        return false;
    }

    m_settings.hotwordsFile = absolutePath;
    m_hotwordsTextDirty = false;
    saveSettings();
    ui->hotwordsStatusLabel->setText(tr("자동 저장됨"));
    if (errorMessage) {
        errorMessage->clear();
    }
    return true;
}

void MainWindow::loadInitialPromptText()
{
    const QSignalBlocker blocker(ui->initialPromptTextEdit);
    ui->initialPromptTextEdit->clear();
    m_initialPromptTextDirty = false;

    const QString filePath = m_settings.initialPromptFile.trimmed();
    if (filePath.isEmpty()) {
        ui->initialPromptStatusLabel->setText(tr("입력하면 자동 저장됩니다."));
        return;
    }

    QFile input(filePath);
    if (!input.open(QIODevice::ReadOnly)) {
        const QString errorMessage = tr("Initial Prompt 파일을 불러올 수 없습니다: %1")
                                         .arg(input.errorString());
        logError(tr("Initial Prompt Load Error"), errorMessage);
        ui->initialPromptStatusLabel->setText(errorMessage);
        return;
    }

    QString contents = QString::fromUtf8(input.readAll());
    if (contents.startsWith(QChar::ByteOrderMark)) {
        contents.remove(0, 1);
    }
    ui->initialPromptTextEdit->setPlainText(contents);
    ui->initialPromptStatusLabel->setText(tr("저장된 초기 프롬프트를 불러왔습니다."));
}

bool MainWindow::saveInitialPromptText(QString *errorMessage)
{
    // A blank setting must still be persisted and passed to the bridge.  This
    // prevents the engine's bundled default prompt file from being applied
    // when the user intentionally leaves the editor empty.
    if (!m_initialPromptTextDirty && !m_settings.initialPromptFile.trimmed().isEmpty()) {
        if (errorMessage) {
            errorMessage->clear();
        }
        return true;
    }

    QString filePath = m_settings.initialPromptFile.trimmed();
    if (filePath.isEmpty()) {
        filePath = userInitialPromptFilePath();
    }
    if (filePath.isEmpty()) {
        if (errorMessage) {
            *errorMessage = tr("초기 프롬프트 파일을 저장할 사용자 데이터 폴더를 찾을 수 없습니다.");
        }
        return false;
    }

    const QString absolutePath = QDir::cleanPath(QFileInfo(filePath).absoluteFilePath());
    const QString directoryPath = QFileInfo(absolutePath).absolutePath();
    if (!QDir().mkpath(directoryPath)) {
        if (errorMessage) {
            *errorMessage = tr("초기 프롬프트 파일 폴더를 만들 수 없습니다: %1")
                                .arg(QDir::toNativeSeparators(directoryPath));
        }
        return false;
    }

    QSaveFile output(absolutePath);
    if (!output.open(QIODevice::WriteOnly)) {
        if (errorMessage) {
            *errorMessage = tr("초기 프롬프트 파일을 저장할 수 없습니다: %1")
                                .arg(output.errorString());
        }
        return false;
    }

    QString contents = ui->initialPromptTextEdit->toPlainText();
    if (!contents.isEmpty() && !contents.endsWith(QLatin1Char('\n'))) {
        contents.append(QLatin1Char('\n'));
    }
    const QByteArray encodedContents = contents.toUtf8();
    if (output.write(encodedContents) != encodedContents.size()) {
        output.cancelWriting();
        if (errorMessage) {
            *errorMessage = tr("초기 프롬프트 파일을 저장할 수 없습니다: %1")
                                .arg(output.errorString());
        }
        return false;
    }
    if (!output.commit()) {
        if (errorMessage) {
            *errorMessage = tr("초기 프롬프트 파일을 저장할 수 없습니다: %1")
                                .arg(output.errorString());
        }
        return false;
    }

    m_settings.initialPromptFile = absolutePath;
    m_initialPromptTextDirty = false;
    saveSettings();
    ui->initialPromptStatusLabel->setText(tr("자동 저장됨"));
    if (errorMessage) {
        errorMessage->clear();
    }
    return true;
}

bool MainWindow::validateSettingsForRun(bool diarizationEnabled)
{
    QString errorMessage;
    if (!m_settings.validateForRun(diarizationEnabled, &errorMessage)) {
        logError(tr("Settings Error"), errorMessage);
        const QString message = tr("전사를 시작할 수 없습니다.\n%1\n\n설정 메뉴에서 값을 확인하세요.")
                                    .arg(errorMessage);
        QMessageBox::warning(this, tr("설정 확인"), message);
        statusBar()->showMessage(errorMessage);
        return false;
    }

    const QString outputDirectory = QFileInfo(m_settings.outputDirectory).absoluteFilePath();
    if (!QDir().mkpath(outputDirectory) || !QFileInfo(outputDirectory).isDir()) {
        logError(tr("Output Directory Error"), QDir::toNativeSeparators(outputDirectory));
        const QString message = tr("결과 출력 폴더를 만들 수 없습니다: %1\n\n설정 메뉴에서 다른 폴더를 선택하세요.")
                                    .arg(QDir::toNativeSeparators(outputDirectory));
        QMessageBox::warning(this, tr("설정 확인"), message);
        statusBar()->showMessage(message);
        return false;
    }
    m_settings.outputDirectory = outputDirectory;
    return true;
}

void MainWindow::updateUiForState()
{
    const bool hasInput = hasValidInput() && !m_inputInspectionPending;
    const AppActionPolicy policy = appActionPolicy(m_state, hasInput);
    const bool backendBusy = m_backendProcess && m_backendProcess->isRunning();
    const bool controlsEnabled = m_state != AppState::Recording
        && m_state != AppState::Processing
        && !m_inputInspectionPending
        && !backendBusy;

    ui->recordStartButton->setEnabled(
        policy.canStartRecording && !m_inputInspectionPending && !backendBusy);
    ui->fileSelectButton->setEnabled(policy.canSelectFile && !backendBusy);
    ui->recordStopButton->setEnabled(policy.canStopRecording);
    ui->transcribeButton->setEnabled(policy.canStartTranscription && !backendBusy);
    ui->cancelButton->setEnabled(policy.canCancel && !m_cancellationPending);

    ui->recordingModeRadioButton->setEnabled(controlsEnabled);
    ui->fileModeRadioButton->setEnabled(controlsEnabled);
    ui->microphoneComboBox->setEnabled(
        controlsEnabled && !m_audioRecorder->inputDevices().isEmpty());
    ui->diarizationCheckBox->setEnabled(controlsEnabled);
    ui->deviceComboBox->setEnabled(controlsEnabled);
    ui->hotwordsTextEdit->setEnabled(m_state != AppState::Processing && !backendBusy);
    ui->initialPromptTextEdit->setEnabled(m_state != AppState::Processing && !backendBusy);
    if (m_settingsAction) {
        m_settingsAction->setEnabled(controlsEnabled);
    }
    updateSpeakerCountEnabled();

    const bool resultAvailable = m_state == AppState::Completed && hasAvailableResult();
    ui->saveResultButton->setEnabled(resultAvailable);
    ui->openResultFolderButton->setEnabled(resultAvailable);

    if (m_state == AppState::Completed) {
        ui->processingProgressBar->setRange(0, 100);
        ui->processingProgressBar->setValue(100);
    } else if (m_state != AppState::Processing) {
        ui->processingProgressBar->setRange(0, 100);
        ui->processingProgressBar->setValue(0);
        ui->processingTimeLabel->setText(tr("—"));
    }

    const QString stateLabel = m_state == AppState::Processing
        ? (m_cancellationPending
               ? tr("취소 중...")
               : processingStateText(m_backendState))
        : statusText(m_state);
    ui->processingStateLabel->setText(stateLabel);
    ui->statusLabel->setText(stateLabel);
    statusBar()->showMessage(m_stateMessage.isEmpty() ? stateLabel : m_stateMessage);
}

void MainWindow::closeEvent(QCloseEvent *event)
{
    if (m_backendProcess && m_backendProcess->isRunning()) {
        m_closeRequested = true;
        if (!m_cancellationPending) {
            m_cancellationPending = true;
            m_backendState = QStringLiteral("cancelling");
            updateUiForState();
        }
        emit cancellationRequested();
        event->ignore();
        return;
    }

    if (m_audioRecorder) {
        m_audioRecorder->stopRecording();
    }
    m_hotwordsSaveTimer->stop();
    QString hotwordsError;
    if (!saveHotwordsText(&hotwordsError)) {
        logError(tr("Hotwords Save Error"), hotwordsError);
    }
    m_initialPromptSaveTimer->stop();
    QString initialPromptError;
    if (!saveInitialPromptText(&initialPromptError)) {
        logError(tr("Initial Prompt Save Error"), initialPromptError);
    }
    saveSettings();
    QMainWindow::closeEvent(event);
}
