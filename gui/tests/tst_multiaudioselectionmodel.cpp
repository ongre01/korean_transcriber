#include "../input/MultiAudioSelectionModel.h"

#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
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
    return QFileInfo(path).canonicalFilePath();
}

MultiAudioInput readyInput(const QString &path, const QString &alias)
{
    MultiAudioInput input;
    input.path = path;
    input.alias = alias;
    input.durationMilliseconds = 9000;
    input.status = MultiAudioInput::Status::Ready;
    return input;
}

MultiAudioInput referenceInput(const QString &path, const QString &alias)
{
    MultiAudioInput input = readyInput(path, alias);
    input.hasAlignmentOffset = true;
    input.hasDrift = true;
    input.confidence = 1.0;
    input.alignmentStatus = MultiAudioInput::AlignmentStatus::Aligned;
    return input;
}

MultiAudioInput alignedInput(const QString &path, const QString &alias,
                             qint64 offsetMilliseconds)
{
    MultiAudioInput input = readyInput(path, alias);
    input.hasAlignmentOffset = true;
    input.alignmentOffsetMilliseconds = offsetMilliseconds;
    input.hasDrift = true;
    input.driftPpm = -42.5;
    input.confidence = 0.91;
    input.alignmentStatus = MultiAudioInput::AlignmentStatus::Aligned;
    return input;
}

MultiAudioInput manualInput(const QString &path, const QString &alias,
                            qint64 offsetMilliseconds)
{
    MultiAudioInput input = readyInput(path, alias);
    input.hasAlignmentOffset = true;
    input.alignmentOffsetMilliseconds = offsetMilliseconds;
    input.alignmentOffsetIsManual = true;
    input.manualAlignmentCorrectionAllowed = true;
    input.confidence = 0.12;
    input.alignmentStatus = MultiAudioInput::AlignmentStatus::Manual;
    return input;
}

QJsonObject evidence(int inputIndex, const QJsonValue &score, double validRatio)
{
    QJsonObject object;
    object.insert(QStringLiteral("input_index"), inputIndex);
    object.insert(QStringLiteral("quality_score"), score);
    object.insert(QStringLiteral("valid_ratio"), validRatio);
    return object;
}

QJsonObject segment(qint64 start, qint64 end, int automaticIndex, int selectedIndex,
                    const QJsonArray &inputs)
{
    const bool manual = automaticIndex != selectedIndex;
    QJsonObject automatic;
    automatic.insert(QStringLiteral("input_index"), automaticIndex);
    automatic.insert(QStringLiteral("quality_score"),
                     inputs.at(automaticIndex).toObject().value(
                         QStringLiteral("quality_score")));
    automatic.insert(QStringLiteral("confidence"), 0.75);
    automatic.insert(QStringLiteral("reason"), QStringLiteral("best_quality"));
    automatic.insert(QStringLiteral("reason_codes"),
                     QJsonArray{QStringLiteral("best_quality"),
                                QStringLiteral("hysteresis_hold")});
    automatic.insert(QStringLiteral("segment_start_sample"), start);
    automatic.insert(QStringLiteral("segment_end_sample"), end);
    QJsonObject object;
    object.insert(QStringLiteral("start_sample"), start);
    object.insert(QStringLiteral("end_sample"), end);
    object.insert(QStringLiteral("start"), start / 16000.0);
    object.insert(QStringLiteral("end"), end / 16000.0);
    object.insert(QStringLiteral("selected_input_index"), selectedIndex);
    object.insert(QStringLiteral("automatic_input_index"), automaticIndex);
    object.insert(QStringLiteral("selection_source"),
                  manual ? QStringLiteral("manual_override")
                         : QStringLiteral("automatic"));
    object.insert(QStringLiteral("quality_score"),
                  inputs.at(selectedIndex).toObject().value(
                      QStringLiteral("quality_score")));
    object.insert(QStringLiteral("confidence"), manual ? 1.0 : 0.75);
    object.insert(QStringLiteral("reason"),
                  manual ? QStringLiteral("manual_override")
                         : QStringLiteral("best_quality"));
    object.insert(QStringLiteral("reason_codes"),
                  QJsonArray{manual ? QStringLiteral("manual_override")
                                    : QStringLiteral("best_quality")});
    object.insert(QStringLiteral("automatic"), automatic);
    object.insert(QStringLiteral("inputs"), inputs);
    return object;
}

