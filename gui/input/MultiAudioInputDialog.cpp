#include "MultiAudioInputDialog.h"

#include "AudioFileInfo.h"
#include "../app/EnginePaths.h"

#include <QAbstractItemView>
#include <QComboBox>
#include <QDialogButtonBox>
#include <QDir>
#include <QFileDialog>
#include <QFileInfo>
#include <QHeaderView>
#include <QHBoxLayout>
#include <QInputDialog>
#include <QItemSelectionModel>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>
#include <QLabel>
#include <QMessageBox>
#include <QProcess>
#include <QProgressBar>
#include <QPushButton>
#include <QSet>
#include <QStandardPaths>
#include <QTableView>
#include <QTemporaryFile>
#include <QTimer>
#include <QVBoxLayout>

#include <algorithm>
#include <cmath>
#include <functional>
#include <limits>

namespace {
struct ParsedAlignmentResult
{
    int inputIndex = -1;
    MultiAudioInput::AlignmentStatus status =
        MultiAudioInput::AlignmentStatus::Failed;
    bool hasOffset = false;
    qint64 offsetMilliseconds = 0;
    double confidence = 0.0;
    bool hasDrift = false;
    double driftPpm = 0.0;
    QString detail;
};

bool jsonInteger(const QJsonValue &value, int minimum, int maximum, int *result)
{
    if (!value.isDouble()) {
        return false;
    }
    const double number = value.toDouble();
    if (!std::isfinite(number) || std::floor(number) != number
        || number < minimum || number > maximum) {
        return false;
    }
    *result = static_cast<int>(number);
    return true;
}

bool jsonFiniteNumber(const QJsonValue &value, double *result)
{
    if (!value.isDouble()) {
        return false;
    }
    const double number = value.toDouble();
    if (!std::isfinite(number)) {
        return false;
    }
    *result = number;
    return true;
}

QString resultDetail(const QJsonObject &object)
{
    QString detail = object.value(QStringLiteral("warning_reason"))
                         .toString().trimmed();
    if (detail.isEmpty()) {
        detail = object.value(QStringLiteral("failure_reason"))
                     .toString().trimmed();
    }
    return detail;
}
} // namespace

