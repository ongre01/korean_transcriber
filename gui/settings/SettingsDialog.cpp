#include "SettingsDialog.h"

#include <QCheckBox>
#include <QComboBox>
#include <QDialogButtonBox>
#include <QDoubleSpinBox>
#include <QFileDialog>
#include <QFormLayout>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QPushButton>
#include <QVBoxLayout>

namespace {
QWidget *pathEditor(QLineEdit *&lineEdit, QPushButton *&browseButton,
                    QWidget *parent, const QString &text)
{
    auto *container = new QWidget(parent);
    auto *layout = new QHBoxLayout(container);
    layout->setContentsMargins(0, 0, 0, 0);
    lineEdit = new QLineEdit(container);
    browseButton = new QPushButton(text, container);
    layout->addWidget(lineEdit, 1);
    layout->addWidget(browseButton);
    return container;
}

QDoubleSpinBox *secondsSpinBox(QWidget *parent, double minimum, double maximum)
{
    auto *spinBox = new QDoubleSpinBox(parent);
    spinBox->setDecimals(2);
    spinBox->setRange(minimum, maximum);
    spinBox->setSingleStep(0.1);
    spinBox->setSuffix(QObject::tr(" 초"));
    return spinBox;
}
} // namespace

SettingsDialog::SettingsDialog(const Settings &currentSettings, QWidget *parent)
    : QDialog(parent)
    , m_currentSettings(currentSettings)
{
    setWindowTitle(tr("설정"));
    setModal(true);
    resize(720, 420);

    auto *mainLayout = new QVBoxLayout(this);
    auto *basicForm = new QFormLayout;

    QPushButton *pythonBrowseButton = nullptr;
    basicForm->addRow(tr("Python 실행 파일"),
                      pathEditor(m_pythonPathEdit, pythonBrowseButton, this, tr("찾아보기...")));
    connect(pythonBrowseButton, &QPushButton::clicked, this, [this]() {
        chooseFile(m_pythonPathEdit, tr("Python 실행 파일 선택"),
                   tr("실행 파일 (*.exe);;모든 파일 (*.*)"));
    });

    QPushButton *outputBrowseButton = nullptr;
    basicForm->addRow(tr("결과 출력 폴더"),
                      pathEditor(m_outputDirectoryEdit, outputBrowseButton, this, tr("선택...")));
    connect(outputBrowseButton, &QPushButton::clicked, this, [this]() {
        chooseDirectory(m_outputDirectoryEdit, tr("결과 출력 폴더 선택"));
    });
    mainLayout->addLayout(basicForm);

    m_advancedToggleButton = new QPushButton(tr("고급 설정 표시"), this);
    m_advancedToggleButton->setCheckable(true);
    mainLayout->addWidget(m_advancedToggleButton, 0, Qt::AlignLeft);

    m_advancedGroupBox = new QGroupBox(tr("고급 설정"), this);
    auto *advancedForm = new QFormLayout(m_advancedGroupBox);

    QPushButton *modelBrowseButton = nullptr;
    advancedForm->addRow(tr("Whisper 모델 폴더"),
                         pathEditor(m_whisperModelDirectoryEdit, modelBrowseButton,
                                    m_advancedGroupBox, tr("선택...")));
    connect(modelBrowseButton, &QPushButton::clicked, this, [this]() {
        chooseDirectory(m_whisperModelDirectoryEdit, tr("Whisper 모델 폴더 선택"));
    });

    m_windowSecondsSpinBox = secondsSpinBox(m_advancedGroupBox, 30.0, 7200.0);
    advancedForm->addRow(tr("Window Seconds"), m_windowSecondsSpinBox);
    m_overlapSecondsSpinBox = secondsSpinBox(m_advancedGroupBox, 0.0, 3600.0);
    advancedForm->addRow(tr("Overlap Seconds"), m_overlapSecondsSpinBox);

    QPushButton *hotwordsBrowseButton = nullptr;
    advancedForm->addRow(tr("Hotwords 파일"),
                         pathEditor(m_hotwordsFileEdit, hotwordsBrowseButton,
                                    m_advancedGroupBox, tr("선택...")));
    connect(hotwordsBrowseButton, &QPushButton::clicked, this, [this]() {
        chooseFile(m_hotwordsFileEdit, tr("Hotwords 파일 선택"), tr("텍스트 파일 (*.txt);;모든 파일 (*.*)"));
    });

    QPushButton *promptBrowseButton = nullptr;
    advancedForm->addRow(tr("Initial Prompt 파일"),
                         pathEditor(m_initialPromptFileEdit, promptBrowseButton,
                                    m_advancedGroupBox, tr("선택...")));
    connect(promptBrowseButton, &QPushButton::clicked, this, [this]() {
        chooseFile(m_initialPromptFileEdit, tr("Initial Prompt 파일 선택"),
                   tr("텍스트 파일 (*.txt);;모든 파일 (*.*)"));
    });

    QPushButton *segmentationBrowseButton = nullptr;
    advancedForm->addRow(tr("Segmentation 모델"),
                         pathEditor(m_segmentationModelEdit, segmentationBrowseButton,
                                    m_advancedGroupBox, tr("선택...")));
    connect(segmentationBrowseButton, &QPushButton::clicked, this, [this]() {
        chooseFile(m_segmentationModelEdit, tr("Segmentation 모델 선택"),
                   tr("ONNX 모델 (*.onnx);;모든 파일 (*.*)"));
    });

    QPushButton *embeddingBrowseButton = nullptr;
    advancedForm->addRow(tr("Embedding 모델"),
                         pathEditor(m_embeddingModelEdit, embeddingBrowseButton,
                                    m_advancedGroupBox, tr("선택...")));
    connect(embeddingBrowseButton, &QPushButton::clicked, this, [this]() {
        chooseFile(m_embeddingModelEdit, tr("Embedding 모델 선택"),
                   tr("ONNX 모델 (*.onnx);;모든 파일 (*.*)"));
    });

    m_speakerThresholdSpinBox = new QDoubleSpinBox(m_advancedGroupBox);
    m_speakerThresholdSpinBox->setDecimals(3);
    m_speakerThresholdSpinBox->setRange(0.001, 1.0);
    m_speakerThresholdSpinBox->setSingleStep(0.05);
    advancedForm->addRow(tr("Cluster Threshold"), m_speakerThresholdSpinBox);

    m_minimumSpeechDurationSpinBox = secondsSpinBox(m_advancedGroupBox, 0.0, 120.0);
    advancedForm->addRow(tr("최소 발화 지속 시간"), m_minimumSpeechDurationSpinBox);
    m_minimumSilenceDurationSpinBox = secondsSpinBox(m_advancedGroupBox, 0.0, 120.0);
    advancedForm->addRow(tr("최소 무음 간격"), m_minimumSilenceDurationSpinBox);

    m_diarizationDeviceComboBox = new QComboBox(m_advancedGroupBox);
    m_diarizationDeviceComboBox->addItems({QStringLiteral("NPU"), QStringLiteral("CPU"),
                                           QStringLiteral("GPU")});
    advancedForm->addRow(tr("화자 분리 Device"), m_diarizationDeviceComboBox);
    m_diarizationFallbackCheckBox = new QCheckBox(tr("NPU 사용 불가 시 CPU로 대체"),
                                                   m_advancedGroupBox);
    advancedForm->addRow(QString(), m_diarizationFallbackCheckBox);

    m_advancedGroupBox->setVisible(false);
    connect(m_advancedToggleButton, &QPushButton::toggled, this, [this](bool visible) {
        m_advancedGroupBox->setVisible(visible);
        m_advancedToggleButton->setText(visible ? tr("고급 설정 숨기기") : tr("고급 설정 표시"));
        adjustSize();
    });
    mainLayout->addWidget(m_advancedGroupBox);

    auto *buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, this);
    connect(buttons, &QDialogButtonBox::accepted, this, &SettingsDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, this, &SettingsDialog::reject);
    mainLayout->addWidget(buttons);

    m_pythonPathEdit->setText(currentSettings.pythonPath);
    m_outputDirectoryEdit->setText(currentSettings.outputDirectory);
    m_whisperModelDirectoryEdit->setText(currentSettings.whisperModelDirectory);
    m_windowSecondsSpinBox->setValue(currentSettings.windowSeconds);
    m_overlapSecondsSpinBox->setValue(currentSettings.overlapSeconds);
    m_hotwordsFileEdit->setText(currentSettings.hotwordsFile);
    m_initialPromptFileEdit->setText(currentSettings.initialPromptFile);
    m_segmentationModelEdit->setText(currentSettings.diarizationSegmentationModel);
    m_embeddingModelEdit->setText(currentSettings.diarizationEmbeddingModel);
    m_speakerThresholdSpinBox->setValue(currentSettings.speakerThreshold);
    m_minimumSpeechDurationSpinBox->setValue(currentSettings.minimumSpeechDuration);
    m_minimumSilenceDurationSpinBox->setValue(currentSettings.minimumSilenceDuration);
    const int deviceIndex = m_diarizationDeviceComboBox->findText(
        currentSettings.diarizationDevice, Qt::MatchFixedString);
    m_diarizationDeviceComboBox->setCurrentIndex(deviceIndex >= 0 ? deviceIndex : 0);
    m_diarizationFallbackCheckBox->setChecked(currentSettings.diarizationFallbackToCpu);
}

