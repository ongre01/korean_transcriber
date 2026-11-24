#include "../mainwindow.h"
#include "../settings/SettingsDialog.h"

#include <QCheckBox>
#include <QComboBox>
#include <QFile>
#include <QLineEdit>
#include <QSettings>
#include <QStandardPaths>
#include <QTemporaryDir>
#include <QtTest>

namespace {
class ScopedEnvironment
{
public:
    ScopedEnvironment(const char *name, const QByteArray &value)
        : m_name(name), m_previous(qgetenv(name)), m_wasSet(qEnvironmentVariableIsSet(name))
    {
        qputenv(m_name.constData(), value);
    }

    ~ScopedEnvironment()
    {
        if (m_wasSet) {
            qputenv(m_name.constData(), m_previous);
        } else {
            qunsetenv(m_name.constData());
        }
    }

private:
    QByteArray m_name;
    QByteArray m_previous;
    bool m_wasSet;
};
} // namespace

class EngineSelectionTests : public QObject
{
    Q_OBJECT

private slots:
    void initTestCase();
    void init();
    void settingsRoundTripPreservesBothEngines();
    void nemotronValidatesOwnResources();
    void installedNemotronResolvesWithoutUpdatedPath();
    void argumentsUseOnlySelectedEngine();
    void dialogPreservesWhisperOptions();
    void mainWindowSwitchesEngineAndLocksDuringProcessing();

private:
    QTemporaryDir m_temporary;
};

void EngineSelectionTests::initTestCase()
{
    QVERIFY(m_temporary.isValid());
    QStandardPaths::setTestModeEnabled(true);
    QSettings::setDefaultFormat(QSettings::IniFormat);
    QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, m_temporary.path());
    QCoreApplication::setOrganizationName(QStringLiteral("EngineSelectionTests"));
    QCoreApplication::setApplicationName(QStringLiteral("IsolatedSettings"));
}

void EngineSelectionTests::init()
{
    QSettings().clear();
}

void EngineSelectionTests::settingsRoundTripPreservesBothEngines()
{
    QCOMPARE(Settings::load().transcriptionEngine, QStringLiteral("whisper"));
    Settings settings;
    settings.transcriptionEngine = QStringLiteral("nemotron");
    settings.nemotronExecutable = QStringLiteral("C:/엔진 폴더/nemo-speech.exe");
    settings.nemotronModelFile = QStringLiteral("C:/모델 폴더/decoder.gguf");
    settings.nemotronConfigFile = QStringLiteral("C:/모델 폴더/8gb.yaml");
    settings.whisperModelDirectory = QStringLiteral("C:/Whisper 모델");
    settings.selectedDevice = QStringLiteral("NPU");
    settings.speakerCount = QStringLiteral("3");
    settings.beams = 4;
    settings.save();
    const Settings loaded = Settings::load();
    QCOMPARE(loaded.transcriptionEngine, settings.transcriptionEngine);
    QCOMPARE(loaded.nemotronExecutable, settings.nemotronExecutable);
    QCOMPARE(loaded.nemotronModelFile, settings.nemotronModelFile);
    QCOMPARE(loaded.nemotronConfigFile, settings.nemotronConfigFile);
    QCOMPARE(loaded.whisperModelDirectory, settings.whisperModelDirectory);
    QCOMPARE(loaded.selectedDevice, settings.selectedDevice);
    QCOMPARE(loaded.speakerCount, settings.speakerCount);
    QCOMPARE(loaded.beams, settings.beams);
}

void EngineSelectionTests::nemotronValidatesOwnResources()
{
    const ScopedEnvironment localAppData("LOCALAPPDATA", m_temporary.path().toLocal8Bit());
    Settings settings;
    settings.transcriptionEngine = QStringLiteral("nemotron");
    settings.pythonPath = QCoreApplication::applicationFilePath();
    settings.nemotronExecutable = QCoreApplication::applicationFilePath();
    settings.outputDirectory = m_temporary.path();
    settings.nemotronModelFile = m_temporary.filePath(QStringLiteral("decoder.gguf"));
    QFile model(settings.nemotronModelFile);
    QVERIFY(model.open(QIODevice::WriteOnly));
    model.close();
    // Unavailable Whisper and sherpa resources cannot block a Nemotron run.
    settings.whisperModelDirectory.clear();
    settings.diarizationSegmentationModel = QStringLiteral("missing.onnx");
    QString error;
    QVERIFY2(settings.validateForRun(true, &error), qPrintable(error));
    settings.nemotronConfigFile = m_temporary.filePath(QStringLiteral("missing.yaml"));
    QVERIFY(!settings.validateForRun(true, &error));
    QVERIFY(error.contains(QStringLiteral("YAML")));
    settings.nemotronConfigFile.clear();
    settings.nemotronModelFile.clear();
    QVERIFY(!settings.validateForRun(false, &error));
    QVERIFY(error.contains(QStringLiteral("GGUF")));
    settings.transcriptionEngine = QStringLiteral("whisper");
    QVERIFY(!settings.validateForRun(false, &error));
    QVERIFY(error.contains(QStringLiteral("Whisper")));
}