MultiAudioInputDialog::MultiAudioInputDialog(QWidget *parent)
    : QDialog(parent)
    , m_model(new MultiAudioInputModel(this))
    , m_audioFileInfo(new AudioFileInfo(this))
{
    const EnginePaths enginePaths = EnginePaths::discover();
    m_alignmentPythonProgram = m_audioFileInfo->pythonProgram();
    m_alignmentScriptPath = enginePaths.assetPath(
        QStringLiteral("multimic_alignment.py"));

    setObjectName(QStringLiteral("multiAudioInputDialog"));
    setWindowTitle(tr("다중 마이크 파일"));
    setModal(true);
    resize(1180, 650);
    setMinimumSize(900, 520);

    auto *mainLayout = new QVBoxLayout(this);
    auto *descriptionLabel = new QLabel(
        tr("같은 녹음을 담은 파일을 2개 이상 추가하세요. 파일 검사는 별도 프로세스에서 순차적으로 실행됩니다."),
        this);
    descriptionLabel->setWordWrap(true);
    mainLayout->addWidget(descriptionLabel);

    m_tableView = new QTableView(this);
    m_tableView->setObjectName(QStringLiteral("multiAudioInputTableView"));
    m_tableView->setModel(m_model);
    m_tableView->setSelectionBehavior(QAbstractItemView::SelectRows);
    m_tableView->setSelectionMode(QAbstractItemView::ExtendedSelection);
    m_tableView->setEditTriggers(QAbstractItemView::DoubleClicked
                                 | QAbstractItemView::SelectedClicked
                                 | QAbstractItemView::EditKeyPressed);
    m_tableView->setAlternatingRowColors(true);
    m_tableView->setSortingEnabled(false);
    m_tableView->verticalHeader()->setVisible(false);
    QHeaderView *header = m_tableView->horizontalHeader();
    header->setStretchLastSection(true);
    header->setSectionResizeMode(MultiAudioInputModel::UseColumn,
                                 QHeaderView::ResizeToContents);
    header->setSectionResizeMode(MultiAudioInputModel::FileColumn,
                                 QHeaderView::Stretch);
    header->setSectionResizeMode(MultiAudioInputModel::DurationColumn,
                                 QHeaderView::ResizeToContents);
    header->setSectionResizeMode(MultiAudioInputModel::AliasColumn,
                                 QHeaderView::ResizeToContents);
    header->setSectionResizeMode(MultiAudioInputModel::OffsetColumn,
                                 QHeaderView::ResizeToContents);
    header->setSectionResizeMode(MultiAudioInputModel::DriftColumn,
                                 QHeaderView::ResizeToContents);
    header->setSectionResizeMode(MultiAudioInputModel::ConfidenceColumn,
                                 QHeaderView::ResizeToContents);
    mainLayout->addWidget(m_tableView, 1);

    auto *controlsLayout = new QHBoxLayout;
    m_addButton = new QPushButton(tr("파일 추가..."), this);
    m_addButton->setObjectName(QStringLiteral("multiAudioAddButton"));
    m_removeButton = new QPushButton(tr("선택 제거"), this);
    m_removeButton->setObjectName(QStringLiteral("multiAudioRemoveButton"));
    controlsLayout->addWidget(m_addButton);
    controlsLayout->addWidget(m_removeButton);
    controlsLayout->addSpacing(16);
    controlsLayout->addWidget(new QLabel(tr("기준 파일"), this));
    m_referenceComboBox = new QComboBox(this);
    m_referenceComboBox->setObjectName(QStringLiteral("multiAudioReferenceComboBox"));
    m_referenceComboBox->setMinimumContentsLength(24);
    m_referenceComboBox->setSizeAdjustPolicy(QComboBox::AdjustToMinimumContentsLengthWithIcon);
    controlsLayout->addWidget(m_referenceComboBox, 1);
    mainLayout->addLayout(controlsLayout);

    auto *alignmentLayout = new QHBoxLayout;
    m_analyzeAlignmentButton = new QPushButton(tr("시간 정렬 분석"), this);
    m_analyzeAlignmentButton->setObjectName(
        QStringLiteral("multiAudioAnalyzeAlignmentButton"));
    m_cancelAlignmentButton = new QPushButton(tr("분석 취소"), this);
    m_cancelAlignmentButton->setObjectName(
        QStringLiteral("multiAudioCancelAlignmentButton"));
    m_manualOffsetButton = new QPushButton(tr("수동 오프셋..."), this);
    m_manualOffsetButton->setObjectName(
        QStringLiteral("multiAudioManualOffsetButton"));
    m_alignmentProgressBar = new QProgressBar(this);
    m_alignmentProgressBar->setObjectName(
        QStringLiteral("multiAudioAlignmentProgressBar"));
    m_alignmentProgressBar->setRange(0, 0);
    m_alignmentProgressBar->setTextVisible(false);
    m_alignmentProgressBar->setVisible(false);
    alignmentLayout->addWidget(m_analyzeAlignmentButton);
    alignmentLayout->addWidget(m_cancelAlignmentButton);
    alignmentLayout->addWidget(m_manualOffsetButton);
    alignmentLayout->addWidget(m_alignmentProgressBar, 1);
    mainLayout->addLayout(alignmentLayout);

    auto *alignmentHelpLabel = new QLabel(
        tr("저신뢰 정렬 결과는 수동 오프셋을 지정하거나 ‘사용’을 해제해 제외하세요. "
           "디코딩 등 준비 실패는 파일을 교체하거나 제외해야 합니다."),
        this);
    alignmentHelpLabel->setWordWrap(true);
    mainLayout->addWidget(alignmentHelpLabel);

    m_alignmentMessageLabel = new QLabel(this);
    m_alignmentMessageLabel->setObjectName(
        QStringLiteral("multiAudioAlignmentMessageLabel"));
    m_alignmentMessageLabel->setWordWrap(true);
    mainLayout->addWidget(m_alignmentMessageLabel);

    m_summaryLabel = new QLabel(this);
    m_summaryLabel->setObjectName(QStringLiteral("multiAudioSummaryLabel"));
    m_summaryLabel->setWordWrap(true);
    mainLayout->addWidget(m_summaryLabel);

    auto *recommendationLabel = new QLabel(
        tr("초기 사용은 최대 8개 파일을 권장합니다. 이는 안내 기준이며 파일 수를 기술적으로 제한하지 않습니다."),
        this);
    recommendationLabel->setWordWrap(true);
    mainLayout->addWidget(recommendationLabel);

    m_buttonBox = new QDialogButtonBox(QDialogButtonBox::Ok
                                       | QDialogButtonBox::Cancel,
                                       this);
    m_buttonBox->button(QDialogButtonBox::Ok)->setText(tr("적용"));
    m_buttonBox->button(QDialogButtonBox::Cancel)->setText(tr("취소"));
    mainLayout->addWidget(m_buttonBox);

    connect(m_addButton, &QPushButton::clicked,
            this, &MultiAudioInputDialog::chooseFiles);
    connect(m_removeButton, &QPushButton::clicked,
            this, &MultiAudioInputDialog::removeSelectedFiles);
    connect(m_analyzeAlignmentButton, &QPushButton::clicked,
            this, &MultiAudioInputDialog::startAlignmentAnalysis);
    connect(m_cancelAlignmentButton, &QPushButton::clicked,
            this, &MultiAudioInputDialog::cancelAlignmentAnalysis);
    connect(m_manualOffsetButton, &QPushButton::clicked,
            this, &MultiAudioInputDialog::editManualOffset);
    connect(m_buttonBox, &QDialogButtonBox::accepted,
            this, &MultiAudioInputDialog::accept);
    connect(m_buttonBox, &QDialogButtonBox::rejected,
            this, &MultiAudioInputDialog::reject);
    connect(m_referenceComboBox,
            qOverload<int>(&QComboBox::currentIndexChanged),
            this,
            [this](int index) {
                if (m_rebuildingReferenceCombo || index < 0) {
                    return;
                }
                m_model->setReferenceRow(
                    m_referenceComboBox->itemData(index).toInt());
            });
    connect(m_audioFileInfo, &AudioFileInfo::inspectionSucceeded,
            this, &MultiAudioInputDialog::inspectionSucceeded);
    connect(m_audioFileInfo, &AudioFileInfo::inspectionFailed,
            this, &MultiAudioInputDialog::inspectionFailed);
    connect(m_model, &QAbstractItemModel::modelReset,
            this, &MultiAudioInputDialog::rebuildReferenceCombo);
    connect(m_model, &QAbstractItemModel::rowsInserted,
            this, &MultiAudioInputDialog::rebuildReferenceCombo);
    connect(m_model, &QAbstractItemModel::rowsRemoved,
            this, &MultiAudioInputDialog::rebuildReferenceCombo);
    connect(m_model, &QAbstractItemModel::dataChanged,
            this, &MultiAudioInputDialog::rebuildReferenceCombo);
    connect(m_model, &MultiAudioInputModel::referenceRowChanged,
            this, &MultiAudioInputDialog::rebuildReferenceCombo);
    connect(m_model, &MultiAudioInputModel::configurationValidityChanged,
            this, [this](bool) { updateActions(); });
    connect(m_tableView->selectionModel(), &QItemSelectionModel::selectionChanged,
            this, [this]() { updateActions(); });

    m_lastDirectory = QStandardPaths::writableLocation(
        QStandardPaths::DocumentsLocation);
    rebuildReferenceCombo();
    updateActions();
}

MultiAudioInputDialog::~MultiAudioInputDialog()
{
    ++m_alignmentGeneration;
    if (m_alignmentProcess
        && m_alignmentProcess->state() != QProcess::NotRunning) {
        disconnect(m_alignmentProcess, nullptr, this, nullptr);
        m_alignmentProcess->kill();
        m_alignmentProcess->waitForFinished(100);
    }
    if (m_alignmentManifestFile) {
        delete m_alignmentManifestFile;
        m_alignmentManifestFile = nullptr;
    }
}

