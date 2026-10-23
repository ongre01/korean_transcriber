#include "../input/MultiAudioInputModel.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QtTest>

namespace {
QString createFile(const QTemporaryDir &directory, const QString &name)
{
    const QString path = directory.filePath(name);
    QFile file(path);
    if (!file.open(QIODevice::WriteOnly) || file.write("audio") < 0) {
        return {};
    }
    file.close();
    return path;
}

bool markReady(MultiAudioInputModel &model, const QString &path,
               qint64 durationMilliseconds = 1000)
{
    return model.setInspectionStarted(path)
        && model.setInspectionSucceeded(path, durationMilliseconds);
}
} // namespace

class MultiAudioInputModelTests : public QObject
{
    Q_OBJECT

private slots:
    void exposesAlignmentColumnsAndRawRoles();
    void rejectsCanonicalDuplicates();
    void editsAliasAndUseState();
    void inspectionControlsConfigurationValidity();
    void referenceFollowsRemovalDisableAndFailure();
    void setInputsNormalizesAndDeduplicates();
    void staleInspectionResultIsIgnored();
    void alignmentLifecycleExposesDiagnostics();
    void warningAndFailureRequireManualCorrectionOrExclusion();
    void referenceChangeInvalidatesButRestorePreservesResults();
    void referenceFailureSurvivesRestoreUntilReferenceChanges();
    void inspectionRestartClearsRelevantAlignment();
};

void MultiAudioInputModelTests::exposesAlignmentColumnsAndRawRoles()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString path = createFile(directory, QStringLiteral("room.mic.wav"));
    QVERIFY(!path.isEmpty());

    MultiAudioInputModel model;
    QCOMPARE(model.columnCount(), 8);
    QVERIFY(model.addInput(path));
    QCOMPARE(model.rowCount(), 1);
    QCOMPARE(model.referenceRow(), 0);

    const QModelIndex fileIndex = model.index(0, MultiAudioInputModel::FileColumn);
    QCOMPARE(fileIndex.data(Qt::DisplayRole).toString(), QStringLiteral("room.mic.wav"));
    QCOMPARE(fileIndex.data(MultiAudioInputModel::PathRole).toString(),
             QFileInfo(path).canonicalFilePath());
    QCOMPARE(fileIndex.data(MultiAudioInputModel::AliasRole).toString(),
             QStringLiteral("room.mic"));
    QCOMPARE(fileIndex.data(MultiAudioInputModel::DurationRole).toLongLong(), qint64(-1));
    QCOMPARE(fileIndex.data(MultiAudioInputModel::EnabledRole).toBool(), true);
    QCOMPARE(fileIndex.data(MultiAudioInputModel::ReferenceRole).toBool(), true);
    QCOMPARE(fileIndex.data(MultiAudioInputModel::StatusRole).toInt(),
             static_cast<int>(MultiAudioInput::Status::Pending));
    QCOMPARE(fileIndex.data(MultiAudioInputModel::AlignmentStatusRole).toInt(),
             static_cast<int>(MultiAudioInput::AlignmentStatus::NotAnalyzed));
    QCOMPARE(fileIndex.data(MultiAudioInputModel::HasDriftRole).toBool(), false);
    QCOMPARE(fileIndex.data(MultiAudioInputModel::AlignmentOffsetIsManualRole)
                 .toBool(),
             false);
    QCOMPARE(model.headerData(MultiAudioInputModel::AliasColumn, Qt::Horizontal).toString(),
             QStringLiteral("마이크 별칭"));
    QCOMPARE(model.headerData(MultiAudioInputModel::DriftColumn, Qt::Horizontal).toString(),
             QStringLiteral("드리프트"));
}

void MultiAudioInputModelTests::rejectsCanonicalDuplicates()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString path = createFile(directory, QStringLiteral("mic.wav"));
    QVERIFY(!path.isEmpty());

    MultiAudioInputModel model;
    QVERIFY(model.addInput(path));
    QVERIFY(!model.addInput(QDir(directory.path()).filePath(QStringLiteral("./mic.wav"))));
#ifdef Q_OS_WIN
    QVERIFY(!model.addInput(path.toUpper()));
#endif
    QCOMPARE(model.rowCount(), 1);
    QCOMPARE(model.rowForPath(path), 0);
}