void EngineSelectionTests::installedNemotronResolvesWithoutUpdatedPath()
{
#ifndef Q_OS_WIN
    QSKIP("The per-user NeMoSpeech installation location is Windows-specific.");
#else
    QTemporaryDir install;
    QVERIFY(install.isValid());
    const ScopedEnvironment emptyPath("PATH", QByteArray());
    const ScopedEnvironment localAppData("LOCALAPPDATA", install.path().toLocal8Bit());
    Settings settings;
    QCOMPARE(settings.resolvedNemotronExecutable(), QStringLiteral("nemo-speech"));

    const QString executable = install.filePath(QStringLiteral("Programs/NeMoSpeech/bin/nemo-speech.exe"));
    QVERIFY(QDir().mkpath(QFileInfo(executable).absolutePath()));
    QFile runtime(executable);
    QVERIFY(runtime.open(QIODevice::WriteOnly));
    runtime.close();
    // The Settings object predates the installation and has the old process PATH.
    QCOMPARE(settings.resolvedNemotronExecutable(), executable);
    settings.nemotronExecutable = QStringLiteral("nemo-speech.exe");
    QCOMPARE(settings.resolvedNemotronExecutable(), executable);
    settings.nemotronExecutable.clear();
    QCOMPARE(settings.resolvedNemotronExecutable(), executable);
    settings.transcriptionEngine = QStringLiteral("nemotron");
    settings.pythonPath = QCoreApplication::applicationFilePath();
    settings.nemotronModelFile = executable;
    settings.outputDirectory = install.path();
    QVERIFY(settings.validateForRun(false));
    settings.save();
    QCOMPARE(Settings::load().nemotronExecutable, executable);

    SettingsDialog dialog(settings);
    auto *editor = dialog.findChild<QLineEdit *>(QStringLiteral("nemotronExecutableEdit"));
    QVERIFY(editor);
    QCOMPARE(editor->text(), executable);

    settings.nemotronExecutable = install.filePath(QStringLiteral("explicit/missing.exe"));
    QCOMPARE(settings.resolvedNemotronExecutable(), settings.nemotronExecutable);
    QVERIFY(!settings.validateForRun(false));
    settings.nemotronExecutable = QStringLiteral("custom-nemo-command");
    QCOMPARE(settings.resolvedNemotronExecutable(), settings.nemotronExecutable);

    const QString pathDirectory = install.filePath(QStringLiteral("preferred"));
    QVERIFY(QDir().mkpath(pathDirectory));
    const QString pathExecutable = QDir(pathDirectory).filePath(QStringLiteral("nemo-speech.exe"));
    QVERIFY(QFile::copy(executable, pathExecutable));
    const ScopedEnvironment preferredPath("PATH", pathDirectory.toLocal8Bit());
    settings.nemotronExecutable = QStringLiteral("nemo-speech");
    QCOMPARE(settings.resolvedNemotronExecutable(), pathExecutable);
#endif
}

void EngineSelectionTests::argumentsUseOnlySelectedEngine()
{
    TranscribeOptions options;
    options.inputFile = QStringLiteral("C:/녹음 파일/회의.wav");
    options.transcriptionEngine = QStringLiteral("nemotron");
    options.nemotronExecutable = QStringLiteral("C:/엔진 파일/nemo-speech.exe");
    options.nemotronModelFile = QStringLiteral("C:/모델 파일/decoder.gguf");
    options.nemotronConfigFile = QStringLiteral("C:/설정 파일/custom.yaml");
    options.hotwordsFile = QStringLiteral("hotwords.txt");
    options.initialPromptFile = QStringLiteral("prompt.txt");
    options.diarizationEnabled = true;
    QString error;
    QVERIFY2(options.isValid(&error), qPrintable(error));
    const QStringList arguments = options.toBridgeArguments();
    QCOMPARE(arguments.value(arguments.indexOf(QStringLiteral("--engine")) + 1), QStringLiteral("nemotron"));
    QCOMPARE(arguments.value(arguments.indexOf(QStringLiteral("--nemotron-model")) + 1), options.nemotronModelFile);
    QCOMPARE(arguments.value(arguments.indexOf(QStringLiteral("--nemotron-executable")) + 1), options.nemotronExecutable);
    QVERIFY(arguments.contains(QStringLiteral("--nemotron-config")));
    QVERIFY(arguments.contains(QStringLiteral("--hotwords-file")));
    QVERIFY(arguments.contains(QStringLiteral("--initial-prompt-file")));
    QVERIFY(arguments.contains(QStringLiteral("--diarization")));
    for (const QString &flag : {QStringLiteral("--model-dir"), QStringLiteral("--device"),
                               QStringLiteral("--beams"), QStringLiteral("--window-seconds"),
                               QStringLiteral("--skip-silence"), QStringLiteral("--diarization-device")}) {
        QVERIFY2(!arguments.contains(flag), qPrintable(flag));
    }
    options.speakerCount = 2;
    QVERIFY(!options.isValid(&error));
    options.speakerCount.reset();
    options.inputFile.clear();
    options.multiInputManifestFile = QStringLiteral("multi input.json");
    options.nemotronConfigFile.clear();
    QVERIFY(options.isValid());
    QVERIFY(options.toBridgeArguments().contains(QStringLiteral("--multi-input-manifest")));
    QVERIFY(!options.toBridgeArguments().contains(QStringLiteral("--nemotron-config")));
    options.transcriptionEngine = QStringLiteral("whisper");
    QVERIFY(!options.isValid());
    options.modelDirectory = QStringLiteral("whisper model");
    QVERIFY(options.isValid());
    QVERIFY(options.toBridgeArguments().contains(QStringLiteral("--model-dir")));
    QVERIFY(!options.toBridgeArguments().contains(QStringLiteral("--nemotron-executable")));
}