MultiAudioInputModel *MultiAudioInputDialog::model() const noexcept
{
    return m_model;
}

QVector<MultiAudioInput> MultiAudioInputDialog::inputs() const
{
    QVector<MultiAudioInput> result = isAlignmentAnalysisRunning()
        && !m_alignmentSnapshot.isEmpty()
        ? m_alignmentSnapshot
        : m_model->inputs();
    for (MultiAudioInput &input : result) {
        // The dialog owns the asynchronous inspector. Once the dialog closes,
        // an in-flight inspection no longer exists, so persist it as pending
        // and let the next dialog instance inspect it again.
        if (input.status == MultiAudioInput::Status::Inspecting) {
            input.status = MultiAudioInput::Status::Pending;
            input.durationMilliseconds = -1;
            input.statusDetail.clear();
        }
    }
    return result;
}

void MultiAudioInputDialog::setInputs(const QVector<MultiAudioInput> &inputs)
{
    ++m_alignmentGeneration;
    if (m_alignmentProcess) {
        disconnect(m_alignmentProcess, nullptr, this, nullptr);
        if (m_alignmentProcess->state() != QProcess::NotRunning) {
            m_alignmentProcess->kill();
            m_alignmentProcess->waitForFinished(100);
        }
        cleanupAlignmentRun(m_alignmentProcess);
    }
    m_audioFileInfo->cancel();
    m_currentInspection.clear();
    m_inspectionQueue.clear();
    m_model->setInputs(inputs);
    enqueuePendingInspections();
    startNextInspection();
    rebuildReferenceCombo();
    updateActions();
}

int MultiAudioInputDialog::referenceRow() const noexcept
{
    return m_model->referenceRow();
}

void MultiAudioInputDialog::setReferenceRow(int row)
{
    if (isAlignmentAnalysisRunning()) {
        return;
    }
    m_model->restoreReferenceRow(row);
}

void MultiAudioInputDialog::setInitialDirectory(const QString &directory)
{
    const QFileInfo info(directory);
    if (info.isDir()) {
        m_lastDirectory = info.absoluteFilePath();
    }
}

QString MultiAudioInputDialog::lastDirectory() const
{
    return m_lastDirectory;
}

void MultiAudioInputDialog::setAudioFileInfoConfiguration(
    const QString &pythonProgram, const QString &probeScript)
{
    m_audioFileInfo->setPythonProgram(pythonProgram);
    m_audioFileInfo->setProbeScript(probeScript);
    m_alignmentPythonProgram = pythonProgram.trimmed();

    const QString siblingAnalyzer = QFileInfo(probeScript).dir().filePath(
        QStringLiteral("multimic_alignment.py"));
    if (QFileInfo(siblingAnalyzer).isFile()) {
        m_alignmentScriptPath = QFileInfo(siblingAnalyzer).absoluteFilePath();
    }
}

void MultiAudioInputDialog::setAlignmentAnalyzerConfiguration(
    const QString &pythonProgram, const QString &scriptPath)
{
    m_alignmentPythonProgram = pythonProgram.trimmed();
    m_alignmentScriptPath = scriptPath.trimmed();
}

bool MultiAudioInputDialog::isAlignmentAnalysisRunning() const
{
    return m_alignmentProcess != nullptr;
}

int MultiAudioInputDialog::addFiles(const QStringList &filePaths)
{
    if (isAlignmentAnalysisRunning()) {
        return 0;
    }
    int added = 0;
    for (const QString &filePath : filePaths) {
        if (!m_model->addInput(filePath)) {
            continue;
        }
        ++added;
        const int row = m_model->rowForPath(filePath);
        if (row >= 0
            && m_model->inputAt(row).status == MultiAudioInput::Status::Pending) {
            m_inspectionQueue.append(m_model->inputAt(row).path);
        }
        const QFileInfo file(filePath);
        if (file.isFile()) {
            m_lastDirectory = file.absolutePath();
        }
    }

    startNextInspection();
    rebuildReferenceCombo();
    updateActions();
    return added;
}

void MultiAudioInputDialog::accept()
{
    if (isAlignmentAnalysisRunning()) {
        setAlignmentMessage(tr("시간 정렬 분석을 취소하거나 완료한 뒤 적용하세요."),
                            true);
        return;
    }

    QString reason;
    if (!m_model->isConfigurationValid(&reason)) {
        QMessageBox::warning(this, tr("다중 입력 확인"), reason);
        return;
    }
    QDialog::accept();
}

void MultiAudioInputDialog::reject()
{
    if (isAlignmentAnalysisRunning()) {
        cancelAlignmentAnalysis();
    }
    QDialog::reject();
}

void MultiAudioInputDialog::chooseFiles()
{
    if (isAlignmentAnalysisRunning()) {
        return;
    }
    const QStringList selectedFiles = QFileDialog::getOpenFileNames(
        this,
        tr("다중 마이크 파일 선택"),
        m_lastDirectory,
        AudioFileInfo::fileDialogFilter());
    if (selectedFiles.isEmpty()) {
        return;
    }

    const int added = addFiles(selectedFiles);
    const int skipped = selectedFiles.size() - added;
    if (skipped > 0) {
        m_summaryLabel->setText(
            tr("%1개 파일은 이미 목록에 있거나 경로가 올바르지 않아 추가하지 않았습니다.")
                .arg(skipped));
    }
}

void MultiAudioInputDialog::removeSelectedFiles()
{
    if (isAlignmentAnalysisRunning()) {
        return;
    }
    const QModelIndexList selectedRows =
        m_tableView->selectionModel()->selectedRows();
    if (selectedRows.isEmpty()) {
        return;
    }

    QVector<int> rows;
    rows.reserve(selectedRows.size());
    for (const QModelIndex &index : selectedRows) {
        if (!rows.contains(index.row())) {
            rows.append(index.row());
        }
    }
    std::sort(rows.begin(), rows.end(), std::greater<int>());

    bool removedCurrentInspection = false;
    for (int row : rows) {
        if (row < 0 || row >= m_model->rowCount()) {
            continue;
        }
        if (!m_currentInspection.isEmpty()
            && m_model->rowForPath(m_currentInspection) == row) {
            removedCurrentInspection = true;
        }
        m_model->removeInput(row);
    }

    if (removedCurrentInspection) {
        m_audioFileInfo->cancel();
        m_currentInspection.clear();
    }
    purgeInspectionQueue();
    startNextInspection();
    rebuildReferenceCombo();
    updateActions();
}