void MultiAudioInputModelTests::editsAliasAndUseState()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString path = createFile(directory, QStringLiteral("mic.wav"));
    MultiAudioInputModel model;
    QVERIFY(model.addInput(path));

    const QModelIndex aliasIndex = model.index(0, MultiAudioInputModel::AliasColumn);
    QVERIFY(model.flags(aliasIndex).testFlag(Qt::ItemIsEditable));
    QVERIFY(model.setData(aliasIndex, QStringLiteral("  발표자  "), Qt::EditRole));
    QCOMPARE(aliasIndex.data(Qt::EditRole).toString(), QStringLiteral("발표자"));

    const QModelIndex useIndex = model.index(0, MultiAudioInputModel::UseColumn);
    QVERIFY(model.flags(useIndex).testFlag(Qt::ItemIsUserCheckable));
    QVERIFY(model.setData(useIndex, Qt::Unchecked, Qt::CheckStateRole));
    QCOMPARE(useIndex.data(Qt::CheckStateRole).toInt(), static_cast<int>(Qt::Unchecked));
    QCOMPARE(model.referenceRow(), -1);
    QVERIFY(model.setData(useIndex, Qt::Checked, Qt::CheckStateRole));
    QCOMPARE(model.referenceRow(), 0);
}

void MultiAudioInputModelTests::inspectionControlsConfigurationValidity()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString first = createFile(directory, QStringLiteral("first.wav"));
    const QString second = createFile(directory, QStringLiteral("second.wav"));
    const QString third = createFile(directory, QStringLiteral("third.wav"));
    MultiAudioInputModel model;
    QVERIFY(model.addInput(first, QStringLiteral("A")));
    QVERIFY(model.addInput(second, QStringLiteral("B")));
    QVERIFY(model.addInput(third, QStringLiteral("C")));

    QSignalSpy validitySpy(&model,
                           &MultiAudioInputModel::configurationValidityChanged);
    QVERIFY(!model.isConfigurationValid());
    QVERIFY(model.setInspectionStarted(first));
    QVERIFY(model.setInspectionSucceeded(first, 61234));
    QVERIFY(!model.isConfigurationValid());
    QCOMPARE(model.index(0, MultiAudioInputModel::DurationColumn)
                 .data(Qt::DisplayRole).toString(),
             QStringLiteral("01:01"));
    QVERIFY(model.setInspectionStarted(second));
    QVERIFY(model.setInspectionSucceeded(second, 60000));
    QVERIFY(!model.isConfigurationValid());
    QVERIFY(model.setData(model.index(2, MultiAudioInputModel::UseColumn),
                          Qt::Unchecked, Qt::CheckStateRole));
    QVERIFY(model.isConfigurationValid());
    QCOMPARE(validitySpy.size(), 1);
    QCOMPARE(validitySpy.takeFirst().at(0).toBool(), true);

    QVERIFY(model.setData(model.index(1, MultiAudioInputModel::AliasColumn),
                          QString(), Qt::EditRole));
    QString reason;
    QVERIFY(!model.isConfigurationValid(&reason));
    QVERIFY(!reason.isEmpty());

    QVERIFY(model.setData(model.index(1, MultiAudioInputModel::AliasColumn),
                          QStringLiteral("B"), Qt::EditRole));
    QVERIFY(QFile::remove(second));
    QVERIFY(!model.isConfigurationValid(&reason));
    QVERIFY(reason.contains(QStringLiteral("찾을 수 없습니다")));
}

void MultiAudioInputModelTests::referenceFollowsRemovalDisableAndFailure()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString first = createFile(directory, QStringLiteral("first.wav"));
    const QString second = createFile(directory, QStringLiteral("second.wav"));
    const QString third = createFile(directory, QStringLiteral("third.wav"));
    MultiAudioInputModel model;
    QVERIFY(model.addInput(first));
    QVERIFY(model.addInput(second));
    QVERIFY(model.addInput(third));
    QVERIFY(model.setInspectionStarted(first));
    QVERIFY(model.setInspectionSucceeded(first, 1000));
    QVERIFY(model.setInspectionStarted(second));
    QVERIFY(model.setInspectionSucceeded(second, 1000));
    QVERIFY(model.setInspectionStarted(third));
    QVERIFY(model.setInspectionSucceeded(third, 1000));
    model.setReferenceRow(2);
    QCOMPARE(model.referenceRow(), 2);

    QVERIFY(model.removeInput(0));
    QCOMPARE(model.referenceRow(), 1);
    QCOMPARE(model.inputAt(model.referenceRow()).path,
             QFileInfo(third).canonicalFilePath());

    QVERIFY(model.setData(model.index(1, MultiAudioInputModel::UseColumn),
                          Qt::Unchecked, Qt::CheckStateRole));
    QCOMPARE(model.referenceRow(), 0);
    QCOMPARE(model.inputAt(0).path, QFileInfo(second).canonicalFilePath());

    QVERIFY(model.setInspectionStarted(second));
    QVERIFY(model.setInspectionFailed(second, QStringLiteral("decode failed")));
    QCOMPARE(model.referenceRow(), -1);
    QCOMPARE(model.index(0, MultiAudioInputModel::StatusColumn)
                 .data(Qt::ToolTipRole).toString(),
             QStringLiteral("decode failed"));
}