void EngineSelectionTests::dialogPreservesWhisperOptions()
{
    Settings settings;
    settings.beams = 5;
    settings.windowSeconds = 60;
    settings.whisperModelDirectory = QStringLiteral("saved whisper");
    SettingsDialog dialog(settings);
    auto *engine = dialog.findChild<QComboBox *>(QStringLiteral("transcriptionEngineComboBox"));
    auto *model = dialog.findChild<QLineEdit *>(QStringLiteral("nemotronModelFileEdit"));
    QVERIFY(engine && model);
    QVERIFY(model->isHidden() || !model->isVisibleTo(&dialog));
    engine->setCurrentIndex(1);
    QVERIFY(model->isVisibleTo(&dialog));
    model->setText(QStringLiteral("decoder.gguf"));
    const Settings nemotron = dialog.settings();
    QCOMPARE(nemotron.transcriptionEngine, QStringLiteral("nemotron"));
    QCOMPARE(nemotron.nemotronModelFile, QStringLiteral("decoder.gguf"));
    QCOMPARE(nemotron.whisperModelDirectory, settings.whisperModelDirectory);
    QCOMPARE(nemotron.beams, settings.beams);
    QCOMPARE(nemotron.windowSeconds, settings.windowSeconds);
    const QString screenshotDirectory = qEnvironmentVariable("AUDIOTRANSCRIBER_TEST_SCREENSHOTS");
    if (!screenshotDirectory.isEmpty()) {
        QVERIFY(dialog.grab().save(screenshotDirectory + QStringLiteral("/nemotron-settings.png")));
    }
    engine->setCurrentIndex(0);
    QCOMPARE(dialog.settings().nemotronModelFile, QStringLiteral("decoder.gguf"));
}

void EngineSelectionTests::mainWindowSwitchesEngineAndLocksDuringProcessing()
{
    Settings settings;
    settings.selectedDevice = QStringLiteral("NPU");
    settings.speakerCount = QStringLiteral("3");
    settings.diarizationEnabled = true;
    settings.hotwordsFile = m_temporary.filePath(QStringLiteral("hotwords.txt"));
    settings.initialPromptFile = m_temporary.filePath(QStringLiteral("prompt.txt"));
    settings.save();
    MainWindow window;
    auto *engine = window.findChild<QComboBox *>(QStringLiteral("transcriptionEngineComboBox"));
    auto *device = window.findChild<QComboBox *>(QStringLiteral("deviceComboBox"));
    auto *speakers = window.findChild<QComboBox *>(QStringLiteral("speakerCountComboBox"));
    QVERIFY(engine && device && speakers);
    QCOMPARE(engine->currentIndex(), 0);
    QCOMPARE(speakers->currentText(), QStringLiteral("3"));
    engine->setCurrentIndex(1);
    QCOMPARE(Settings::load().transcriptionEngine, QStringLiteral("nemotron"));
    QCOMPARE(speakers->currentText(), QStringLiteral("Auto"));
    QVERIFY(!speakers->isEnabled());
    QVERIFY(device->isHidden());
    const QString screenshotDirectory = qEnvironmentVariable("AUDIOTRANSCRIBER_TEST_SCREENSHOTS");
    if (!screenshotDirectory.isEmpty()) {
        QVERIFY(window.grab().save(screenshotDirectory + QStringLiteral("/nemotron-main.png")));
    }
    window.setAppState(AppState::Processing);
    QVERIFY(!engine->isEnabled());
    window.setAppState(AppState::Idle);
    QVERIFY(engine->isEnabled());
    engine->setCurrentIndex(0);
    QCOMPARE(device->currentText(), QStringLiteral("NPU"));
    QCOMPARE(speakers->currentText(), QStringLiteral("3"));
    QVERIFY(speakers->isEnabled());
    QVERIFY(!device->isHidden());
}

QTEST_MAIN(EngineSelectionTests)
#include "tst_engineselection.moc"