void MultiAudioInputDialog::startAlignmentAnalysis()
{
    if (isAlignmentAnalysisRunning()) {
        return;
    }

    if (!m_currentInspection.isEmpty() || m_audioFileInfo->isInspecting()
        || !m_inspectionQueue.isEmpty()) {
        setAlignmentMessage(
            tr("모든 파일 검사가 완료된 뒤 시간 정렬을 실행하세요."),
            true);
        return;
    }

    QString reason;
    if (!m_model->isConfigurationValid(&reason)) {
        setAlignmentMessage(reason, true);
        return;
    }
    if (m_alignmentPythonProgram.isEmpty()) {
        setAlignmentMessage(tr("Python 실행 경로가 설정되지 않았습니다."), true);
        return;
    }

    const QFileInfo analyzerFile(m_alignmentScriptPath);
    if (!analyzerFile.isFile()) {
        setAlignmentMessage(tr("시간 정렬 분석 도구를 찾을 수 없습니다."), true);
        return;
    }

    QVector<int> analysisRows;
    QJsonArray manifestInputs;
    int referenceInputIndex = -1;
    for (int row = 0; row < m_model->rowCount(); ++row) {
        const MultiAudioInput input = m_model->inputAt(row);
        if (!input.enabled) {
            continue;
        }
        if (row == m_model->referenceRow()) {
            referenceInputIndex = analysisRows.size();
        }
        analysisRows.append(row);
        QJsonObject manifestInput;
        manifestInput.insert(QStringLiteral("path"), input.path);
        manifestInput.insert(QStringLiteral("label"), input.alias);
        manifestInputs.append(manifestInput);
    }
    if (analysisRows.size() < 2 || referenceInputIndex < 0) {
        setAlignmentMessage(tr("사용할 파일과 기준 파일을 다시 확인하세요."), true);
        return;
    }

    QJsonObject alignment;
    alignment.insert(QStringLiteral("mode"), QStringLiteral("auto"));
    alignment.insert(QStringLiteral("manual_offsets_ms"), QJsonObject());
    QJsonObject selection;
    selection.insert(QStringLiteral("mode"), QStringLiteral("automatic"));
    selection.insert(QStringLiteral("window_seconds"), 3.0);
    selection.insert(QStringLiteral("hop_seconds"), 1.0);
    QJsonObject manifest;
    manifest.insert(QStringLiteral("version"), 1);
    manifest.insert(QStringLiteral("inputs"), manifestInputs);
    manifest.insert(QStringLiteral("reference_index"), referenceInputIndex);
    manifest.insert(QStringLiteral("alignment"), alignment);
    manifest.insert(QStringLiteral("selection"), selection);

    auto *manifestFile = new QTemporaryFile(
        QDir(QDir::tempPath()).filePath(
            QStringLiteral("korean_transcriber_alignment_XXXXXX.json")),
        this);
    manifestFile->setAutoRemove(true);
    const QByteArray manifestData = QJsonDocument(manifest).toJson(
        QJsonDocument::Compact);
    if (!manifestFile->open()
        || manifestFile->write(manifestData) != manifestData.size()
        || !manifestFile->flush()) {
        setAlignmentMessage(tr("시간 정렬 분석 요청 파일을 만들 수 없습니다."), true);
        delete manifestFile;
        return;
    }
    manifestFile->close();

    m_alignmentSnapshot = m_model->inputs();
    m_alignmentSnapshotReferenceRow = m_model->referenceRow();
    if (!m_model->startAlignmentAnalysis()) {
        setAlignmentMessage(tr("시간 정렬 분석을 시작할 수 없습니다."), true);
        delete manifestFile;
        m_alignmentSnapshot.clear();
        m_alignmentSnapshotReferenceRow = -1;
        return;
    }

    const quint64 generation = ++m_alignmentGeneration;
    auto *process = new QProcess(this);
    process->setProcessChannelMode(QProcess::SeparateChannels);
    process->setProgram(m_alignmentPythonProgram);
    process->setArguments({analyzerFile.absoluteFilePath(),
                           QStringLiteral("--multi-input-manifest"),
                           manifestFile->fileName()});
    process->setWorkingDirectory(analyzerFile.absolutePath());

    m_alignmentProcess = process;
    m_alignmentManifestFile = manifestFile;
    m_alignmentRows = analysisRows;
    m_alignmentReferenceInputIndex = referenceInputIndex;
    m_alignmentStandardOutput.clear();
    m_alignmentStandardError.clear();
    m_alignmentCancellationPending = false;

    connect(process, &QProcess::readyReadStandardOutput, this,
            [this, process, generation]() {
        if (process == m_alignmentProcess
            && generation == m_alignmentGeneration) {
            m_alignmentStandardOutput.append(process->readAllStandardOutput());
        }
    });
    connect(process, &QProcess::readyReadStandardError, this,
            [this, process, generation]() {
        if (process == m_alignmentProcess
            && generation == m_alignmentGeneration) {
            m_alignmentStandardError.append(process->readAllStandardError());
        }
    });
    connect(process, &QProcess::errorOccurred, this,
            [this, process, generation](QProcess::ProcessError error) {
        if (error == QProcess::FailedToStart) {
            alignmentProcessFailedToStart(process, generation);
        }
    });
    connect(process,
            qOverload<int, QProcess::ExitStatus>(&QProcess::finished),
            this,
            [this, process, generation](int exitCode,
                                        QProcess::ExitStatus exitStatus) {
        finishAlignmentAnalysis(process, generation, exitCode,
                                static_cast<int>(exitStatus));
    });

    setAlignmentMessage(tr("시간 정렬을 분석하는 중입니다. 필요하면 ‘분석 취소’를 누르세요."));
    updateActions();
    process->start();
}