Settings SettingsDialog::settings() const
{
    Settings result = m_currentSettings;
    result.pythonPath = m_pythonPathEdit->text().trimmed();
    result.outputDirectory = m_outputDirectoryEdit->text().trimmed();
    result.whisperModelDirectory = m_whisperModelDirectoryEdit->text().trimmed();
    result.windowSeconds = m_windowSecondsSpinBox->value();
    result.overlapSeconds = m_overlapSecondsSpinBox->value();
    result.hotwordsFile = m_hotwordsFileEdit->text().trimmed();
    result.initialPromptFile = m_initialPromptFileEdit->text().trimmed();
    result.diarizationSegmentationModel = m_segmentationModelEdit->text().trimmed();
    result.diarizationEmbeddingModel = m_embeddingModelEdit->text().trimmed();
    result.speakerThreshold = m_speakerThresholdSpinBox->value();
    result.minimumSpeechDuration = m_minimumSpeechDurationSpinBox->value();
    result.minimumSilenceDuration = m_minimumSilenceDurationSpinBox->value();
    result.diarizationDevice = m_diarizationDeviceComboBox->currentText();
    result.diarizationFallbackToCpu = m_diarizationFallbackCheckBox->isChecked();
    return result;
}

void SettingsDialog::accept()
{
    QString errorMessage;
    if (!hasValidValues(&errorMessage)) {
        QMessageBox::warning(this, tr("설정 확인"), errorMessage);
        return;
    }
    QDialog::accept();
}