void MultiAudioInputModelTests::setInputsNormalizesAndDeduplicates()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString first = createFile(directory, QStringLiteral("first.wav"));
    const QString second = createFile(directory, QStringLiteral("second.wav"));

    MultiAudioInput a;
    a.path = first;
    a.alias = QStringLiteral("A");
    a.status = MultiAudioInput::Status::Ready;
    a.durationMilliseconds = 1000;
    MultiAudioInput duplicate = a;
    duplicate.alias = QStringLiteral("duplicate");
    MultiAudioInput b;
    b.path = second;
    b.alias = QStringLiteral("B");
    b.status = MultiAudioInput::Status::Ready;
    b.durationMilliseconds = 2000;

    MultiAudioInputModel model;
    model.setInputs({a, duplicate, b});
    QCOMPARE(model.rowCount(), 2);
    QCOMPARE(model.inputAt(0).alias, QStringLiteral("A"));
    QCOMPARE(model.referenceRow(), 0);
    QVERIFY(model.isConfigurationValid());
}

void MultiAudioInputModelTests::staleInspectionResultIsIgnored()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString path = createFile(directory, QStringLiteral("removed.wav"));
    MultiAudioInputModel model;
    QVERIFY(model.addInput(path));
    QVERIFY(model.removeInput(0));
    QVERIFY(!model.setInspectionSucceeded(path, 1000));
    QVERIFY(!model.setInspectionFailed(path, QStringLiteral("late result")));
    QCOMPARE(model.rowCount(), 0);

    QVERIFY(model.addInput(path));
    QVERIFY(!model.setInspectionSucceeded(path, 1000));
    QVERIFY(!model.setInspectionFailed(path, QStringLiteral("late result")));
    QCOMPARE(model.inputAt(0).status, MultiAudioInput::Status::Pending);
    QVERIFY(model.setInspectionStarted(path));
    QVERIFY(model.setInspectionSucceeded(path, 1000));
}