void MultiAudioInputDialog::cancelAlignmentAnalysis()
{
    if (!isAlignmentAnalysisRunning() || m_alignmentCancellationPending) {
        return;
    }

    m_alignmentCancellationPending = true;
    const quint64 generation = m_alignmentGeneration;
    QProcess *process = m_alignmentProcess;
    setAlignmentMessage(tr("시간 정렬 분석을 취소하는 중입니다..."));
    updateActions();
    process->terminate();
    QTimer::singleShot(3000, process, [this, process, generation]() {
        if (process == m_alignmentProcess
            && generation == m_alignmentGeneration
            && process->state() != QProcess::NotRunning) {
            process->kill();
        }
    });
}

void MultiAudioInputDialog::editManualOffset()
{
    if (isAlignmentAnalysisRunning()) {
        return;
    }
    const int row = selectedInputRow();
    if (row < 0 || row == m_model->referenceRow()) {
        return;
    }

    const MultiAudioInput input = m_model->inputAt(row);
    if (!input.enabled || input.status != MultiAudioInput::Status::Ready) {
        return;
    }
    const auto alignmentStatus = input.alignmentStatus;
    if (alignmentStatus != MultiAudioInput::AlignmentStatus::Warning
        && alignmentStatus != MultiAudioInput::AlignmentStatus::Failed
        && alignmentStatus != MultiAudioInput::AlignmentStatus::Manual) {
        return;
    }
    if (!input.manualAlignmentCorrectionAllowed) {
        return;
    }

    const qint64 currentOffset = input.hasAlignmentOffset
        ? input.alignmentOffsetMilliseconds
        : 0;
    const int initialValue = static_cast<int>(std::clamp<qint64>(
        currentOffset,
        std::numeric_limits<int>::min(),
        std::numeric_limits<int>::max()));
    bool accepted = false;
    const int offset = QInputDialog::getInt(
        this,
        tr("수동 정렬 오프셋"),
        tr("기준 파일보다 늦게 시작한 파일은 양수, 먼저 시작한 파일은 음수로 입력하세요. (ms)"),
        initialValue,
        std::numeric_limits<int>::min(),
        std::numeric_limits<int>::max(),
        1,
        &accepted);
    if (!accepted) {
        return;
    }

    if (m_model->setManualAlignmentOffset(
            input.path,
            offset,
            tr("사용자가 자동 정렬 결과를 검토하고 수동 오프셋을 지정했습니다."))) {
        setAlignmentMessage(
            tr("%1의 수동 오프셋을 %2 ms로 설정했습니다.")
                .arg(input.alias)
                .arg(offset));
        updateActions();
    }
}