// Mirrors the shape of a `multimic_quality.py` success response for the
// three-input manifest used by these tests.
QJsonObject qualityResponse()
{
    QJsonObject alignment;
    alignment.insert(QStringLiteral("reference_index"), 0);
    QJsonArray alignmentInputs;
    const QList<QPair<QString, double>> sources{
        {QStringLiteral("reference"), 0.0},
        {QStringLiteral("automatic"), 1250.25},
        {QStringLiteral("manual"), 475.0}};
    for (int index = 0; index < sources.size(); ++index) {
        QJsonObject item;
        item.insert(QStringLiteral("input_index"), index);
        item.insert(QStringLiteral("source"), sources.at(index).first);
        item.insert(QStringLiteral("status"), QStringLiteral("aligned"));
        item.insert(QStringLiteral("offset_ms"), sources.at(index).second);
        alignmentInputs.append(item);
    }
    alignment.insert(QStringLiteral("inputs"), alignmentInputs);

    QJsonArray segments;
    segments.append(segment(0, 16000, 0, 0,
                            {evidence(0, 0.91, 1.0), evidence(1, 0.40, 1.0),
                             evidence(2, QJsonValue(), 0.0)}));
    segments.append(segment(16000, 40000, 1, 2,
                            {evidence(0, 0.35, 1.0), evidence(1, 0.88, 1.0),
                             evidence(2, 0.62, 0.75)}));
    segments.append(segment(40000, 48000, 0, 0,
                            {evidence(0, 0.83, 1.0), evidence(1, 0.80, 1.0),
                             evidence(2, 0.71, 1.0)}));

    QJsonObject lowQuality;
    lowQuality.insert(QStringLiteral("code"), QStringLiteral("all_channels_low_quality"));
    lowQuality.insert(QStringLiteral("message"), QStringLiteral("All low."));
    lowQuality.insert(QStringLiteral("start_sample"), 40000);
    lowQuality.insert(QStringLiteral("end_sample"), 48000);
    QJsonObject unavailable;
    unavailable.insert(QStringLiteral("code"),
                       QStringLiteral("manual_override_unavailable"));
    unavailable.insert(QStringLiteral("message"), QStringLiteral("No audio."));
    unavailable.insert(QStringLiteral("start_sample"), 16000);
    unavailable.insert(QStringLiteral("end_sample"), 22000);

    QJsonObject selection;
    selection.insert(QStringLiteral("sample_rate"), 16000);
    selection.insert(QStringLiteral("timeline_sample_count"), 48000);
    selection.insert(QStringLiteral("rows"), segments);
    selection.insert(QStringLiteral("warnings"), QJsonArray{lowQuality, unavailable});

    QJsonObject payload;
    payload.insert(QStringLiteral("version"), 1);
    payload.insert(QStringLiteral("ok"), true);
    payload.insert(QStringLiteral("warnings"), QJsonArray());
    payload.insert(QStringLiteral("alignment"), alignment);
    payload.insert(QStringLiteral("selection"), selection);
    return payload;
}
} // namespace

class MultiAudioSelectionModelTests : public QObject
{
    Q_OBJECT

private slots:
    void init();
    void parsesResponseAndKeepsAutomaticEvidence();
    void rejectsMalformedOrInconsistentResponses_data();
    void rejectsMalformedOrInconsistentResponses();
    void overridesAreDistinguishableAndOnlyEditableWhileCurrent();
    void manifestCarriesReviewedOffsetsAndOverrides();
    void manifestDropsOverridesThatNoLongerFit();
    void analysisKeyTracksWhatTheAnalysisDependsOn();
    void formatsPositionsAndMissingAudio();

private:
    QVector<MultiAudioInput> inputs() const;
    MultiAudioSelectionPlan parsedPlan() const;