void MultiAudioInputModelTests::alignmentLifecycleExposesDiagnostics()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString first = createFile(directory, QStringLiteral("first.wav"));
    const QString second = createFile(directory, QStringLiteral("second.wav"));
    const QString third = createFile(directory, QStringLiteral("third.wav"));

    MultiAudioInputModel model;
    QVERIFY(model.addInput(first, QStringLiteral("A")));
    QVERIFY(model.addInput(second, QStringLiteral("B")));
    QVERIFY(model.addInput(third, QStringLiteral("C")));
    QVERIFY(markReady(model, first));
    QVERIFY(markReady(model, second));
    QVERIFY(markReady(model, third));
    QVERIFY(model.isConfigurationValid());

    QString reason;
    QVERIFY(!model.isAlignmentResolved(&reason));
    QVERIFY(!reason.isEmpty());
    QVERIFY(model.startAlignmentAnalysis());
    QCOMPARE(model.inputAt(0).alignmentStatus,
             MultiAudioInput::AlignmentStatus::Aligned);
    QCOMPARE(model.inputAt(0).alignmentOffsetMilliseconds, qint64(0));
    QCOMPARE(model.inputAt(0).confidence, 1.0);
    QCOMPARE(model.inputAt(1).alignmentStatus,
             MultiAudioInput::AlignmentStatus::Analyzing);

    QVERIFY(model.applyAlignmentResult(
        second,
        MultiAudioInput::AlignmentStatus::Aligned,
        true,
        500,
        0.82,
        true,
        123.4,
        QStringLiteral("affine correction")));
    const QModelIndex offsetIndex = model.index(1, MultiAudioInputModel::OffsetColumn);
    const QModelIndex driftIndex = model.index(1, MultiAudioInputModel::DriftColumn);
    QCOMPARE(offsetIndex.data(MultiAudioInputModel::AlignmentOffsetRole).toLongLong(),
             qint64(500));
    QCOMPARE(driftIndex.data(MultiAudioInputModel::DriftPpmRole).toDouble(),
             123.4);
    QVERIFY(driftIndex.data(Qt::DisplayRole).toString().contains(QStringLiteral("ppm")));
    QCOMPARE(model.index(1, MultiAudioInputModel::StatusColumn)
                 .data(Qt::DisplayRole).toString(),
             QStringLiteral("성공"));

    QVERIFY(model.applyAlignmentResult(
        third,
        MultiAudioInput::AlignmentStatus::Warning,
        false,
        0,
        0.31,
        true,
        -220.0,
        QStringLiteral("manual review required")));
    QVERIFY(!model.isAlignmentResolved(&reason));
    QVERIFY(reason.contains(QStringLiteral("경고")));
    QCOMPARE(model.index(2, MultiAudioInputModel::StatusColumn)
                 .data(Qt::ToolTipRole).toString(),
             QStringLiteral("manual review required"));

    QSignalSpy resolutionSpy(&model,
                             &MultiAudioInputModel::alignmentResolutionChanged);
    QVERIFY(model.setManualAlignmentOffset(third, -125));
    QVERIFY(model.isAlignmentResolved());
    QCOMPARE(resolutionSpy.size(), 1);
    QCOMPARE(resolutionSpy.takeFirst().at(0).toBool(), true);
    const MultiAudioInput manual = model.inputAt(2);
    QCOMPARE(manual.alignmentStatus, MultiAudioInput::AlignmentStatus::Manual);
    QVERIFY(manual.alignmentOffsetIsManual);
    QVERIFY(manual.hasAlignmentOffset);
    QCOMPARE(manual.alignmentOffsetMilliseconds, qint64(-125));
    QCOMPARE(manual.confidence, 0.31);
    QVERIFY(manual.hasDrift);
    QCOMPARE(manual.driftPpm, -220.0);

    model.clearAlignmentResults();
    QVERIFY(!model.isAlignmentResolved());
    QVERIFY(model.startAlignmentAnalysis());
    model.cancelAlignmentAnalysis();
    QCOMPARE(model.inputAt(1).alignmentStatus,
             MultiAudioInput::AlignmentStatus::NotAnalyzed);
    QVERIFY(model.setAlignmentResult(second, 42, 0.75,
                                     QStringLiteral("legacy API")));
    QCOMPARE(model.inputAt(1).alignmentStatus,
             MultiAudioInput::AlignmentStatus::Aligned);
    QVERIFY(!model.inputAt(1).hasDrift);
}