void MultiAudioInputDialog::finishAlignmentAnalysis(QProcess *process,
                                                     quint64 generation,
                                                     int exitCode,
                                                     int exitStatus)
{
    if (process != m_alignmentProcess || generation != m_alignmentGeneration) {
        process->deleteLater();
        return;
    }

    m_alignmentStandardOutput.append(process->readAllStandardOutput());
    m_alignmentStandardError.append(process->readAllStandardError());

    if (m_alignmentCancellationPending) {
        restoreAlignmentSnapshot();
        cleanupAlignmentRun(process);
        setAlignmentMessage(tr("시간 정렬 분석을 취소했습니다. 이전 입력과 정렬 결과를 유지합니다."));
        updateActions();
        return;
    }

    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(
        m_alignmentStandardOutput.trimmed(), &parseError);
    const bool normalExit =
        exitStatus == static_cast<int>(QProcess::NormalExit);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        restoreAlignmentSnapshot();
        cleanupAlignmentRun(process);
        setAlignmentMessage(
            normalExit && exitCode == 0
                ? tr("시간 정렬 분석 응답을 해석할 수 없습니다.")
                : tr("시간 정렬 분석 프로세스가 비정상 종료되었습니다."),
            true);
        updateActions();
        return;
    }

    const QJsonObject payload = document.object();
    const QJsonValue okValue = payload.value(QStringLiteral("ok"));
    if (normalExit && okValue.isBool() && !okValue.toBool()
        && payload.value(QStringLiteral("kind")).toString()
            == QStringLiteral("preparation")) {
        QString preparationMessage;
        if (applyPreparationFailures(payload, &preparationMessage)) {
            cleanupAlignmentRun(process);
            setAlignmentMessage(preparationMessage, true);
            updateActions();
            return;
        }
    }
    if (!okValue.isBool() || !okValue.toBool()
        || !normalExit || exitCode != 0) {
        QString message = payload.value(QStringLiteral("message"))
                              .toString().trimmed();
        if (message.isEmpty()) {
            message = tr("시간 정렬 분석을 완료할 수 없습니다. 입력 파일과 Python 환경을 확인하세요.");
        }
        restoreAlignmentSnapshot();
        cleanupAlignmentRun(process);
        setAlignmentMessage(message, true);
        updateActions();
        return;
    }

    int version = -1;
    const QJsonObject alignment = payload.value(QStringLiteral("alignment"))
                                      .toObject();
    int returnedReferenceIndex = -1;
    const QJsonArray returnedInputs =
        alignment.value(QStringLiteral("inputs")).toArray();
    bool validPayload = jsonInteger(payload.value(QStringLiteral("version")),
                                    1, 1, &version)
        && !alignment.isEmpty()
        && jsonInteger(alignment.value(QStringLiteral("reference_index")),
                       0, m_alignmentRows.size() - 1,
                       &returnedReferenceIndex)
        && returnedReferenceIndex == m_alignmentReferenceInputIndex
        && returnedInputs.size() == m_alignmentRows.size();

    QVector<ParsedAlignmentResult> results;
    results.resize(m_alignmentRows.size());
    QVector<bool> seen(m_alignmentRows.size(), false);
    if (validPayload) {
        for (const QJsonValue &value : returnedInputs) {
            if (!value.isObject()) {
                validPayload = false;
                break;
            }
            const QJsonObject object = value.toObject();
            int inputIndex = -1;
            int resultReferenceIndex = -1;
            double confidence = 0.0;
            if (!jsonInteger(object.value(QStringLiteral("input_index")),
                             0, m_alignmentRows.size() - 1, &inputIndex)
                || seen.at(inputIndex)
                || !jsonInteger(object.value(QStringLiteral("reference_index")),
                                0, m_alignmentRows.size() - 1,
                                &resultReferenceIndex)
                || resultReferenceIndex != m_alignmentReferenceInputIndex
                || !jsonFiniteNumber(object.value(QStringLiteral("confidence")),
                                     &confidence)
                || confidence < 0.0 || confidence > 1.0) {
                validPayload = false;
                break;
            }

            ParsedAlignmentResult result;
            result.inputIndex = inputIndex;
            result.confidence = confidence;
            result.detail = resultDetail(object);

            const QString status = object.value(QStringLiteral("status"))
                                       .toString();
            if (status == QStringLiteral("aligned")) {
                result.status = MultiAudioInput::AlignmentStatus::Aligned;
            } else if (status == QStringLiteral("manual_review")) {
                result.status = MultiAudioInput::AlignmentStatus::Warning;
            } else if (status == QStringLiteral("failed")) {
                result.status = MultiAudioInput::AlignmentStatus::Failed;
            } else {
                validPayload = false;
                break;
            }

            const QJsonValue offsetValue = object.value(QStringLiteral("offset_ms"));
            double exactOffsetMilliseconds = 0.0;
            if (!offsetValue.isNull() && !offsetValue.isUndefined()) {
                if (!jsonFiniteNumber(offsetValue, &exactOffsetMilliseconds)
                    || static_cast<long double>(exactOffsetMilliseconds)
                        < static_cast<long double>(std::numeric_limits<qint64>::min())
                    || static_cast<long double>(exactOffsetMilliseconds)
                        > static_cast<long double>(std::numeric_limits<qint64>::max())) {
                    validPayload = false;
                    break;
                }
                result.hasOffset = true;
                result.offsetMilliseconds = qRound64(exactOffsetMilliseconds);
            }

            const QJsonValue driftValue = object.value(QStringLiteral("drift_ppm"));
            if (!driftValue.isNull() && !driftValue.isUndefined()) {
                if (!jsonFiniteNumber(driftValue, &result.driftPpm)) {
                    validPayload = false;
                    break;
                }
                result.hasDrift = true;
            }
            if (result.status == MultiAudioInput::AlignmentStatus::Aligned
                && !result.hasOffset) {
                validPayload = false;
                break;
            }
            if (inputIndex == returnedReferenceIndex
                && (result.status
                        != MultiAudioInput::AlignmentStatus::Aligned
                    || !result.hasOffset
                    || exactOffsetMilliseconds != 0.0
                    || result.confidence != 1.0
                    || (result.hasDrift && result.driftPpm != 0.0))) {
                validPayload = false;
                break;
            }

            seen[inputIndex] = true;
            results[inputIndex] = result;
        }
    }
    if (validPayload
        && std::any_of(seen.cbegin(), seen.cend(), [](bool value) { return !value; })) {
        validPayload = false;
    }

    if (validPayload) {
        if (m_alignmentSnapshot.size() != m_model->rowCount()
            || m_alignmentSnapshotReferenceRow != m_model->referenceRow()) {
            validPayload = false;
        } else {
            for (int inputIndex = 0; inputIndex < m_alignmentRows.size(); ++inputIndex) {
                const int row = m_alignmentRows.at(inputIndex);
                if (row < 0 || row >= m_model->rowCount()
                    || m_model->inputAt(row).path
                        != m_alignmentSnapshot.at(row).path) {
                    validPayload = false;
                    break;
                }
            }
        }
    }

    if (!validPayload) {
        restoreAlignmentSnapshot();
        cleanupAlignmentRun(process);
        setAlignmentMessage(tr("시간 정렬 분석 응답의 형식이나 입력 순서가 올바르지 않습니다."),
                            true);
        updateActions();
        return;
    }

    int alignedCount = 0;
    int warningCount = 0;
    int failedCount = 0;
    bool applied = true;
    for (const ParsedAlignmentResult &result : results) {
        const int row = m_alignmentRows.at(result.inputIndex);
        const QString path = m_model->inputAt(row).path;
        if (!m_model->applyAlignmentResult(
                path,
                result.status,
                result.hasOffset,
                result.offsetMilliseconds,
                result.confidence,
                result.hasDrift,
                result.driftPpm,
                result.detail)) {
            applied = false;
            break;
        }
        if (result.status == MultiAudioInput::AlignmentStatus::Aligned) {
            ++alignedCount;
        } else if (result.status == MultiAudioInput::AlignmentStatus::Warning) {
            ++warningCount;
        } else {
            ++failedCount;
        }
    }

    if (!applied) {
        restoreAlignmentSnapshot();
        cleanupAlignmentRun(process);
        setAlignmentMessage(tr("시간 정렬 결과를 입력 목록에 적용할 수 없습니다."), true);
        updateActions();
        return;
    }

    cleanupAlignmentRun(process);
    setAlignmentMessage(
        tr("시간 정렬 분석 완료: 성공 %1개 · 검토 필요 %2개 · 실패 %3개")
            .arg(alignedCount)
            .arg(warningCount)
            .arg(failedCount),
        warningCount > 0 || failedCount > 0);
    updateActions();
}