    QTemporaryDir m_directory;
    QString m_first;
    QString m_second;
    QString m_third;
    QString m_excluded;
};

void MultiAudioSelectionModelTests::init()
{
    QVERIFY(m_directory.isValid());
    m_first = createFile(m_directory, QStringLiteral("a.wav"));
    m_second = createFile(m_directory, QStringLiteral("b.wav"));
    m_third = createFile(m_directory, QStringLiteral("c.wav"));
    m_excluded = createFile(m_directory, QStringLiteral("excluded.wav"));
    QVERIFY(!m_first.isEmpty() && !m_second.isEmpty() && !m_third.isEmpty()
            && !m_excluded.isEmpty());
}

QVector<MultiAudioInput> MultiAudioSelectionModelTests::inputs() const
{
    MultiAudioInput excluded = readyInput(m_excluded, QStringLiteral("X"));
    excluded.enabled = false;
    return {referenceInput(m_first, QStringLiteral("A")),
            excluded,
            alignedInput(m_second, QStringLiteral("B"), 1250),
            manualInput(m_third, QStringLiteral("C"), 475)};
}

MultiAudioSelectionPlan MultiAudioSelectionModelTests::parsedPlan() const
{
    const QVector<MultiAudioInput> all = inputs();
    const QVector<MultiAudioInput> manifestInputs{all.at(0), all.at(2), all.at(3)};
    MultiAudioSelectionPlan plan;
    QString error;
    if (!parseMultiAudioQualityResponse(qualityResponse(), manifestInputs, 0,
                                        &plan, &error)) {
        qWarning() << error;
        return {};
    }
    plan.analysisKey = MultiAudioInputModel::selectionAnalysisKey(all, 0);
    plan.timelineKey = MultiAudioInputModel::selectionTimelineKey(all, 0);
    return plan;
}

void MultiAudioSelectionModelTests::parsesResponseAndKeepsAutomaticEvidence()
{
    const MultiAudioSelectionPlan plan = parsedPlan();
    QCOMPARE(plan.segments.size(), 3);
    QCOMPARE(plan.sampleRate, 16000);
    QCOMPARE(plan.timelineSampleCount, qint64(48000));
    QCOMPARE(plan.inputPaths, QStringList({m_first, m_second, m_third}));

    const MultiAudioSelectionSegment &manual = plan.segments.at(1);
    QCOMPARE(manual.startSample, qint64(16000));
    QCOMPARE(manual.endSample, qint64(40000));
    QVERIFY(manual.isManual());
    QCOMPARE(manual.automaticInputPath, m_second);
    QCOMPARE(manual.overrideInputPath, m_third);
    QCOMPARE(manual.finalInputPath(), m_third);
    QVERIFY(manual.hasAutomaticScore);
    QCOMPARE(manual.automaticScore, 0.88);
    QCOMPARE(manual.automaticConfidence, 0.75);
    QCOMPARE(manual.automaticReasonCodes,
             QStringList({QStringLiteral("best_quality"),
                          QStringLiteral("hysteresis_hold")}));
    QCOMPARE(manual.automaticSegmentStartSample, qint64(16000));
    QCOMPARE(manual.automaticSegmentEndSample, qint64(40000));
    QVERIFY(manual.evidence(m_third));
    QCOMPARE(manual.evidence(m_third)->validRatio, 0.75);

    QVERIFY(!plan.segments.at(0).isManual());
    QVERIFY(!plan.segments.at(0).inputs.at(2).hasScore);
    QCOMPARE(plan.manualSegmentCount(), 1);
    QCOMPARE(plan.switchCount(), 2);
    QCOMPARE(plan.incompleteFinalSegmentCount(), 1);
    // Missing audio under a manual choice is derived locally, not copied.
    QCOMPARE(plan.warnings.size(), 1);
    QVERIFY(plan.warnings.constFirst().startsWith(QStringLiteral("00:02.5–00:03.0")));
}