void MultiAudioInputModelTests::warningAndFailureRequireManualCorrectionOrExclusion()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString first = createFile(directory, QStringLiteral("first.wav"));
    const QString second = createFile(directory, QStringLiteral("second.wav"));
    const QString third = createFile(directory, QStringLiteral("third.wav"));
    const QString fourth = createFile(directory, QStringLiteral("fourth.wav"));

    MultiAudioInputModel model;
    for (const QString &path : {first, second, third, fourth}) {
        QVERIFY(model.addInput(path));
        QVERIFY(markReady(model, path));
    }
    QVERIFY(model.startAlignmentAnalysis());
    QVERIFY(model.applyAlignmentResult(
        second, MultiAudioInput::AlignmentStatus::Aligned,
        true, 100, 0.9, true, 5.0));
    QVERIFY(model.applyAlignmentResult(
        third, MultiAudioInput::AlignmentStatus::Warning,
        false, 0, 0.4, false, 0.0, QStringLiteral("low confidence")));
    QVERIFY(model.applyAlignmentResult(
        fourth, MultiAudioInput::AlignmentStatus::Failed,
        false, 0, 0.1, false, 0.0, QStringLiteral("no shared speech")));
    QVERIFY(!model.isAlignmentResolved());

    QSignalSpy dataChangedSpy(&model, &QAbstractItemModel::dataChanged);
    QVERIFY(model.setData(model.index(2, MultiAudioInputModel::UseColumn),
                          Qt::Unchecked, Qt::CheckStateRole));
    QCOMPARE(dataChangedSpy.size(), 2);
    const QList<QVariant> excludedStatusChange = dataChangedSpy.at(1);
    QCOMPARE(excludedStatusChange.at(0).value<QModelIndex>(),
             model.index(2, MultiAudioInputModel::StatusColumn));
    QCOMPARE(excludedStatusChange.at(1).value<QModelIndex>(),
             model.index(2, MultiAudioInputModel::StatusColumn));
    QCOMPARE(excludedStatusChange.at(2).value<QVector<int>>(),
             QVector<int>{Qt::DisplayRole});
    dataChangedSpy.clear();

    QVERIFY(model.setData(model.index(3, MultiAudioInputModel::UseColumn),
                          Qt::Unchecked, Qt::CheckStateRole));
    QCOMPARE(dataChangedSpy.size(), 2);
    const QList<QVariant> failedStatusChange = dataChangedSpy.at(1);
    QCOMPARE(failedStatusChange.at(0).value<QModelIndex>(),
             model.index(3, MultiAudioInputModel::StatusColumn));
    QCOMPARE(failedStatusChange.at(1).value<QModelIndex>(),
             model.index(3, MultiAudioInputModel::StatusColumn));
    QCOMPARE(failedStatusChange.at(2).value<QVector<int>>(),
             QVector<int>{Qt::DisplayRole});
    dataChangedSpy.clear();

    QVERIFY(model.isConfigurationValid());
    QVERIFY(model.isAlignmentResolved());
    QCOMPARE(model.index(2, MultiAudioInputModel::StatusColumn)
                 .data(Qt::DisplayRole).toString(),
             QStringLiteral("제외됨"));
    QCOMPARE(model.index(2, MultiAudioInputModel::StatusColumn)
                 .data(MultiAudioInputModel::AlignmentStatusRole).toInt(),
             static_cast<int>(MultiAudioInput::AlignmentStatus::Warning));
    QCOMPARE(model.index(3, MultiAudioInputModel::StatusColumn)
                 .data(Qt::DisplayRole).toString(),
             QStringLiteral("제외됨"));
    QCOMPARE(model.index(3, MultiAudioInputModel::StatusColumn)
                 .data(MultiAudioInputModel::AlignmentStatusRole).toInt(),
             static_cast<int>(MultiAudioInput::AlignmentStatus::Failed));

    QVERIFY(model.setData(model.index(2, MultiAudioInputModel::UseColumn),
                          Qt::Checked, Qt::CheckStateRole));
    QVERIFY(model.setData(model.index(3, MultiAudioInputModel::UseColumn),
                          Qt::Checked, Qt::CheckStateRole));
    QVERIFY(!model.isAlignmentResolved());
    QVERIFY(model.inputAt(3).manualAlignmentCorrectionAllowed);
    QVERIFY(model.setManualAlignmentOffset(third, -50,
                                           QStringLiteral("operator adjusted")));
    QVERIFY(model.setManualAlignmentOffset(fourth, 200));
    QVERIFY(model.isAlignmentResolved());
    QCOMPARE(model.inputAt(2).alignmentDetail,
             QStringLiteral("operator adjusted"));
    QCOMPARE(model.inputAt(3).alignmentStatus,
             MultiAudioInput::AlignmentStatus::Manual);
    QVERIFY(model.inputAt(3).manualAlignmentCorrectionAllowed);

    QVERIFY(model.applyAlignmentResult(
        fourth, MultiAudioInput::AlignmentStatus::Failed,
        false, 0, 0.0, false, 0.0,
        QStringLiteral("preparation failed"), false));
    QVERIFY(!model.inputAt(3).manualAlignmentCorrectionAllowed);
    QVERIFY(!model.isAlignmentResolved());
    QVERIFY(!model.setManualAlignmentOffset(fourth, 250));
    QVERIFY(!model.isAlignmentResolved());
    QVERIFY(model.setData(model.index(3, MultiAudioInputModel::UseColumn),
                          Qt::Unchecked, Qt::CheckStateRole));
    QVERIFY(model.isAlignmentResolved());
}

