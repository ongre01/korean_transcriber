#include "../app/EnginePaths.h"
#include "../settings/Settings.h"
#include "EnginePathsConfig.h"

#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QSettings>
#include <QTemporaryDir>
#include <QtTest>

namespace {
const QString modelRelativePath = QStringLiteral("models/whisper-large-v3-turbo-int8");

QString pythonRelativePath()
{
#ifdef Q_OS_WIN
    return QStringLiteral(".venv/Scripts/python.exe");
#else
    return QStringLiteral(".venv/bin/python");
#endif
}

bool createFile(const QString &path)
{
    return QDir().mkpath(QFileInfo(path).absolutePath())
        && QFile(path).open(QIODevice::WriteOnly);
}

QString createEngine(const QString &path, bool withPython = true, bool withModel = true)
{
    if (!createFile(QDir(path).filePath(QStringLiteral("backend_bridge.py")))
        || !createFile(QDir(path).filePath(QStringLiteral("audio_metadata.py")))
        || (withPython && !createFile(QDir(path).filePath(pythonRelativePath())))
        || (withModel && !QDir().mkpath(QDir(path).filePath(modelRelativePath)))) {
        return {};
    }
    return QFileInfo(path).canonicalFilePath();
}

QString modelPath(const QString &engine)
{
    return QFileInfo(QDir(engine).filePath(modelRelativePath)).canonicalFilePath();
}
} // namespace

class EnginePathsTests : public QObject
{
    Q_OBJECT

private slots:
    void deepShadowBuildFindsRepository();
    void deploymentWinsAndDoesNotMixInstallations();
    void externalShadowBuildUsesConfiguredSource();
    void workingDirectoryFallback();
    void missingAssetsStayEmpty();
    void defaultAndLegacyPythonUseVirtualenv();
    void explicitPythonAndEnvironmentOverride();
    void staleShadowModelMigrates();
    void explicitModelsRemainUnchanged();
    void missingDefaultDoesNotRewriteSavedModel();
    void configuredRuntimeSourceIsUsable();
    void persistedSettingsLoadAndSave();
};

void EnginePathsTests::deepShadowBuildFindsRepository()
{
    QTemporaryDir temporary;
    QVERIFY(temporary.isValid());
    const QString engine = createEngine(temporary.filePath(QStringLiteral("project/engine")));
    QVERIFY(!engine.isEmpty());
    const QString application = temporary.filePath(
        QStringLiteral("project/gui/build/a/b/c/d/e/f/g/debug"));
    QVERIFY(QDir().mkpath(application));
    const auto paths = EnginePaths::discover(application, {}, {});
    QCOMPARE(paths.engineDirectory(), engine);
    QCOMPARE(paths.assetPath(QStringLiteral("backend_bridge.py")),
             QFileInfo(QDir(engine).filePath(QStringLiteral("backend_bridge.py"))).canonicalFilePath());
}

void EnginePathsTests::deploymentWinsAndDoesNotMixInstallations()
{
    QTemporaryDir temporary;
    QVERIFY(temporary.isValid());
    const QString deployed = createEngine(temporary.filePath(QStringLiteral("deployment/engine")),
                                          false, false);
    const QString source = createEngine(temporary.filePath(QStringLiteral("source/engine")));
    const QString cwdEngine = createEngine(temporary.filePath(QStringLiteral("cwd/engine")));
    QVERIFY(!deployed.isEmpty() && !source.isEmpty() && !cwdEngine.isEmpty());
    const auto paths = EnginePaths::discover(temporary.filePath(QStringLiteral("deployment")),
                                            source, temporary.filePath(QStringLiteral("cwd")));
    QCOMPARE(paths.engineDirectory(), deployed);
    QVERIFY(paths.assetPath(modelRelativePath).isEmpty());
    QCOMPARE(paths.defaultPythonProgram(), QStringLiteral("python"));
}