void MultiAudioSelectionModelTests::rejectsMalformedOrInconsistentResponses_data()
{
    QTest::addColumn<QByteArray>("change");
    QTest::newRow("gap") << QByteArray("gap");
    QTest::newRow("short timeline") << QByteArray("short_timeline");
    QTest::newRow("automatic offset changed") << QByteArray("automatic_offset");
    QTest::newRow("manual reported as automatic") << QByteArray("manual_source");
    QTest::newRow("wrong reference") << QByteArray("reference");
    QTest::newRow("missing evidence") << QByteArray("missing_evidence");
    QTest::newRow("score above one") << QByteArray("score");
    QTest::newRow("automatic with different selection") << QByteArray("source_mismatch");
    QTest::newRow("not ok") << QByteArray("not_ok");
    QTest::newRow("automatic segment outside row")
        << QByteArray("automatic_segment");
}

void MultiAudioSelectionModelTests::rejectsMalformedOrInconsistentResponses()
{
    QFETCH(QByteArray, change);
    QJsonObject payload = qualityResponse();
    QJsonObject selection = payload.value(QStringLiteral("selection")).toObject();
    QJsonArray segments = selection.value(QStringLiteral("rows")).toArray();
    QJsonObject alignment = payload.value(QStringLiteral("alignment")).toObject();
    QJsonArray alignmentInputs = alignment.value(QStringLiteral("inputs")).toArray();

    if (change == "gap") {
        QJsonObject item = segments.at(1).toObject();
        item.insert(QStringLiteral("start_sample"), 16001);
        segments.replace(1, item);
    } else if (change == "short_timeline") {
        selection.insert(QStringLiteral("timeline_sample_count"), 48001);
    } else if (change == "automatic_offset") {
        QJsonObject item = alignmentInputs.at(1).toObject();
        item.insert(QStringLiteral("offset_ms"), 1300.0);
        alignmentInputs.replace(1, item);
    } else if (change == "manual_source") {
        QJsonObject item = alignmentInputs.at(2).toObject();
        item.insert(QStringLiteral("source"), QStringLiteral("automatic"));
        alignmentInputs.replace(2, item);
    } else if (change == "reference") {
        alignment.insert(QStringLiteral("reference_index"), 1);
    } else if (change == "missing_evidence") {
        QJsonObject item = segments.at(0).toObject();
        QJsonArray inputs = item.value(QStringLiteral("inputs")).toArray();
        inputs.removeLast();
        item.insert(QStringLiteral("inputs"), inputs);
        segments.replace(0, item);
    } else if (change == "score") {
        QJsonObject item = segments.at(2).toObject();
        QJsonObject automatic = item.value(QStringLiteral("automatic")).toObject();
        automatic.insert(QStringLiteral("quality_score"), 1.5);
        item.insert(QStringLiteral("automatic"), automatic);
        segments.replace(2, item);
    } else if (change == "source_mismatch") {
        QJsonObject item = segments.at(0).toObject();
        item.insert(QStringLiteral("selected_input_index"), 2);
        segments.replace(0, item);
    } else if (change == "not_ok") {
        payload.insert(QStringLiteral("ok"), false);
    } else if (change == "automatic_segment") {
        QJsonObject item = segments.at(1).toObject();
        QJsonObject automatic = item.value(QStringLiteral("automatic")).toObject();
        automatic.insert(QStringLiteral("segment_start_sample"), 16001);
        item.insert(QStringLiteral("automatic"), automatic);
        segments.replace(1, item);
    }
    alignment.insert(QStringLiteral("inputs"), alignmentInputs);
    selection.insert(QStringLiteral("rows"), segments);
    payload.insert(QStringLiteral("alignment"), alignment);
    payload.insert(QStringLiteral("selection"), selection);

    const QVector<MultiAudioInput> all = inputs();
    MultiAudioSelectionPlan plan;
    plan.analysisKey = QStringLiteral("unchanged");
    QString error;
    QVERIFY(!parseMultiAudioQualityResponse(
        payload, {all.at(0), all.at(2), all.at(3)}, 0, &plan, &error));
    QVERIFY(!error.isEmpty());
    QCOMPARE(plan.analysisKey, QStringLiteral("unchanged"));
}

