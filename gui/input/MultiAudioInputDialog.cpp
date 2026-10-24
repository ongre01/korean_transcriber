#include "MultiAudioInputDialog.h"

#include "AudioFileInfo.h"
#include "../app/EnginePaths.h"

#include <QAbstractItemView>
#include <QComboBox>
#include <QDialogButtonBox>
#include <QDir>
#include <QFileDialog>
#include <QFileInfo>
#include <QGroupBox>
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
#include <QSignalBlocker>
#include <QSplitter>
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
    , m_selectionModel(new MultiAudioSelectionModel(this))
    , m_audioFileInfo(new AudioFileInfo(this))
{
    const EnginePaths enginePaths = EnginePaths::discover();
    m_alignmentPythonProgram = m_audioFileInfo->pythonProgram();
    m_alignmentScriptPath = enginePaths.assetPath(
        QStringLiteral("multimic_alignment.py"));
    m_qualityPythonProgram = m_alignmentPythonProgram;
    m_qualityScriptPath = enginePaths.assetPath(
        QStringLiteral("multimic_quality.py"));

    setObjectName(QStringLiteral("multiAudioInputDialog"));
    setWindowTitle(tr("다중 마이크 파일"));
    setModal(true);
    resize(1180, 880);
    setMinimumSize(900, 640);

    auto *dialogLayout = new QVBoxLayout(this);
    auto *splitter = new QSplitter(Qt::Vertical, this);
    splitter->setObjectName(QStringLiteral("multiAudioSplitter"));
    splitter->setChildrenCollapsible(false);
    dialogLayout->addWidget(splitter, 1);

    auto *inputPanel = new QWidget(splitter);
    auto *mainLayout = new QVBoxLayout(inputPanel);
    mainLayout->setContentsMargins(0, 0, 0, 0);
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

    auto *qualityGroup = new QGroupBox(tr("음질 분석 · 채널 선택"), splitter);
    qualityGroup->setObjectName(QStringLiteral("multiAudioQualityGroupBox"));
    auto *qualityLayout = new QVBoxLayout(qualityGroup);
    auto *qualityControlsLayout = new QHBoxLayout;
    m_analyzeQualityButton = new QPushButton(tr("음질 분석"), this);
    m_analyzeQualityButton->setObjectName(
        QStringLiteral("multiAudioAnalyzeQualityButton"));
    m_cancelQualityButton = new QPushButton(tr("분석 취소"), this);
    m_cancelQualityButton->setObjectName(
        QStringLiteral("multiAudioCancelQualityButton"));
    m_qualityProgressBar = new QProgressBar(this);
    m_qualityProgressBar->setObjectName(
        QStringLiteral("multiAudioQualityProgressBar"));
    m_qualityProgressBar->setRange(0, 0);
    m_qualityProgressBar->setTextVisible(false);
    m_qualityProgressBar->setVisible(false);
    qualityControlsLayout->addWidget(m_analyzeQualityButton);
    qualityControlsLayout->addWidget(m_cancelQualityButton);
    qualityControlsLayout->addWidget(m_qualityProgressBar, 1);
    qualityLayout->addLayout(qualityControlsLayout);

    m_selectionTableView = new QTableView(this);
    m_selectionTableView->setObjectName(
        QStringLiteral("multiAudioSelectionTableView"));
    m_selectionTableView->setModel(m_selectionModel);
    m_selectionTableView->setSelectionBehavior(QAbstractItemView::SelectRows);
    m_selectionTableView->setSelectionMode(QAbstractItemView::ExtendedSelection);
    m_selectionTableView->setEditTriggers(QAbstractItemView::NoEditTriggers);
    m_selectionTableView->setAlternatingRowColors(true);
    m_selectionTableView->setSortingEnabled(false);
    m_selectionTableView->verticalHeader()->setVisible(false);
    QHeaderView *selectionHeader = m_selectionTableView->horizontalHeader();
    selectionHeader->setSectionResizeMode(QHeaderView::ResizeToContents);
    selectionHeader->setSectionResizeMode(MultiAudioSelectionModel::ReasonColumn,
                                          QHeaderView::Stretch);
    qualityLayout->addWidget(m_selectionTableView, 1);

    auto *overrideLayout = new QHBoxLayout;
    overrideLayout->addWidget(new QLabel(tr("선택한 구간의 마이크"), this));
    m_overrideComboBox = new QComboBox(this);
    m_overrideComboBox->setObjectName(QStringLiteral("multiAudioOverrideComboBox"));
    m_overrideComboBox->setMinimumContentsLength(16);
    m_overrideComboBox->setSizeAdjustPolicy(
        QComboBox::AdjustToMinimumContentsLengthWithIcon);
    m_applyOverrideButton = new QPushButton(tr("강제 선택"), this);
    m_applyOverrideButton->setObjectName(
        QStringLiteral("multiAudioApplyOverrideButton"));
    m_clearOverrideButton = new QPushButton(tr("자동으로 되돌리기"), this);
    m_clearOverrideButton->setObjectName(
        QStringLiteral("multiAudioClearOverrideButton"));
    m_clearAllOverridesButton = new QPushButton(tr("수동 선택 모두 해제"), this);
    m_clearAllOverridesButton->setObjectName(
        QStringLiteral("multiAudioClearAllOverridesButton"));
    // The application has no audio playback yet, so the preview stays a
    // visible but conditional follow-up instead of a working control.
    m_previewButton = new QPushButton(tr("통합 음성 미리 듣기 (후속 지원)"), this);
    m_previewButton->setObjectName(QStringLiteral("multiAudioPreviewButton"));
    m_previewButton->setEnabled(false);
    m_previewButton->setToolTip(
        tr("현재 앱에는 오디오 재생 기능이 없어 통합 음성 미리 듣기는 재생 기능을 지원할 때 제공하는 후속(조건부) 범위입니다."));
    overrideLayout->addWidget(m_overrideComboBox);
    overrideLayout->addWidget(m_applyOverrideButton);
    overrideLayout->addWidget(m_clearOverrideButton);
    overrideLayout->addWidget(m_clearAllOverridesButton);
    overrideLayout->addStretch();
    overrideLayout->addWidget(m_previewButton);
    qualityLayout->addLayout(overrideLayout);

    m_qualityMessageLabel = new QLabel(this);
    m_qualityMessageLabel->setObjectName(
        QStringLiteral("multiAudioQualityMessageLabel"));
    m_qualityMessageLabel->setWordWrap(true);
    qualityLayout->addWidget(m_qualityMessageLabel);

    m_selectionSummaryLabel = new QLabel(this);
    m_selectionSummaryLabel->setObjectName(
        QStringLiteral("multiAudioSelectionSummaryLabel"));
    m_selectionSummaryLabel->setWordWrap(true);
    qualityLayout->addWidget(m_selectionSummaryLabel);

    auto *selectionScopeLabel = new QLabel(
        tr("구간별 자동 선택 근거(점수·사유)를 표로 확인하고, 필요한 구간만 다른 마이크로 강제 선택하세요. "
           "노란 행이 수동 선택이며 통합 음성 합성과 전사에는 ‘최종 선택’이 사용됩니다. "
           "파형·고급 타임라인 보기는 후속 버전에서 제공합니다."),
        this);
    selectionScopeLabel->setWordWrap(true);
    qualityLayout->addWidget(selectionScopeLabel);
    splitter->setStretchFactor(0, 1);
    splitter->setStretchFactor(1, 1);

    m_buttonBox = new QDialogButtonBox(QDialogButtonBox::Ok
                                       | QDialogButtonBox::Cancel,
                                       this);
    m_buttonBox->button(QDialogButtonBox::Ok)->setText(tr("적용"));
    m_buttonBox->button(QDialogButtonBox::Cancel)->setText(tr("취소"));
    dialogLayout->addWidget(m_buttonBox);

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

    connect(m_analyzeQualityButton, &QPushButton::clicked,
            this, &MultiAudioInputDialog::startQualityAnalysis);
    connect(m_cancelQualityButton, &QPushButton::clicked,
            this, &MultiAudioInputDialog::cancelQualityAnalysis);
    connect(m_applyOverrideButton, &QPushButton::clicked,
            this, &MultiAudioInputDialog::applySelectedOverride);
    connect(m_clearOverrideButton, &QPushButton::clicked,
            this, &MultiAudioInputDialog::clearSelectedOverrides);
    connect(m_clearAllOverridesButton, &QPushButton::clicked,
            this, &MultiAudioInputDialog::clearAllOverrides);
    connect(m_overrideComboBox, qOverload<int>(&QComboBox::currentIndexChanged),
            this, [this](int) { updateActions(); });
    connect(m_selectionTableView->selectionModel(),
            &QItemSelectionModel::selectionChanged,
            this, [this]() { updateActions(); });
    connect(m_selectionModel, &MultiAudioSelectionModel::overridesChanged,
            this, &MultiAudioInputDialog::updateActions);
    // A selection result is only valid for the inputs and alignment it was
    // computed from, so every input change re-evaluates it.
    connect(m_model, &QAbstractItemModel::modelReset,
            this, &MultiAudioInputDialog::refreshSelectionContext);
    connect(m_model, &QAbstractItemModel::rowsInserted,
            this, &MultiAudioInputDialog::refreshSelectionContext);
    connect(m_model, &QAbstractItemModel::rowsRemoved,
            this, &MultiAudioInputDialog::refreshSelectionContext);
    connect(m_model, &QAbstractItemModel::dataChanged,
            this, &MultiAudioInputDialog::refreshSelectionContext);
    connect(m_model, &MultiAudioInputModel::referenceRowChanged,
            this, &MultiAudioInputDialog::refreshSelectionContext);

    m_lastDirectory = QStandardPaths::writableLocation(
        QStandardPaths::DocumentsLocation);
    rebuildReferenceCombo();
    refreshSelectionContext();
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
    ++m_qualityGeneration;
    if (m_qualityProcess
        && m_qualityProcess->state() != QProcess::NotRunning) {
        disconnect(m_qualityProcess, nullptr, this, nullptr);
        m_qualityProcess->kill();
        m_qualityProcess->waitForFinished(100);
    }
    if (m_qualityManifestFile) {
        delete m_qualityManifestFile;
        m_qualityManifestFile = nullptr;
    }
}

