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
} // namespace

class MultiAudioInputModelTests : public QObject
{
    Q_OBJECT

private slots:
    void exposesSevenColumnsAndRawRoles();
    void rejectsCanonicalDuplicates();
    void editsAliasAndUseState();
    void inspectionControlsConfigurationValidity();
    void referenceFollowsRemovalDisableAndFailure();
    void setInputsNormalizesAndDeduplicates();
    void staleInspectionResultIsIgnored();
};

void MultiAudioInputModelTests::exposesSevenColumnsAndRawRoles()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString path = createFile(directory, QStringLiteral("room.mic.wav"));
    QVERIFY(!path.isEmpty());

    MultiAudioInputModel model;
    QCOMPARE(model.columnCount(), 7);
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
    QCOMPARE(model.headerData(MultiAudioInputModel::AliasColumn, Qt::Horizontal).toString(),
             QStringLiteral("마이크 별칭"));
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

QTEST_GUILESS_MAIN(MultiAudioInputModelTests)
#include "tst_multiaudioinputmodel.moc"