void MultiAudioSelectionModelTests::overridesAreDistinguishableAndOnlyEditableWhileCurrent()
{
    QVector<MultiAudioInput> all = inputs();
    MultiAudioSelectionModel model;
    model.setPlan(parsedPlan());
    model.setInputContext(all, 0);
    QVERIFY(model.isCurrent());
    QCOMPARE(model.rowCount(), 3);
    QCOMPARE(model.columnCount(), int(MultiAudioSelectionModel::ColumnCount));
    QCOMPARE(model.headerData(MultiAudioSelectionModel::FinalInputColumn,
                              Qt::Horizontal).toString(),
             QStringLiteral("최종 선택"));

    const auto cell = [&model](int row, int column, int role = Qt::DisplayRole) {
        return model.index(row, column).data(role);
    };
    QCOMPARE(cell(0, MultiAudioSelectionModel::SourceColumn).toString(),
             QStringLiteral("자동"));
    QVERIFY(!cell(0, MultiAudioSelectionModel::StartColumn, Qt::BackgroundRole).isValid());
    QCOMPARE(cell(1, MultiAudioSelectionModel::SourceColumn).toString(),
             QStringLiteral("수동"));
    QVERIFY(cell(1, MultiAudioSelectionModel::StartColumn, Qt::BackgroundRole).isValid());
    QCOMPARE(cell(1, MultiAudioSelectionModel::AutomaticInputColumn).toString(),
             QStringLiteral("B"));
    QCOMPARE(cell(1, MultiAudioSelectionModel::FinalInputColumn).toString(),
             QStringLiteral("C"));
    QCOMPARE(cell(1, MultiAudioSelectionModel::FinalScoreColumn).toString(),
             QStringLiteral("⚠ 0.62 (녹음 75%)"));
    QCOMPARE(cell(1, MultiAudioSelectionModel::ReasonColumn).toString(),
             QStringLiteral("최고 품질 외 1"));
    QVERIFY(cell(1, MultiAudioSelectionModel::StartColumn, Qt::ToolTipRole)
                .toString().contains(QStringLiteral("수동 지정: C")));

    QSignalSpy overridesSpy(&model, &MultiAudioSelectionModel::overridesChanged);
    // Both automatically chosen A rows now use B and become manual.
    QCOMPARE(model.setOverride({0, 2}, m_second), 2);
    QCOMPARE(overridesSpy.count(), 1);
    QVERIFY(cell(0, 0, MultiAudioSelectionModel::IsManualRole).toBool());
    QCOMPARE(cell(0, MultiAudioSelectionModel::FinalInputColumn).toString(),
             QStringLiteral("B"));
    QCOMPARE(model.plan().manualSegmentCount(), 3);

    // Choosing the automatic microphone reverts to the automatic plan.
    QCOMPARE(model.setOverride({0}, m_first), 1);
    QVERIFY(!cell(0, 0, MultiAudioSelectionModel::IsManualRole).toBool());
    QCOMPARE(model.setOverride({0}, m_excluded), 0);
    QCOMPARE(model.clearOverrides({2}), 1);
    QCOMPARE(model.plan().manualSegmentCount(), 1);

    // Excluding an analyzed input makes the plan stale: no new overrides, but
    // existing manual choices can still be cleared.
    all[2].enabled = false;
    model.setInputContext(all, 0);
    QVERIFY(!model.isCurrent());
    QCOMPARE(model.setOverride({0}, m_second), 0);
    QCOMPARE(model.clearAllOverrides(), 1);
    QCOMPARE(model.plan().manualSegmentCount(), 0);
}