MultiAudioInputModel *MultiAudioInputDialog::model() const noexcept
{
    return m_model;
}

MultiAudioSelectionModel *MultiAudioInputDialog::selectionModel() const noexcept
{
    return m_selectionModel;
}

MultiAudioSelectionPlan MultiAudioInputDialog::selectionPlan() const
{
    return m_selectionModel->plan();
}

void MultiAudioInputDialog::setSelectionPlan(const MultiAudioSelectionPlan &plan)
{
    if (isQualityAnalysisRunning()) {
        return;
    }
    m_selectionModel->setPlan(plan);
    setQualityMessage(QString());
    refreshSelectionContext();
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
    stopQualityProcess();
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
    if (isAlignmentAnalysisRunning() || isQualityAnalysisRunning()) {
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
    m_qualityPythonProgram = m_alignmentPythonProgram;

    const QString siblingAnalyzer = QFileInfo(probeScript).dir().filePath(
        QStringLiteral("multimic_alignment.py"));
    if (QFileInfo(siblingAnalyzer).isFile()) {
        m_alignmentScriptPath = QFileInfo(siblingAnalyzer).absoluteFilePath();
    }
    const QString siblingQualityAnalyzer = QFileInfo(probeScript).dir().filePath(
        QStringLiteral("multimic_quality.py"));
    if (QFileInfo(siblingQualityAnalyzer).isFile()) {
        m_qualityScriptPath = QFileInfo(siblingQualityAnalyzer).absoluteFilePath();
    }
}

void MultiAudioInputDialog::setAlignmentAnalyzerConfiguration(
    const QString &pythonProgram, const QString &scriptPath)
{
    m_alignmentPythonProgram = pythonProgram.trimmed();
    m_alignmentScriptPath = scriptPath.trimmed();
}

void MultiAudioInputDialog::setQualityAnalyzerConfiguration(
    const QString &pythonProgram, const QString &scriptPath)
{
    m_qualityPythonProgram = pythonProgram.trimmed();
    m_qualityScriptPath = scriptPath.trimmed();
}

bool MultiAudioInputDialog::isAlignmentAnalysisRunning() const
{
    return m_alignmentProcess != nullptr;
}

bool MultiAudioInputDialog::isQualityAnalysisRunning() const
{
    return m_qualityProcess != nullptr;
}

int MultiAudioInputDialog::addFiles(const QStringList &filePaths)
{
    if (isAlignmentAnalysisRunning() || isQualityAnalysisRunning()) {
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
    if (isQualityAnalysisRunning()) {
        setQualityMessage(tr("음질 분석을 취소하거나 완료한 뒤 적용하세요."), true);
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
    if (isQualityAnalysisRunning()) {
        cancelQualityAnalysis();
    }
    QDialog::reject();
}

void MultiAudioInputDialog::chooseFiles()
{
    if (isAlignmentAnalysisRunning() || isQualityAnalysisRunning()) {
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
    if (isAlignmentAnalysisRunning() || isQualityAnalysisRunning()) {
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
    if (isAlignmentAnalysisRunning() || isQualityAnalysisRunning()) {
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
    if (isAlignmentAnalysisRunning() || isQualityAnalysisRunning()) {
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

QVector<int> MultiAudioInputDialog::selectedSegmentRows() const
{
    QVector<int> rows;
    if (!m_selectionTableView->selectionModel()) {
        return rows;
    }
    for (const QModelIndex &index :
         m_selectionTableView->selectionModel()->selectedRows()) {
        rows.append(index.row());
    }
    std::sort(rows.begin(), rows.end());
    return rows;
}

void MultiAudioInputDialog::startQualityAnalysis()
{
    if (isAlignmentAnalysisRunning() || isQualityAnalysisRunning()) {
        return;
    }

    if (!m_currentInspection.isEmpty() || m_audioFileInfo->isInspecting()
        || !m_inspectionQueue.isEmpty()) {
        setQualityMessage(tr("모든 파일 검사가 완료된 뒤 음질 분석을 실행하세요."),
                          true);
        return;
    }

    QString reason;
    if (!m_model->isAlignmentResolved(&reason)) {
        setQualityMessage(reason, true);
        return;
    }
    if (m_qualityPythonProgram.isEmpty()) {
        setQualityMessage(tr("Python 실행 경로가 설정되지 않았습니다."), true);
        return;
    }

    const QFileInfo analyzerFile(m_qualityScriptPath);
    if (!analyzerFile.isFile()) {
        setQualityMessage(tr("음질 분석 도구를 찾을 수 없습니다."), true);
        return;
    }

    const QVector<MultiAudioInput> inputs = m_model->inputs();
    const int referenceRow = m_model->referenceRow();
    // The previous plan's manual selections are sent along, so the engine
    // reports the final channel plan the user is about to approve.
    const MultiAudioManifestRequest request = buildMultiAudioManifest(
        inputs, referenceRow, m_selectionModel->plan());
    if (request.rows.size() < 2 || request.referenceIndex < 0) {
        setQualityMessage(tr("사용할 파일과 기준 파일을 다시 확인하세요."), true);
        return;
    }

    auto *manifestFile = new QTemporaryFile(
        QDir(QDir::tempPath()).filePath(
            QStringLiteral("korean_transcriber_quality_XXXXXX.json")),
        this);
    manifestFile->setAutoRemove(true);
    const QByteArray manifestData = QJsonDocument(request.manifest).toJson(
        QJsonDocument::Compact);
    if (!manifestFile->open()
        || manifestFile->write(manifestData) != manifestData.size()
        || !manifestFile->flush()) {
        setQualityMessage(tr("음질 분석 요청 파일을 만들 수 없습니다."), true);
        delete manifestFile;
        return;
    }
    manifestFile->close();

    const quint64 generation = ++m_qualityGeneration;
    auto *process = new QProcess(this);
    process->setProcessChannelMode(QProcess::SeparateChannels);
    process->setProgram(m_qualityPythonProgram);
    process->setArguments({analyzerFile.absoluteFilePath(),
                           QStringLiteral("--multi-input-manifest"),
                           manifestFile->fileName()});
    process->setWorkingDirectory(analyzerFile.absolutePath());

    m_qualityProcess = process;
    m_qualityManifestFile = manifestFile;
    m_qualityManifestInputs.clear();
    for (int row : request.rows) {
        m_qualityManifestInputs.append(inputs.at(row));
    }
    m_qualityReferenceInputIndex = request.referenceIndex;
    m_qualityAnalysisKey =
        MultiAudioInputModel::selectionAnalysisKey(inputs, referenceRow);
    m_qualityTimelineKey =
        MultiAudioInputModel::selectionTimelineKey(inputs, referenceRow);
    m_qualityDroppedOverrideCount = request.droppedOverrideCount;
    m_qualityStandardOutput.clear();
    m_qualityStandardError.clear();
    m_qualityCancellationPending = false;

    connect(process, &QProcess::readyReadStandardOutput, this,
            [this, process, generation]() {
        if (process == m_qualityProcess && generation == m_qualityGeneration) {
            m_qualityStandardOutput.append(process->readAllStandardOutput());
        }
    });
    connect(process, &QProcess::readyReadStandardError, this,
            [this, process, generation]() {
        if (process == m_qualityProcess && generation == m_qualityGeneration) {
            m_qualityStandardError.append(process->readAllStandardError());
        }
    });
    connect(process, &QProcess::errorOccurred, this,
            [this, process, generation](QProcess::ProcessError error) {
        if (error == QProcess::FailedToStart) {
            qualityProcessFailedToStart(process, generation);
        }
    });
    connect(process,
            qOverload<int, QProcess::ExitStatus>(&QProcess::finished),
            this,
            [this, process, generation](int exitCode,
                                        QProcess::ExitStatus exitStatus) {
        finishQualityAnalysis(process, generation, exitCode,
                              static_cast<int>(exitStatus));
    });

    setQualityMessage(
        request.overrideCount > 0
            ? tr("수동 선택 %1개를 포함해 구간별 음질을 분석하는 중입니다. 필요하면 ‘분석 취소’를 누르세요.")
                  .arg(request.overrideCount)
            : tr("구간별 음질을 분석하는 중입니다. 필요하면 ‘분석 취소’를 누르세요."));
    updateActions();
    process->start();
}

void MultiAudioInputDialog::cancelQualityAnalysis()
{
    if (!isQualityAnalysisRunning() || m_qualityCancellationPending) {
        return;
    }

    m_qualityCancellationPending = true;
    const quint64 generation = m_qualityGeneration;
    QProcess *process = m_qualityProcess;
    setQualityMessage(tr("음질 분석을 취소하는 중입니다..."));
    updateActions();
    process->terminate();
    QTimer::singleShot(3000, process, [this, process, generation]() {
        if (process == m_qualityProcess
            && generation == m_qualityGeneration
            && process->state() != QProcess::NotRunning) {
            process->kill();
        }
    });
}

void MultiAudioInputDialog::finishQualityAnalysis(QProcess *process,
                                                  quint64 generation,
                                                  int exitCode,
                                                  int exitStatus)
{
    if (process != m_qualityProcess || generation != m_qualityGeneration) {
        process->deleteLater();
        return;
    }

    m_qualityStandardOutput.append(process->readAllStandardOutput());
    m_qualityStandardError.append(process->readAllStandardError());

    if (m_qualityCancellationPending) {
        cleanupQualityRun(process);
        setQualityMessage(tr("음질 분석을 취소했습니다. 이전 분석 결과와 수동 선택을 유지합니다."));
        updateActions();
        return;
    }

    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(
        m_qualityStandardOutput.trimmed(), &parseError);
    const bool normalExit =
        exitStatus == static_cast<int>(QProcess::NormalExit);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        cleanupQualityRun(process);
        setQualityMessage(
            normalExit && exitCode == 0
                ? tr("음질 분석 응답을 해석할 수 없습니다.")
                : tr("음질 분석 프로세스가 비정상 종료되었습니다."),
            true);
        updateActions();
        return;
    }

    const QJsonObject payload = document.object();
    const QJsonValue okValue = payload.value(QStringLiteral("ok"));
    if (!okValue.isBool() || !okValue.toBool() || !normalExit || exitCode != 0) {
        const QString message = qualityFailureMessage(payload);
        cleanupQualityRun(process);
        setQualityMessage(message, true);
        updateActions();
        return;
    }

    // The inputs are locked while the analysis runs; still, a result is never
    // applied to a configuration it was not computed for.
    if (MultiAudioInputModel::selectionAnalysisKey(m_model->inputs(),
                                                   m_model->referenceRow())
        != m_qualityAnalysisKey) {
        cleanupQualityRun(process);
        setQualityMessage(tr("분석 중 입력 구성이 바뀌어 음질 분석 결과를 적용하지 않았습니다."),
                          true);
        updateActions();
        return;
    }

    MultiAudioSelectionPlan plan;
    QString error;
    if (!parseMultiAudioQualityResponse(payload, m_qualityManifestInputs,
                                        m_qualityReferenceInputIndex,
                                        &plan, &error)) {
        cleanupQualityRun(process);
        setQualityMessage(tr("음질 분석 결과를 적용할 수 없습니다: %1").arg(error),
                          true);
        updateActions();
        return;
    }
    plan.analysisKey = m_qualityAnalysisKey;
    plan.timelineKey = m_qualityTimelineKey;
    const int droppedOverrideCount = m_qualityDroppedOverrideCount;
    cleanupQualityRun(process);

    m_selectionModel->setPlan(plan);
    refreshSelectionContext();
    QString message = tr("음질 분석 완료: 구간 %1개 · 수동 선택 %2개 · 경고 %3건")
                          .arg(plan.segments.size())
                          .arg(plan.manualSegmentCount())
                          .arg(plan.warnings.size());
    if (droppedOverrideCount > 0) {
        message += tr(" · 입력 또는 기준 파일이 바뀌어 이전 수동 선택 %1개를 해제했습니다.")
                       .arg(droppedOverrideCount);
    }
    setQualityMessage(message, droppedOverrideCount > 0);
    updateActions();
}

QString MultiAudioInputDialog::qualityFailureMessage(
    const QJsonObject &payload) const
{
    const QString kind = payload.value(QStringLiteral("kind")).toString();
    QStringList details;
    for (const QJsonValue &value : payload.value(QStringLiteral("errors")).toArray()) {
        const QJsonObject error = value.toObject();
        QString detail = error.value(QStringLiteral("message")).toString().trimmed();
        const QString code = error.value(QStringLiteral("code")).toString().trimmed();
        if (detail.isEmpty()) {
            detail = code;
        } else if (!code.isEmpty()) {
            detail = QStringLiteral("[%1] %2").arg(code, detail);
        }
        int inputIndex = -1;
        if (jsonInteger(error.value(QStringLiteral("input_index")), 0,
                        static_cast<int>(m_qualityManifestInputs.size()) - 1,
                        &inputIndex)) {
            detail = tr("%1: %2").arg(
                m_qualityManifestInputs.at(inputIndex).alias, detail);
        }
        if (!detail.isEmpty()) {
            details.append(detail);
        }
    }

    if (kind == QStringLiteral("preparation") && !details.isEmpty()) {
        return tr("음질 분석 준비에 실패한 파일: %1")
            .arg(details.join(QStringLiteral(" · ")));
    }
    if (kind == QStringLiteral("alignment") && !details.isEmpty()) {
        return tr("시간 정렬이 해결되지 않은 파일이 있습니다: %1. 시간 정렬 분석을 다시 실행하거나 수동 오프셋을 지정하세요.")
            .arg(details.join(QStringLiteral(" · ")));
    }
    if (kind == QStringLiteral("contract")
        && payload.value(QStringLiteral("code")).toString()
            == QStringLiteral("invalid_override")) {
        return tr("수동 채널 선택이 기준 녹음 길이를 벗어났습니다. ‘수동 선택 모두 해제’ 후 다시 분석하세요.");
    }
    QString message = payload.value(QStringLiteral("message")).toString().trimmed();
    if (message.isEmpty()) {
        message = tr("음질 분석을 완료할 수 없습니다. 입력 파일과 Python 환경을 확인하세요.");
    }
    return message;
}

void MultiAudioInputDialog::qualityProcessFailedToStart(QProcess *process,
                                                         quint64 generation)
{
    if (process != m_qualityProcess || generation != m_qualityGeneration) {
        return;
    }
    cleanupQualityRun(process);
    setQualityMessage(tr("Python 음질 분석 프로세스를 시작할 수 없습니다."), true);
    updateActions();
}

void MultiAudioInputDialog::cleanupQualityRun(QProcess *process)
{
    if (process == m_qualityProcess) {
        m_qualityProcess = nullptr;
    }
    if (process) {
        process->deleteLater();
    }
    if (m_qualityManifestFile) {
        m_qualityManifestFile->deleteLater();
        m_qualityManifestFile = nullptr;
    }
    m_qualityStandardOutput.clear();
    m_qualityStandardError.clear();
    m_qualityManifestInputs.clear();
    m_qualityReferenceInputIndex = -1;
    m_qualityAnalysisKey.clear();
    m_qualityTimelineKey.clear();
    m_qualityDroppedOverrideCount = 0;
    m_qualityCancellationPending = false;
}

void MultiAudioInputDialog::stopQualityProcess()
{
    ++m_qualityGeneration;
    if (!m_qualityProcess) {
        return;
    }
    disconnect(m_qualityProcess, nullptr, this, nullptr);
    if (m_qualityProcess->state() != QProcess::NotRunning) {
        m_qualityProcess->kill();
        m_qualityProcess->waitForFinished(100);
    }
    cleanupQualityRun(m_qualityProcess);
}

void MultiAudioInputDialog::setQualityMessage(const QString &message, bool error)
{
    m_qualityMessageLabel->setText(message);
    m_qualityMessageLabel->setStyleSheet(
        error ? QStringLiteral("color: #b00020;") : QString());
}

void MultiAudioInputDialog::applySelectedOverride()
{
    if (isAlignmentAnalysisRunning() || isQualityAnalysisRunning()) {
        return;
    }
    const QVector<int> rows = selectedSegmentRows();
    const QString path = m_overrideComboBox->currentData().toString();
    if (rows.isEmpty() || path.isEmpty()) {
        return;
    }
    const int changed = m_selectionModel->setOverride(rows, path);
    setQualityMessage(
        changed > 0
            ? tr("구간 %1개의 최종 선택을 ‘%2’ 마이크로 지정했습니다. 자동 선택과 같은 구간은 자동으로 유지되며, 통합 음성 합성과 전사에 최종 선택이 적용됩니다.")
                  .arg(changed)
                  .arg(m_selectionModel->aliasForPath(path))
            : tr("바뀐 구간이 없습니다."));
}

void MultiAudioInputDialog::clearSelectedOverrides()
{
    if (isAlignmentAnalysisRunning() || isQualityAnalysisRunning()) {
        return;
    }
    const int changed = m_selectionModel->clearOverrides(selectedSegmentRows());
    if (changed > 0) {
        setQualityMessage(tr("구간 %1개를 자동 선택으로 되돌렸습니다.").arg(changed));
    }
}

void MultiAudioInputDialog::clearAllOverrides()
{
    if (isAlignmentAnalysisRunning() || isQualityAnalysisRunning()) {
        return;
    }
    const int changed = m_selectionModel->clearAllOverrides();
    if (changed > 0) {
        setQualityMessage(tr("수동 선택 %1개를 모두 해제했습니다.").arg(changed));
    }
}

void MultiAudioInputDialog::refreshSelectionContext()
{
    m_selectionModel->setInputContext(m_model->inputs(), m_model->referenceRow());
    rebuildOverrideCombo();
    updateActions();
}

void MultiAudioInputDialog::rebuildOverrideCombo()
{
    const QString previousPath = m_overrideComboBox->currentData().toString();
    const QSignalBlocker blocker(m_overrideComboBox);
    m_overrideComboBox->clear();
    for (const QString &path : m_selectionModel->plan().inputPaths) {
        m_overrideComboBox->addItem(m_selectionModel->aliasForPath(path), path);
    }
    int index = previousPath.isEmpty() ? -1
                                       : m_overrideComboBox->findData(previousPath);
    if (index < 0 && m_overrideComboBox->count() > 0) {
        index = 0;
    }
    m_overrideComboBox->setCurrentIndex(index);
}

void MultiAudioInputDialog::updateSelectionSummary()
{
    const MultiAudioSelectionPlan plan = m_selectionModel->plan();
    if (plan.isEmpty()) {
        m_selectionSummaryLabel->setText(
            isQualityAnalysisRunning()
                ? tr("음질 분석 중")
                : tr("음질 분석 결과가 없습니다. 시간 정렬을 마친 뒤 ‘음질 분석’을 실행하세요."));
        m_selectionSummaryLabel->setToolTip(QString());
        m_selectionSummaryLabel->setStyleSheet(QString());
        return;
    }

    const int segmentCount = plan.segments.size();
    const int manualCount = plan.manualSegmentCount();
    QStringList messages;
    messages.append(tr("구간 %1개 · 자동 %2 · 수동 %3 · 최종 전환 %4회")
                        .arg(segmentCount)
                        .arg(segmentCount - manualCount)
                        .arg(manualCount)
                        .arg(plan.switchCount()));
    const int incompleteCount = plan.incompleteFinalSegmentCount();
    if (incompleteCount > 0) {
        messages.append(tr("최종 선택 마이크의 녹음이 비는 구간 %1개(해당 부분은 무음으로 합성)")
                            .arg(incompleteCount));
    }
    if (!plan.warnings.isEmpty()) {
        messages.append(tr("분석 경고 %1건").arg(plan.warnings.size()));
    }
    const bool current =
        plan.isCurrentFor(m_model->inputs(), m_model->referenceRow());
    if (!current) {
        messages.append(tr("입력·기준 파일·시간 정렬이 바뀌어 결과가 최신이 아닙니다. 음질 분석을 다시 실행하세요."));
    }
    m_selectionSummaryLabel->setText(messages.join(QStringLiteral(" · ")));
    m_selectionSummaryLabel->setToolTip(plan.warnings.join(QLatin1Char('\n')));
    m_selectionSummaryLabel->setStyleSheet(
        current ? QString() : QStringLiteral("color: #b00020;"));
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
    const bool qualityRunning = isQualityAnalysisRunning();
    const bool busy = analysisRunning || qualityRunning;
    const bool inspectionRunning = !m_currentInspection.isEmpty()
        || m_audioFileInfo->isInspecting() || !m_inspectionQueue.isEmpty();
    const bool hasSelection = m_tableView->selectionModel()
        && m_tableView->selectionModel()->hasSelection();
    m_tableView->setEnabled(!busy);
    m_addButton->setEnabled(!busy);
    m_removeButton->setEnabled(hasSelection && !busy);
    m_referenceComboBox->setEnabled(
        m_referenceComboBox->count() > 0 && !busy);

    QString reason;
    const bool valid = m_model->isConfigurationValid(&reason);
    m_analyzeAlignmentButton->setEnabled(
        valid && !inspectionRunning && !busy);
    m_cancelAlignmentButton->setEnabled(
        analysisRunning && !m_alignmentCancellationPending);
    m_alignmentProgressBar->setVisible(analysisRunning);

    QString alignmentReason;
    const bool alignmentResolved = m_model->isAlignmentResolved(&alignmentReason);
    m_analyzeQualityButton->setEnabled(
        alignmentResolved && !inspectionRunning && !busy);
    m_analyzeQualityButton->setToolTip(
        alignmentResolved
            ? tr("사용할 파일을 구간별로 비교해 전사에 쓸 마이크를 선택합니다.")
            : alignmentReason);
    m_cancelQualityButton->setEnabled(
        qualityRunning && !m_qualityCancellationPending);
    m_qualityProgressBar->setVisible(qualityRunning);

    const MultiAudioSelectionPlan plan = m_selectionModel->plan();
    const bool planCurrent =
        plan.isCurrentFor(m_model->inputs(), m_model->referenceRow());
    const QVector<int> segmentRows = selectedSegmentRows();
    const bool manualSegmentSelected = std::any_of(
        segmentRows.cbegin(), segmentRows.cend(), [this](int row) {
            return m_selectionModel->segmentAt(row).isManual();
        });
    m_selectionTableView->setEnabled(!busy);
    m_overrideComboBox->setEnabled(
        planCurrent && !busy && m_overrideComboBox->count() > 0);
    m_applyOverrideButton->setEnabled(
        planCurrent && !busy && !segmentRows.isEmpty()
        && m_overrideComboBox->currentIndex() >= 0);
    m_clearOverrideButton->setEnabled(!busy && manualSegmentSelected);
    m_clearAllOverridesButton->setEnabled(
        !busy && plan.manualSegmentCount() > 0);

    bool canEditManualOffset = false;
    const int selectedRow = selectedInputRow();
    if (!busy && selectedRow >= 0
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
    m_buttonBox->button(QDialogButtonBox::Ok)->setEnabled(valid && !busy);
    updateSummary();
    updateSelectionSummary();
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
