#include "MultiAudioInputDialog.h"

#include "AudioFileInfo.h"

#include <QAbstractItemView>
#include <QComboBox>
#include <QDialogButtonBox>
#include <QFileDialog>
#include <QFileInfo>
#include <QHeaderView>
#include <QHBoxLayout>
#include <QItemSelectionModel>
#include <QLabel>
#include <QMessageBox>
#include <QPushButton>
#include <QSet>
#include <QStandardPaths>
#include <QTableView>
#include <QVBoxLayout>

#include <algorithm>
#include <functional>

MultiAudioInputDialog::MultiAudioInputDialog(QWidget *parent)
    : QDialog(parent)
    , m_model(new MultiAudioInputModel(this))
    , m_audioFileInfo(new AudioFileInfo(this))
{
    setObjectName(QStringLiteral("multiAudioInputDialog"));
    setWindowTitle(tr("다중 마이크 파일"));
    setModal(true);
    resize(1040, 520);
    setMinimumSize(820, 420);

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

MultiAudioInputModel *MultiAudioInputDialog::model() const noexcept
{
    return m_model;
}

QVector<MultiAudioInput> MultiAudioInputDialog::inputs() const
{
    QVector<MultiAudioInput> result = m_model->inputs();
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
    m_model->setReferenceRow(row);
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
}

int MultiAudioInputDialog::addFiles(const QStringList &filePaths)
{
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
    QString reason;
    if (!m_model->isConfigurationValid(&reason)) {
        QMessageBox::warning(this, tr("다중 입력 확인"), reason);
        return;
    }
    QDialog::accept();
}

void MultiAudioInputDialog::chooseFiles()
{
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
    const bool hasSelection = m_tableView->selectionModel()
        && m_tableView->selectionModel()->hasSelection();
    m_removeButton->setEnabled(hasSelection);
    m_referenceComboBox->setEnabled(m_referenceComboBox->count() > 0);

    QString reason;
    const bool valid = m_model->isConfigurationValid(&reason);
    m_buttonBox->button(QDialogButtonBox::Ok)->setEnabled(valid);
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
    for (const MultiAudioInput &input : m_model->inputs()) {
        if (input.enabled) {
            ++enabledCount;
        }
        if (input.status == MultiAudioInput::Status::Ready) {
            ++readyCount;
        } else if (input.status == MultiAudioInput::Status::Pending
                   || input.status == MultiAudioInput::Status::Inspecting) {
            ++inspectingCount;
        } else if (input.status == MultiAudioInput::Status::Error) {
            ++errorCount;
        }
    }

    QString reason;
    const bool valid = m_model->isConfigurationValid(&reason);
    QStringList messages;
    messages.append(tr("전체 %1개 · 사용 %2개 · 준비 %3개")
                        .arg(m_model->rowCount())
                        .arg(enabledCount)
                        .arg(readyCount));
    if (inspectingCount > 0) {
        messages.append(tr("%1개 확인 중").arg(inspectingCount));
    }
    if (errorCount > 0) {
        messages.append(tr("%1개 오류").arg(errorCount));
    }
    if (m_model->rowCount() > 8) {
        messages.append(tr("권장 파일 수(8개)를 초과했습니다"));
    }
    messages.append(valid ? tr("적용할 수 있습니다") : reason);
    m_summaryLabel->setText(messages.join(QStringLiteral(" · ")));
}