void MultiAudioSelectionModelTests::manifestCarriesReviewedOffsetsAndOverrides()
{
    const QVector<MultiAudioInput> all = inputs();
    const MultiAudioManifestRequest request =
        buildMultiAudioManifest(all, 0, parsedPlan());

    QCOMPARE(request.rows, QVector<int>({0, 2, 3}));
    QCOMPARE(request.referenceIndex, 0);
    QCOMPARE(request.overrideCount, 1);
    QCOMPARE(request.droppedOverrideCount, 0);

    const QJsonObject manifest = request.manifest;
    QCOMPARE(manifest.value(QStringLiteral("version")).toInt(), 1);
    QCOMPARE(manifest.value(QStringLiteral("reference_index")).toInt(), 0);
    const QJsonArray manifestInputs =
        manifest.value(QStringLiteral("inputs")).toArray();
    QCOMPARE(manifestInputs.size(), 3);
    QCOMPARE(manifestInputs.at(2).toObject().value(QStringLiteral("path")).toString(),
             m_third);
    QCOMPARE(manifestInputs.at(2).toObject().value(QStringLiteral("label")).toString(),
             QStringLiteral("C"));

    const QJsonObject alignment = manifest.value(QStringLiteral("alignment")).toObject();
    QCOMPARE(alignment.value(QStringLiteral("mode")).toString(), QStringLiteral("auto"));
    const QJsonObject offsets =
        alignment.value(QStringLiteral("manual_offsets_ms")).toObject();
    QCOMPARE(offsets.keys(), QStringList({QStringLiteral("2")}));
    QCOMPARE(offsets.value(QStringLiteral("2")).toInteger(), qint64(475));

    const QJsonObject selection = manifest.value(QStringLiteral("selection")).toObject();
    QCOMPARE(selection.value(QStringLiteral("mode")).toString(),
             QStringLiteral("automatic"));
    QCOMPARE(selection.value(QStringLiteral("window_seconds")).toDouble(), 3.0);
    QCOMPARE(selection.value(QStringLiteral("hop_seconds")).toDouble(), 1.0);
    const QJsonArray overrides = selection.value(QStringLiteral("overrides")).toArray();
    QCOMPARE(overrides.size(), 1);
    const QJsonObject override = overrides.at(0).toObject();
    QCOMPARE(override.value(QStringLiteral("start_sample")).toInteger(), qint64(16000));
    QCOMPARE(override.value(QStringLiteral("end_sample")).toInteger(), qint64(40000));
    QCOMPARE(override.value(QStringLiteral("input_index")).toInt(), 2);
    QVERIFY(!QJsonDocument(manifest).toJson().isEmpty());
}

void MultiAudioSelectionModelTests::manifestDropsOverridesThatNoLongerFit()
{
    QVector<MultiAudioInput> all = inputs();
    MultiAudioSelectionPlan plan = parsedPlan();
    plan.segments[0].overrideInputPath = m_second;

    // Another microphone's alignment changed: overrides remain intervals on
    // the unchanged reference timeline and keep their microphones.
    all[2].alignmentOffsetMilliseconds = 1300;
    MultiAudioManifestRequest request = buildMultiAudioManifest(all, 0, plan);
    QCOMPARE(request.overrideCount, 2);
    QCOMPARE(request.droppedOverrideCount, 0);

    // A manual choice whose microphone is excluded cannot be applied.
    all[3].enabled = false;
    request = buildMultiAudioManifest(all, 0, plan);
    QCOMPARE(request.overrideCount, 1);
    QCOMPARE(request.droppedOverrideCount, 1);
    QVERIFY(request.manifest.value(QStringLiteral("alignment")).toObject()
                .value(QStringLiteral("manual_offsets_ms")).toObject().isEmpty());

    // A different reference recording defines another timeline.
    all = inputs();
    request = buildMultiAudioManifest(all, 2, plan);
    QCOMPARE(request.overrideCount, 0);
    QCOMPARE(request.droppedOverrideCount, 2);
    QCOMPARE(request.referenceIndex, 1);
}