void EnginePathsTests::externalShadowBuildUsesConfiguredSource()
{
    QTemporaryDir temporary;
    QVERIFY(temporary.isValid());
    const QString source = createEngine(temporary.filePath(QStringLiteral("source/engine")));
    QVERIFY(!source.isEmpty());
    QVERIFY(!createEngine(temporary.filePath(QStringLiteral("cwd/engine"))).isEmpty());
    const QString application = temporary.filePath(QStringLiteral("unrelated/build/debug"));
    QVERIFY(QDir().mkpath(application));
    const auto paths = EnginePaths::discover(application, source,
                                            temporary.filePath(QStringLiteral("cwd")));
    QCOMPARE(paths.engineDirectory(), source);
}

void EnginePathsTests::workingDirectoryFallback()
{
    QTemporaryDir temporary;
    QVERIFY(temporary.isValid());
    const QString engine = createEngine(temporary.filePath(QStringLiteral("cwd/engine")));
    QVERIFY(!engine.isEmpty());
    const auto paths = EnginePaths::discover({}, {}, temporary.filePath(QStringLiteral("cwd")));
    QCOMPARE(paths.engineDirectory(), engine);
}

void EnginePathsTests::missingAssetsStayEmpty()
{
    QTemporaryDir temporary;
    QVERIFY(temporary.isValid());
    const auto absent = EnginePaths::discover({}, {}, temporary.path());
    QVERIFY(absent.engineDirectory().isEmpty());
    QVERIFY(absent.assetPath(modelRelativePath).isEmpty());
    Settings settings;
    settings.applyEngineDefaults(absent);
    QVERIFY(settings.whisperModelDirectory.isEmpty());

    const QString engine = createEngine(temporary.filePath(QStringLiteral("engine")), true, false);
    QVERIFY(!engine.isEmpty());
    settings.applyEngineDefaults(EnginePaths::discover({}, engine, {}));
    QVERIFY(settings.whisperModelDirectory.isEmpty());
}

void EnginePathsTests::defaultAndLegacyPythonUseVirtualenv()
{
    QTemporaryDir temporary;
    QVERIFY(temporary.isValid());
    const QString engine = createEngine(temporary.filePath(QStringLiteral("engine")));
    QVERIFY(!engine.isEmpty());
    const auto paths = EnginePaths::discover({}, engine, {});
    const QString python = QFileInfo(QDir(engine).filePath(pythonRelativePath())).canonicalFilePath();
    Settings settings;
    settings.applyEngineDefaults(paths);
    QCOMPARE(settings.pythonPath, python);
    QCOMPARE(settings.whisperModelDirectory, modelPath(engine));
    settings.pythonPath = QStringLiteral(" python ");
    settings.applyEngineDefaults(paths);
    QCOMPARE(settings.pythonPath, python);
}

void EnginePathsTests::explicitPythonAndEnvironmentOverride()
{
    QTemporaryDir temporary;
    QVERIFY(temporary.isValid());
    const QString engine = createEngine(temporary.filePath(QStringLiteral("engine")));
    QVERIFY(!engine.isEmpty());
    const auto paths = EnginePaths::discover({}, engine, {});
    Settings settings;
    settings.pythonPath = QStringLiteral("python");
    settings.applyEngineDefaults(paths, QStringLiteral("  custom-python-command  "));
    QCOMPARE(settings.pythonPath, QStringLiteral("custom-python-command"));

    const QString explicitProgram = temporary.filePath(QStringLiteral("custom/python.exe"));
    settings.pythonPath = explicitProgram;
    settings.applyEngineDefaults(paths, QStringLiteral("environment-python"));
    QCOMPARE(settings.pythonPath, explicitProgram);
    settings.pythonPath = QStringLiteral("python3");
    settings.applyEngineDefaults(paths);
    QCOMPARE(settings.pythonPath, QStringLiteral("python3"));
}