void SettingsDialog::chooseFile(QLineEdit *target, const QString &caption, const QString &filter)
{
    const QString selected = QFileDialog::getOpenFileName(this, caption, target->text(), filter);
    if (!selected.isEmpty()) {
        target->setText(selected);
    }
}

void SettingsDialog::chooseDirectory(QLineEdit *target, const QString &caption)
{
    const QString selected = QFileDialog::getExistingDirectory(this, caption, target->text());
    if (!selected.isEmpty()) {
        target->setText(selected);
    }
}

bool SettingsDialog::hasValidValues(QString *errorMessage) const
{
    const auto fail = [errorMessage](const QString &message) {
        if (errorMessage) {
            *errorMessage = message;
        }
        return false;
    };

    if (m_pythonPathEdit->text().trimmed().isEmpty()) {
        return fail(tr("Python 실행 파일을 입력하세요."));
    }
    if (m_outputDirectoryEdit->text().trimmed().isEmpty()) {
        return fail(tr("결과 출력 폴더를 입력하세요."));
    }
    if (m_windowSecondsSpinBox->value() < 30.0) {
        return fail(tr("Window Seconds는 30 이상이어야 합니다."));
    }
    if (m_overlapSecondsSpinBox->value() >= m_windowSecondsSpinBox->value() / 2.0) {
        return fail(tr("Overlap Seconds는 Window Seconds의 절반보다 작아야 합니다."));
    }
    return true;
}