bool MultiAudioInputDialog::applyPreparationFailures(
    const QJsonObject &payload, QString *message)
{
    int version = -1;
    const QJsonValue errorsValue = payload.value(QStringLiteral("errors"));
    if (!jsonInteger(payload.value(QStringLiteral("version")), 1, 1,
                     &version)
        || !errorsValue.isArray()) {
        return false;
    }

    struct PreparationFailure
    {
        int inputIndex = -1;
        QString detail;
    };

    const QJsonArray errors = errorsValue.toArray();
    QVector<PreparationFailure> failures;
    failures.reserve(errors.size());
    QSet<int> seenIndexes;
    for (const QJsonValue &value : errors) {
        if (!value.isObject()) {
            return false;
        }
        const QJsonObject error = value.toObject();
        int inputIndex = -1;
        if (!jsonInteger(error.value(QStringLiteral("input_index")),
                         0, m_alignmentRows.size() - 1, &inputIndex)
            || seenIndexes.contains(inputIndex)) {
            return false;
        }

        QString detail = error.value(QStringLiteral("message"))
                             .toString().trimmed();
        const QString code = error.value(QStringLiteral("code"))
                                 .toString().trimmed();
        if (detail.isEmpty()) {
            detail = tr("오디오 정렬 준비에 실패했습니다.");
        }
        if (!code.isEmpty()) {
            detail = QStringLiteral("[%1] %2").arg(code, detail);
        }
        failures.append({inputIndex, detail});
        seenIndexes.insert(inputIndex);
    }
    if (failures.isEmpty()) {
        return false;
    }

    restoreAlignmentSnapshot();
    QStringList failureDescriptions;
    for (const PreparationFailure &failure : failures) {
        const int row = m_alignmentRows.at(failure.inputIndex);
        if (row < 0 || row >= m_model->rowCount()) {
            restoreAlignmentSnapshot();
            return false;
        }
        const MultiAudioInput input = m_model->inputAt(row);
        if (!m_model->applyAlignmentResult(
                input.path,
                MultiAudioInput::AlignmentStatus::Failed,
                false,
                0,
                0.0,
                false,
                0.0,
                failure.detail,
                false)) {
            restoreAlignmentSnapshot();
            return false;
        }
        failureDescriptions.append(
            tr("%1: %2").arg(input.alias, failure.detail));
    }

    if (message) {
        *message = tr("시간 정렬 준비에 실패한 파일: %1")
                       .arg(failureDescriptions.join(QStringLiteral(" · ")));
    }
    return true;
}

void MultiAudioInputDialog::alignmentProcessFailedToStart(QProcess *process,
                                                           quint64 generation)
{
    if (process != m_alignmentProcess || generation != m_alignmentGeneration) {
        return;
    }
    restoreAlignmentSnapshot();
    cleanupAlignmentRun(process);
    setAlignmentMessage(tr("Python 시간 정렬 분석 프로세스를 시작할 수 없습니다."), true);
    updateActions();
}

void MultiAudioInputDialog::restoreAlignmentSnapshot()
{
    if (m_alignmentSnapshot.isEmpty()) {
        m_model->cancelAlignmentAnalysis();
        return;
    }
    m_model->setInputs(m_alignmentSnapshot);
    m_model->restoreReferenceRow(m_alignmentSnapshotReferenceRow);
}

void MultiAudioInputDialog::cleanupAlignmentRun(QProcess *process)
{
    if (process == m_alignmentProcess) {
        m_alignmentProcess = nullptr;
    }
    if (process) {
        process->deleteLater();
    }
    if (m_alignmentManifestFile) {
        m_alignmentManifestFile->deleteLater();
        m_alignmentManifestFile = nullptr;
    }
    m_alignmentStandardOutput.clear();
    m_alignmentStandardError.clear();
    m_alignmentRows.clear();
    m_alignmentSnapshot.clear();
    m_alignmentReferenceInputIndex = -1;
    m_alignmentSnapshotReferenceRow = -1;
    m_alignmentCancellationPending = false;
}

void MultiAudioInputDialog::setAlignmentMessage(const QString &message,
                                                 bool error)
{
    m_alignmentMessageLabel->setText(message);
    m_alignmentMessageLabel->setStyleSheet(
        error ? QStringLiteral("color: #b00020;") : QString());
}

int MultiAudioInputDialog::selectedInputRow() const
{
    if (!m_tableView->selectionModel()) {
        return -1;
    }
    const QModelIndexList rows = m_tableView->selectionModel()->selectedRows();
    return rows.size() == 1 ? rows.constFirst().row() : -1;
}

void MultiAudioInputDialog::inspectionSucceeded(const AudioFileMetadata &metadata)
{
    const int currentRow = m_model->rowForPath(m_currentInspection);
    const int resultRow = m_model->rowForPath(metadata.filePath);
    if (currentRow >= 0 && resultRow == currentRow) {
        m_model->setInspectionSucceeded(metadata.filePath,
                                        metadata.durationMilliseconds);
    }
    m_currentInspection.clear();
    startNextInspection();
    updateActions();
}

void MultiAudioInputDialog::inspectionFailed(const QString &filePath,
                                             const QString &message)
{
    const int currentRow = m_model->rowForPath(m_currentInspection);
    const int resultRow = m_model->rowForPath(filePath);
    if (currentRow >= 0 && resultRow == currentRow) {
        m_model->setInspectionFailed(filePath, message);
    }
    m_currentInspection.clear();
    startNextInspection();
    updateActions();
}

void MultiAudioInputDialog::rebuildReferenceCombo()
{
    m_rebuildingReferenceCombo = true;
    m_referenceComboBox->clear();

    const int currentReference = m_model->referenceRow();
    int comboReferenceIndex = -1;
    for (int row = 0; row < m_model->rowCount(); ++row) {
        const MultiAudioInput input = m_model->inputAt(row);
        if (!input.enabled || input.status != MultiAudioInput::Status::Ready) {
            continue;
        }
        const QString display = tr("%1 — %2")
                                    .arg(input.alias,
                                         QFileInfo(input.path).fileName());
        m_referenceComboBox->addItem(display, row);
        if (row == currentReference) {
            comboReferenceIndex = m_referenceComboBox->count() - 1;
        }
    }
    m_referenceComboBox->setCurrentIndex(comboReferenceIndex);
    m_rebuildingReferenceCombo = false;
    updateActions();
}