void MultiAudioInputModelTests::referenceChangeInvalidatesButRestorePreservesResults()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString first = createFile(directory, QStringLiteral("first.wav"));
    const QString second = createFile(directory, QStringLiteral("second.wav"));
    const QString third = createFile(directory, QStringLiteral("third.wav"));

    MultiAudioInputModel model;
    for (const QString &path : {first, second, third}) {
        QVERIFY(model.addInput(path));
        QVERIFY(markReady(model, path));
    }
    QVERIFY(model.startAlignmentAnalysis());
    QVERIFY(model.setAlignmentResult(second, 100, 0.9));
    QVERIFY(model.setAlignmentResult(third, 200, 0.8));
    QVERIFY(model.isAlignmentResolved());

    model.setReferenceRow(1);
    QCOMPARE(model.referenceRow(), 1);
    QCOMPARE(model.inputAt(1).alignmentStatus,
             MultiAudioInput::AlignmentStatus::Aligned);
    QCOMPARE(model.inputAt(1).alignmentOffsetMilliseconds, qint64(0));
    QCOMPARE(model.inputAt(0).alignmentStatus,
             MultiAudioInput::AlignmentStatus::NotAnalyzed);
    QCOMPARE(model.inputAt(2).alignmentStatus,
             MultiAudioInput::AlignmentStatus::NotAnalyzed);
    QVERIFY(!model.isAlignmentResolved());

    QVERIFY(model.applyAlignmentResult(
        first, MultiAudioInput::AlignmentStatus::Warning,
        false, 0, 0.4, false, 0.0,
        QStringLiteral("manual review required")));
    QVERIFY(model.setManualAlignmentOffset(first, -125));
    QVERIFY(model.setAlignmentResult(third, 250, 0.85));
    QVERIFY(model.isAlignmentResolved());
    const QVector<MultiAudioInput> saved = model.inputs();

    MultiAudioInputModel restored;
    restored.setInputs(saved);
    QCOMPARE(restored.referenceRow(), 0);
    QVERIFY(restored.restoreReferenceRow(1));
    QCOMPARE(restored.referenceRow(), 1);
    QCOMPARE(restored.inputAt(0).alignmentOffsetMilliseconds, qint64(-125));
    QVERIFY(restored.inputAt(0).alignmentOffsetIsManual);
    QCOMPARE(restored.inputAt(2).alignmentOffsetMilliseconds, qint64(250));
    QVERIFY(restored.isAlignmentResolved());

    restored.setReferenceRow(2);
    QCOMPARE(restored.referenceRow(), 2);
    QCOMPARE(restored.inputAt(0).alignmentStatus,
             MultiAudioInput::AlignmentStatus::NotAnalyzed);
    QCOMPARE(restored.inputAt(1).alignmentStatus,
             MultiAudioInput::AlignmentStatus::NotAnalyzed);
    QVERIFY(!restored.isAlignmentResolved());
}