void EnginePathsTests::staleShadowModelMigrates()
{
    QTemporaryDir temporary;
    QVERIFY(temporary.isValid());
    const QString engine = createEngine(temporary.filePath(QStringLiteral("project/engine")));
    QVERIFY(!engine.isEmpty());
    const QString application = temporary.filePath(QStringLiteral("external/debug"));
    const auto paths = EnginePaths::discover(application, engine, {});
    Settings settings;
    settings.whisperModelDirectory = temporary.filePath(QStringLiteral(
        "project/gui/build/Desktop_Qt_6_11_2_MSVC2022_64bit_Debug/debug/engine/"))
        + modelRelativePath;
    settings.applyEngineDefaults(paths);
    QCOMPARE(settings.whisperModelDirectory, modelPath(engine));

    // An out-of-tree shadow build previously saved its executable-local default.
    settings.whisperModelDirectory = QDir(application).filePath(
        QStringLiteral("engine/") + modelRelativePath);
    settings.applyEngineDefaults(paths);
    QCOMPARE(settings.whisperModelDirectory, modelPath(engine));
}

void EnginePathsTests::explicitModelsRemainUnchanged()
{
    QTemporaryDir temporary;
    QVERIFY(temporary.isValid());
    const QString engine = createEngine(temporary.filePath(QStringLiteral("project/engine")));
    QVERIFY(!engine.isEmpty());
    const auto paths = EnginePaths::discover({}, engine, {});
    Settings settings;
    const QString customMissing = temporary.filePath(QStringLiteral("custom/engine/"))
        + modelRelativePath;
    settings.whisperModelDirectory = customMissing;
    settings.applyEngineDefaults(paths);
    QCOMPARE(settings.whisperModelDirectory, customMissing);

    const QString customExisting = temporary.filePath(QStringLiteral("other-model"));
    QVERIFY(QDir().mkpath(customExisting));
    settings.whisperModelDirectory = customExisting;
    settings.applyEngineDefaults(paths);
    QCOMPARE(settings.whisperModelDirectory, customExisting);
}

void EnginePathsTests::missingDefaultDoesNotRewriteSavedModel()
{
    QTemporaryDir temporary;
    QVERIFY(temporary.isValid());
    const QString engine = createEngine(temporary.filePath(QStringLiteral("project/engine")), true, false);
    QVERIFY(!engine.isEmpty());
    const auto paths = EnginePaths::discover({}, engine, {});
    Settings settings;
    const QString legacy = temporary.filePath(QStringLiteral("project/gui/build/debug/engine/"))
        + modelRelativePath;
    settings.whisperModelDirectory = legacy;
    settings.applyEngineDefaults(paths);
    QCOMPARE(settings.whisperModelDirectory, legacy);
}

void EnginePathsTests::configuredRuntimeSourceIsUsable()
{
    const QString source = QString::fromUtf8(AUDIOTRANSCRIBER_SOURCE_ENGINE_DIR);
    QVERIFY2(QFileInfo(QDir(source).filePath(QStringLiteral("backend_bridge.py"))).isFile(),
             qPrintable(source));
    QVERIFY(!EnginePaths::discover().engineDirectory().isEmpty());
}

void EnginePathsTests::persistedSettingsLoadAndSave()
{
    QTemporaryDir temporary;
    QVERIFY(temporary.isValid());
    QSettings::setDefaultFormat(QSettings::IniFormat);
    QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, temporary.path());
    QCoreApplication::setOrganizationName(QStringLiteral("EnginePathsTests"));
    QCoreApplication::setApplicationName(QStringLiteral("IsolatedSettings"));
    Settings settings;
    settings.pythonPath = temporary.filePath(QStringLiteral("custom/python.exe"));
    settings.whisperModelDirectory = temporary.filePath(QStringLiteral("custom-missing-model"));
    settings.save();
    const Settings loaded = Settings::load();
    QCOMPARE(loaded.pythonPath, settings.pythonPath);
    QCOMPARE(loaded.whisperModelDirectory, settings.whisperModelDirectory);

    settings.pythonPath = QStringLiteral("python");
    settings.save();
    const Settings migrated = Settings::load();
    QCOMPARE(migrated.pythonPath, EnginePaths::discover().defaultPythonProgram(
                 QString::fromLocal8Bit(qgetenv("PYTHON"))));
    QCOMPARE(migrated.whisperModelDirectory, settings.whisperModelDirectory);
}

QTEST_GUILESS_MAIN(EnginePathsTests)
#include "tst_enginepaths.moc"