void MultiAudioInputDialog::updateActions()
{
    const bool analysisRunning = isAlignmentAnalysisRunning();
    const bool inspectionRunning = !m_currentInspection.isEmpty()
        || m_audioFileInfo->isInspecting() || !m_inspectionQueue.isEmpty();
    const bool hasSelection = m_tableView->selectionModel()
        && m_tableView->selectionModel()->hasSelection();
    m_tableView->setEnabled(!analysisRunning);
    m_addButton->setEnabled(!analysisRunning);
    m_removeButton->setEnabled(hasSelection && !analysisRunning);
    m_referenceComboBox->setEnabled(
        m_referenceComboBox->count() > 0 && !analysisRunning);

    QString reason;
    const bool valid = m_model->isConfigurationValid(&reason);
    m_analyzeAlignmentButton->setEnabled(
        valid && !inspectionRunning && !analysisRunning);
    m_cancelAlignmentButton->setEnabled(
        analysisRunning && !m_alignmentCancellationPending);
    m_alignmentProgressBar->setVisible(analysisRunning);

    bool canEditManualOffset = false;
    const int selectedRow = selectedInputRow();
    if (!analysisRunning && selectedRow >= 0
        && selectedRow != m_model->referenceRow()) {
        const MultiAudioInput input = m_model->inputAt(selectedRow);
        canEditManualOffset = input.enabled
            && input.status == MultiAudioInput::Status::Ready
            && input.manualAlignmentCorrectionAllowed
            && (input.alignmentStatus
                    == MultiAudioInput::AlignmentStatus::Warning
                || input.alignmentStatus
                    == MultiAudioInput::AlignmentStatus::Failed
                || input.alignmentStatus
                    == MultiAudioInput::AlignmentStatus::Manual);
    }
    m_manualOffsetButton->setEnabled(canEditManualOffset);
    m_buttonBox->button(QDialogButtonBox::Ok)->setEnabled(
        valid && !analysisRunning);
    updateSummary();
}

void MultiAudioInputDialog::enqueuePendingInspections()
{
    for (int row = 0; row < m_model->rowCount(); ++row) {
        const MultiAudioInput input = m_model->inputAt(row);
        if (input.status == MultiAudioInput::Status::Pending
            && !m_inspectionQueue.contains(input.path)) {
            m_inspectionQueue.append(input.path);
        }
    }
}

void MultiAudioInputDialog::startNextInspection()
{
    if (!m_currentInspection.isEmpty() || m_audioFileInfo->isInspecting()) {
        return;
    }

    while (!m_inspectionQueue.isEmpty()) {
        const QString filePath = m_inspectionQueue.takeFirst();
        const int row = m_model->rowForPath(filePath);
        if (row < 0
            || m_model->inputAt(row).status != MultiAudioInput::Status::Pending) {
            continue;
        }
        m_currentInspection = m_model->inputAt(row).path;
        m_model->setInspectionStarted(m_currentInspection);
        m_audioFileInfo->inspect(m_currentInspection);
        return;
    }
}

void MultiAudioInputDialog::purgeInspectionQueue()
{
    QStringList remaining;
    for (const QString &filePath : m_inspectionQueue) {
        if (m_model->rowForPath(filePath) >= 0 && !remaining.contains(filePath)) {
            remaining.append(filePath);
        }
    }
    m_inspectionQueue = remaining;
}

void MultiAudioInputDialog::updateSummary()
{
    int enabledCount = 0;
    int readyCount = 0;
    int inspectingCount = 0;
    int errorCount = 0;
    int excludedCount = 0;
    int alignedCount = 0;
    int manualCount = 0;
    int warningCount = 0;
    int alignmentFailedCount = 0;
    int notAnalyzedCount = 0;
    for (const MultiAudioInput &input : m_model->inputs()) {
        if (input.enabled) {
            ++enabledCount;
        } else {
            ++excludedCount;
        }
        if (input.status == MultiAudioInput::Status::Ready) {
            ++readyCount;
        } else if (input.status == MultiAudioInput::Status::Pending
                   || input.status == MultiAudioInput::Status::Inspecting) {
            ++inspectingCount;
        } else if (input.status == MultiAudioInput::Status::Error) {
            ++errorCount;
        }

        if (!input.enabled || input.status != MultiAudioInput::Status::Ready) {
            continue;
        }
        switch (input.alignmentStatus) {
        case MultiAudioInput::AlignmentStatus::Aligned:
            ++alignedCount;
            break;
        case MultiAudioInput::AlignmentStatus::Manual:
            ++manualCount;
            break;
        case MultiAudioInput::AlignmentStatus::Warning:
            ++warningCount;
            break;
        case MultiAudioInput::AlignmentStatus::Failed:
            ++alignmentFailedCount;
            break;
        case MultiAudioInput::AlignmentStatus::NotAnalyzed:
        case MultiAudioInput::AlignmentStatus::Analyzing:
            ++notAnalyzedCount;
            break;
        }
    }

    QString reason;
    const bool valid = m_model->isConfigurationValid(&reason);
    QString alignmentReason;
    const bool alignmentResolved = m_model->isAlignmentResolved(
        &alignmentReason);
    QStringList messages;
    messages.append(tr("전체 %1개 · 사용 %2개 · 준비 %3개")
                        .arg(m_model->rowCount())
                        .arg(enabledCount)
                        .arg(readyCount));
    if (excludedCount > 0) {
        messages.append(tr("제외 %1개").arg(excludedCount));
    }
    if (inspectingCount > 0) {
        messages.append(tr("%1개 확인 중").arg(inspectingCount));
    }
    if (errorCount > 0) {
        messages.append(tr("%1개 오류").arg(errorCount));
    }
    if (m_model->rowCount() > 8) {
        messages.append(tr("권장 파일 수(8개)를 초과했습니다"));
    }
    if (alignedCount > 0 || manualCount > 0 || warningCount > 0
        || alignmentFailedCount > 0 || notAnalyzedCount > 0) {
        messages.append(
            tr("정렬 성공 %1 · 수동 %2 · 경고 %3 · 실패 %4 · 미분석 %5")
                .arg(alignedCount)
                .arg(manualCount)
                .arg(warningCount)
                .arg(alignmentFailedCount)
                .arg(notAnalyzedCount));
    }
    if (!valid) {
        messages.append(reason);
    } else if (isAlignmentAnalysisRunning()) {
        messages.append(tr("시간 정렬 분석 중"));
    } else if (alignmentResolved) {
        messages.append(tr("정렬 조치 완료"));
    } else {
        messages.append(alignmentReason);
    }
    m_summaryLabel->setText(messages.join(QStringLiteral(" · ")));
}