void MultiAudioSelectionModelTests::analysisKeyTracksWhatTheAnalysisDependsOn()
{
    const QVector<MultiAudioInput> all = inputs();
    const QString key = MultiAudioInputModel::selectionAnalysisKey(all, 0);
    const QString timeline = MultiAudioInputModel::selectionTimelineKey(all, 0);
    QVERIFY(!key.isEmpty());
    QVERIFY(MultiAudioInputModel::selectionAnalysisKey(all, -1).isEmpty());

    QVector<MultiAudioInput> changed = all;
    changed[2].alias = QStringLiteral("Renamed");
    changed[1].alignmentOffsetMilliseconds = 99;
    QCOMPARE(MultiAudioInputModel::selectionAnalysisKey(changed, 0), key);

    changed = all;
    changed[3].alignmentOffsetMilliseconds = 476;
    QVERIFY(MultiAudioInputModel::selectionAnalysisKey(changed, 0) != key);
    QCOMPARE(MultiAudioInputModel::selectionTimelineKey(changed, 0), timeline);

    changed = all;
    changed[2].driftPpm = -42.0;
    QVERIFY(MultiAudioInputModel::selectionAnalysisKey(changed, 0) != key);

    changed = all;
    changed[1].enabled = true;
    QVERIFY(MultiAudioInputModel::selectionAnalysisKey(changed, 0) != key);

    changed = all;
    changed[0].durationMilliseconds = 9001;
    QVERIFY(MultiAudioInputModel::selectionTimelineKey(changed, 0) != timeline);
    QVERIFY(MultiAudioInputModel::selectionAnalysisKey(all, 2) != key);
}

void MultiAudioSelectionModelTests::formatsPositionsAndMissingAudio()
{
    QCOMPARE(MultiAudioSelectionModel::formatPosition(0, 16000),
             QStringLiteral("00:00.0"));
    QCOMPARE(MultiAudioSelectionModel::formatPosition(980000, 16000),
             QStringLiteral("01:01.3"));
    QCOMPARE(MultiAudioSelectionModel::formatPosition(qint64(3600) * 16000, 16000),
             QStringLiteral("1:00:00.0"));
    QCOMPARE(MultiAudioSelectionModel::formatPosition(10, 0),
             QStringLiteral("—"));
    QCOMPARE(MultiAudioSelectionModel::reasonLabel(QStringLiteral("single_input_speech")),
             QStringLiteral("단독 발화"));
    QCOMPARE(MultiAudioSelectionModel::reasonLabel(QStringLiteral("future_code")),
             QStringLiteral("future_code"));

    MultiAudioSelectionModel model;
    model.setPlan(parsedPlan());
    model.setInputContext(inputs(), 0);
    QCOMPARE(model.setOverride({0}, m_third), 1);
    QCOMPARE(model.index(0, MultiAudioSelectionModel::FinalScoreColumn).data().toString(),
             QStringLiteral("⚠ 녹음 없음"));
    QCOMPARE(model.index(2, MultiAudioSelectionModel::FinalScoreColumn).data().toString(),
             QLocale().toString(0.83, 'f', 2));
    QVERIFY(model.index(0, 0).data(Qt::ToolTipRole).toString().contains(
        QStringLiteral("무음으로 합성")));
}

QTEST_GUILESS_MAIN(MultiAudioSelectionModelTests)
#include "tst_multiaudioselectionmodel.moc"
