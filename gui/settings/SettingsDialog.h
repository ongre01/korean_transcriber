#ifndef SETTINGSDIALOG_H
#define SETTINGSDIALOG_H

#include "Settings.h"

#include <QDialog>

class QCheckBox;
class QComboBox;
class QDoubleSpinBox;
class QGroupBox;
class QLineEdit;
class QPushButton;
class QSpinBox;

class SettingsDialog final : public QDialog
{
    Q_OBJECT

public:
    explicit SettingsDialog(const Settings &settings, QWidget *parent = nullptr);

    Settings settings() const;

protected:
    void accept() override;

private:
    void chooseFile(QLineEdit *target, const QString &caption, const QString &filter);
    void chooseDirectory(QLineEdit *target, const QString &caption);
    bool hasValidValues(QString *errorMessage) const;

    QLineEdit *m_pythonPathEdit = nullptr;
    QLineEdit *m_outputDirectoryEdit = nullptr;
    QLineEdit *m_whisperModelDirectoryEdit = nullptr;
    QSpinBox *m_beamsSpinBox = nullptr;
    QDoubleSpinBox *m_windowSecondsSpinBox = nullptr;
    QDoubleSpinBox *m_overlapSecondsSpinBox = nullptr;
    QLineEdit *m_hotwordsFileEdit = nullptr;
    QLineEdit *m_initialPromptFileEdit = nullptr;
    QCheckBox *m_skipSilenceCheckBox = nullptr;
    QDoubleSpinBox *m_silenceThresholdDbSpinBox = nullptr;
    QDoubleSpinBox *m_silenceMinimumSpeechDurationSpinBox = nullptr;
    QDoubleSpinBox *m_silenceMinimumDurationSpinBox = nullptr;
    QDoubleSpinBox *m_silencePaddingDurationSpinBox = nullptr;
    QLineEdit *m_segmentationModelEdit = nullptr;
    QLineEdit *m_embeddingModelEdit = nullptr;
    QDoubleSpinBox *m_speakerThresholdSpinBox = nullptr;
    QDoubleSpinBox *m_minimumSpeechDurationSpinBox = nullptr;
    QDoubleSpinBox *m_minimumSilenceDurationSpinBox = nullptr;
    QComboBox *m_diarizationDeviceComboBox = nullptr;
    QCheckBox *m_diarizationFallbackCheckBox = nullptr;
    QGroupBox *m_advancedGroupBox = nullptr;
    QPushButton *m_advancedToggleButton = nullptr;
    Settings m_currentSettings;
};

#endif // SETTINGSDIALOG_H