void MultiAudioInputModelTests::referenceFailureSurvivesRestoreUntilReferenceChanges()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString first = createFile(directory, QStringLiteral("first.wav"));
    const QString second = createFile(directory, QStringLiteral("second.wav"));
    const QString third = createFile(directory, QStringLiteral("third.wav"));

    MultiAudioInputModel model;
    for (const QString &path : {first, second, third}) {
        QVERIFY(model.addInput(path));
        QVERIFY(markReady(model, path));
    }
    model.setReferenceRow(1);
    QCOMPARE(model.referenceRow(), 1);
    QVERIFY(model.startAlignmentAnalysis());
    QVERIFY(model.applyAlignmentResult(
        first, MultiAudioInput::AlignmentStatus::Aligned,
        true, -100, 0.9, true, 2.0));
    QVERIFY(model.applyAlignmentResult(
        second, MultiAudioInput::AlignmentStatus::Failed,
        false, 0, 0.0, false, 0.0,
        QStringLiteral("reference preparation failed")));
    QVERIFY(model.applyAlignmentResult(
        third, MultiAudioInput::AlignmentStatus::Aligned,
        true, 200, 0.8, true, -3.0));

    QString reason;
    QVERIFY(!model.isAlignmentResolved(&reason));
    QVERIFY(reason.contains(QStringLiteral("기준")));
    QVERIFY(reason.contains(QStringLiteral("실패")));
    QCOMPARE(model.inputAt(1).alignmentStatus,
             MultiAudioInput::AlignmentStatus::Failed);
    QCOMPARE(model.index(1, MultiAudioInputModel::StatusColumn)
                 .data(Qt::DisplayRole).toString(),
             QStringLiteral("실패"));
    QCOMPARE(model.index(1, MultiAudioInputModel::StatusColumn)
                 .data(Qt::ToolTipRole).toString(),
             QStringLiteral("reference preparation failed"));

    const QVector<MultiAudioInput> saved = model.inputs();
    MultiAudioInputModel restored;
    restored.setInputs(saved);
    QCOMPARE(restored.referenceRow(), 0);
    QCOMPARE(restored.inputAt(1).alignmentStatus,
             MultiAudioInput::AlignmentStatus::Failed);
    QCOMPARE(restored.inputAt(1).alignmentDetail,
             QStringLiteral("reference preparation failed"));

    QVERIFY(restored.restoreReferenceRow(1));
    QCOMPARE(restored.referenceRow(), 1);
    QCOMPARE(restored.inputAt(1).alignmentStatus,
             MultiAudioInput::AlignmentStatus::Failed);
    QCOMPARE(restored.inputAt(1).alignmentDetail,
             QStringLiteral("reference preparation failed"));
    QVERIFY(!restored.isAlignmentResolved(&reason));
    QVERIFY(reason.contains(QStringLiteral("기준")));
    QVERIFY(reason.contains(QStringLiteral("실패")));

    restored.setReferenceRow(2);
    QCOMPARE(restored.referenceRow(), 2);
    QCOMPARE(restored.inputAt(0).alignmentStatus,
             MultiAudioInput::AlignmentStatus::NotAnalyzed);
    QCOMPARE(restored.inputAt(1).alignmentStatus,
             MultiAudioInput::AlignmentStatus::NotAnalyzed);
    QVERIFY(restored.inputAt(1).alignmentDetail.isEmpty());
    const MultiAudioInput newReference = restored.inputAt(2);
    QCOMPARE(newReference.alignmentStatus,
             MultiAudioInput::AlignmentStatus::Aligned);
    QVERIFY(newReference.hasAlignmentOffset);
    QCOMPARE(newReference.alignmentOffsetMilliseconds, qint64(0));
    QVERIFY(!newReference.alignmentOffsetIsManual);
    QVERIFY(newReference.hasDrift);
    QCOMPARE(newReference.driftPpm, 0.0);
    QVERIFY(!restored.isAlignmentResolved());
}

void MultiAudioInputModelTests::inspectionRestartClearsRelevantAlignment()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString first = createFile(directory, QStringLiteral("first.wav"));
    const QString second = createFile(directory, QStringLiteral("second.wav"));
    const QString third = createFile(directory, QStringLiteral("third.wav"));

    MultiAudioInputModel model;
    for (const QString &path : {first, second, third}) {
        QVERIFY(model.addInput(path));
        QVERIFY(markReady(model, path));
    }
    QVERIFY(model.startAlignmentAnalysis());
    QVERIFY(model.setAlignmentResult(second, 100, 0.9));
    QVERIFY(model.setAlignmentResult(third, 200, 0.8));
    QVERIFY(model.isAlignmentResolved());

    QVERIFY(model.setInspectionStarted(second));
    QCOMPARE(model.inputAt(1).alignmentStatus,
             MultiAudioInput::AlignmentStatus::NotAnalyzed);
    QCOMPARE(model.inputAt(2).alignmentStatus,
             MultiAudioInput::AlignmentStatus::Aligned);
    QVERIFY(!model.isAlignmentResolved());
    QVERIFY(model.setInspectionSucceeded(second, 1500));
    QVERIFY(model.setAlignmentResult(second, 110, 0.91));
    QVERIFY(model.isAlignmentResolved());

    QVERIFY(model.setInspectionStarted(first));
    QCOMPARE(model.inputAt(1).alignmentStatus,
             MultiAudioInput::AlignmentStatus::NotAnalyzed);
    QCOMPARE(model.inputAt(2).alignmentStatus,
             MultiAudioInput::AlignmentStatus::NotAnalyzed);
    QVERIFY(!model.inputAt(1).hasAlignmentOffset);
    QVERIFY(!model.inputAt(2).hasAlignmentOffset);
    QVERIFY(model.setInspectionSucceeded(first, 2000));
    QCOMPARE(model.inputAt(0).alignmentStatus,
             MultiAudioInput::AlignmentStatus::Aligned);
    QVERIFY(!model.isAlignmentResolved());
}

QTEST_GUILESS_MAIN(MultiAudioInputModelTests)
#include "tst_multiaudioinputmodel.moc"
