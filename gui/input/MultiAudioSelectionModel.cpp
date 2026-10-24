#include "MultiAudioSelectionModel.h"

#include <QBrush>
#include <QColor>
#include <QFileInfo>
#include <QFont>
#include <QJsonArray>
#include <QLocale>

#include <algorithm>
#include <cmath>
#include <limits>

namespace {
constexpr double kSelectionWindowSeconds = 3.0;
constexpr double kSelectionHopSeconds = 1.0;
// Largest integer that a JSON number (an IEEE double) carries exactly.
constexpr qint64 kMaxExactJsonInteger = Q_INT64_C(9007199254740992);

// Plan paths are copied from the input model, which stores normalized
// canonical paths, so a textual comparison is exact and painting the table
// never touches the file system.
bool samePath(const QString &left, const QString &right)
{
#ifdef Q_OS_WIN
    return left.compare(right, Qt::CaseInsensitive) == 0;
#else
    return left == right;
#endif
}

bool fail(QString *error, const QString &message)
{
    if (error) {
        *error = message;
    }
    return false;
}

bool jsonInteger(const QJsonValue &value, qint64 minimum, qint64 maximum,
                 qint64 *result)
{
    if (!value.isDouble()) {
        return false;
    }
    const double number = value.toDouble();
    if (!std::isfinite(number) || std::floor(number) != number
        || std::fabs(number) > static_cast<double>(kMaxExactJsonInteger)) {
        return false;
    }
    const auto integer = static_cast<qint64>(number);
    if (integer < minimum || integer > maximum) {
        return false;
    }
    *result = integer;
    return true;
}

bool jsonUnitInterval(const QJsonValue &value, double *result)
{
    if (!value.isDouble()) {
        return false;
    }
    const double number = value.toDouble();
    if (!std::isfinite(number) || number < 0.0 || number > 1.0) {
        return false;
    }
    *result = number;
    return true;
}

bool jsonOptionalScore(const QJsonValue &value, bool *hasScore, double *score)
{
    if (value.isNull()) {
        *hasScore = false;
        *score = 0.0;
        return true;
    }
    if (!jsonUnitInterval(value, score)) {
        return false;
    }
    *hasScore = true;
    return true;
}

bool jsonReasonCodes(const QJsonValue &value, QStringList *codes)
{
    if (!value.isArray()) {
        return false;
    }
    QStringList result;
    for (const QJsonValue &item : value.toArray()) {
        const QString code = item.toString().trimmed();
        if (!item.isString() || code.isEmpty()) {
            return false;
        }
        result.append(code);
    }
    if (result.isEmpty()) {
        return false;
    }
    *codes = result;
    return true;
}

QString formatScore(double score)
{
    return QLocale().toString(score, 'f', 2);
}

QString formatConfidence(double confidence)
{
    return QLocale().toString(confidence * 100.0, 'f', 1) + QStringLiteral(" %");
}

int recordedPercent(double validRatio)
{
    // Rounded down so a partly missing recording never reads as 100 %.
    return static_cast<int>(std::floor(validRatio * 100.0));
}

QString warningText(const QString &code, const QString &message,
                    const QString &range)
{
    QString text;
    if (code == QStringLiteral("no_valid_channel")) {
        text = MultiAudioSelectionModel::tr(
            "어떤 마이크에도 녹음이 없습니다. 시간축을 줄이지 않도록 기존 채널을 유지합니다.");
    } else if (code == QStringLiteral("all_channels_unscoreable")) {
        text = MultiAudioSelectionModel::tr(
            "모든 마이크의 품질 점수를 계산할 수 없어 녹음이 가장 많은 마이크를 사용합니다.");
    } else if (code == QStringLiteral("all_channels_low_quality")) {
        text = MultiAudioSelectionModel::tr(
            "모든 마이크의 품질이 기준보다 낮아 가장 나은 원본을 사용합니다.");
    } else if (code == QStringLiteral("no_quality_windows")) {
        text = MultiAudioSelectionModel::tr(
            "품질 분석 창이 없어 녹음이 가장 많은 마이크를 사용합니다.");
    } else {
        text = message.isEmpty() ? code : message;
    }
    return range.isEmpty() ? text
                           : MultiAudioSelectionModel::tr("%1: %2").arg(range, text);
}
} // namespace

bool MultiAudioSelectionSegment::isManual() const noexcept
{
    return !overrideInputPath.isEmpty();
}

QString MultiAudioSelectionSegment::finalInputPath() const
{
    return isManual() ? overrideInputPath : automaticInputPath;
}

const MultiAudioSegmentEvidence *MultiAudioSelectionSegment::evidence(
    const QString &path) const
{
    for (const MultiAudioSegmentEvidence &item : inputs) {
        if (samePath(item.path, path)) {
            return &item;
        }
    }
    return nullptr;
}

bool MultiAudioSelectionPlan::isEmpty() const noexcept
{
    return segments.isEmpty();
}

bool MultiAudioSelectionPlan::isCurrentFor(const QVector<MultiAudioInput> &inputs,
                                           int referenceRow) const
{
    return !isEmpty() && !analysisKey.isEmpty()
        && analysisKey
            == MultiAudioInputModel::selectionAnalysisKey(inputs, referenceRow);
}

int MultiAudioSelectionPlan::manualSegmentCount() const
{
    return static_cast<int>(std::count_if(
        segments.cbegin(), segments.cend(),
        [](const MultiAudioSelectionSegment &segment) {
            return segment.isManual();
        }));
}

int MultiAudioSelectionPlan::switchCount() const
{
    int count = 0;
    for (int row = 1; row < segments.size(); ++row) {
        if (!samePath(segments.at(row - 1).finalInputPath(),
                      segments.at(row).finalInputPath())) {
            ++count;
        }
    }
    return count;
}

int MultiAudioSelectionPlan::incompleteFinalSegmentCount() const
{
    return static_cast<int>(std::count_if(
        segments.cbegin(), segments.cend(),
        [](const MultiAudioSelectionSegment &segment) {
            const MultiAudioSegmentEvidence *evidence =
                segment.evidence(segment.finalInputPath());
            return !evidence || evidence->validRatio < 1.0;
        }));
}

MultiAudioManifestRequest buildMultiAudioManifest(
    const QVector<MultiAudioInput> &inputs,
    int referenceRow,
    const MultiAudioSelectionPlan &selection)
{
    MultiAudioManifestRequest request;
    QJsonArray manifestInputs;
    QJsonObject manualOffsets;
    for (int row = 0; row < inputs.size(); ++row) {
        const MultiAudioInput &input = inputs.at(row);
        if (!input.enabled) {
            continue;
        }
        const int inputIndex = request.rows.size();
        if (row == referenceRow) {
            request.referenceIndex = inputIndex;
        } else if (input.alignmentStatus == MultiAudioInput::AlignmentStatus::Manual
                   && input.hasAlignmentOffset && input.alignmentOffsetIsManual) {
            manualOffsets.insert(QString::number(inputIndex),
                                 QJsonValue(input.alignmentOffsetMilliseconds));
        }
        request.rows.append(row);
        QJsonObject manifestInput;
        manifestInput.insert(QStringLiteral("path"), input.path);
        manifestInput.insert(QStringLiteral("label"), input.alias);
        manifestInputs.append(manifestInput);
    }

    // Manual selections are intervals on the reference timeline, so they stay
    // meaningful across alignment changes of other inputs, but not when the
    // reference recording itself changes or their microphone is no longer used.
    QJsonArray overrides;
    const bool sameTimeline = !selection.isEmpty()
        && selection.timelineKey
            == MultiAudioInputModel::selectionTimelineKey(inputs, referenceRow);
    for (const MultiAudioSelectionSegment &segment : selection.segments) {
        if (!segment.isManual()) {
            continue;
        }
        int inputIndex = -1;
        for (int index = 0; sameTimeline && index < request.rows.size(); ++index) {
            if (samePath(inputs.at(request.rows.at(index)).path,
                         segment.overrideInputPath)) {
                inputIndex = index;
                break;
            }
        }
        if (inputIndex < 0) {
            ++request.droppedOverrideCount;
            continue;
        }
        QJsonObject overrideObject;
        overrideObject.insert(QStringLiteral("start_sample"),
                              QJsonValue(segment.startSample));
        overrideObject.insert(QStringLiteral("end_sample"),
                              QJsonValue(segment.endSample));
        overrideObject.insert(QStringLiteral("input_index"), inputIndex);
        overrides.append(overrideObject);
    }
    request.overrideCount = overrides.size();

    QJsonObject alignment;
    alignment.insert(QStringLiteral("mode"), QStringLiteral("auto"));
    alignment.insert(QStringLiteral("manual_offsets_ms"), manualOffsets);
    QJsonObject selectionSettings;
    selectionSettings.insert(QStringLiteral("mode"), QStringLiteral("automatic"));
    selectionSettings.insert(QStringLiteral("window_seconds"), kSelectionWindowSeconds);
    selectionSettings.insert(QStringLiteral("hop_seconds"), kSelectionHopSeconds);
    selectionSettings.insert(QStringLiteral("overrides"), overrides);
    request.manifest.insert(QStringLiteral("version"), 1);
    request.manifest.insert(QStringLiteral("inputs"), manifestInputs);
    request.manifest.insert(QStringLiteral("reference_index"), request.referenceIndex);
    request.manifest.insert(QStringLiteral("alignment"), alignment);
    request.manifest.insert(QStringLiteral("selection"), selectionSettings);
    return request;
}

bool parseMultiAudioQualityResponse(const QJsonObject &payload,
                                    const QVector<MultiAudioInput> &manifestInputs,
                                    int referenceIndex,
                                    MultiAudioSelectionPlan *plan,
                                    QString *error)
{
    const qint64 inputCount = manifestInputs.size();
    qint64 version = -1;
    if (!jsonInteger(payload.value(QStringLiteral("version")), 1, 1, &version)
        || payload.value(QStringLiteral("ok")) != QJsonValue(true)) {
        return fail(error,
                    MultiAudioSelectionModel::tr(
                        "응답 버전 또는 성공 여부가 올바르지 않습니다."));
    }
    if (inputCount < 2 || referenceIndex < 0 || referenceIndex >= inputCount) {
        return fail(error,
                    MultiAudioSelectionModel::tr(
                        "분석 요청의 입력 구성이 올바르지 않습니다."));
    }

    // The analysis recomputes the automatic alignment. Its result must be the
    // one the user reviewed, otherwise the table would describe a different
    // timeline than the one shown in the input list.
    const QJsonObject alignment = payload.value(QStringLiteral("alignment")).toObject();
    const QJsonArray alignmentInputs =
        alignment.value(QStringLiteral("inputs")).toArray();
    qint64 alignmentReference = -1;
    if (!jsonInteger(alignment.value(QStringLiteral("reference_index")),
                     0, inputCount - 1, &alignmentReference)
        || alignmentReference != referenceIndex
        || alignmentInputs.size() != inputCount) {
        return fail(error,
                    MultiAudioSelectionModel::tr(
                        "적용된 시간 정렬 정보의 형식이 올바르지 않습니다."));
    }
    QVector<bool> alignmentSeen(manifestInputs.size(), false);
    for (const QJsonValue &value : alignmentInputs) {
        const QJsonObject item = value.toObject();
        qint64 inputIndex = -1;
        const QJsonValue offsetValue = item.value(QStringLiteral("offset_ms"));
        if (!value.isObject()
            || !jsonInteger(item.value(QStringLiteral("input_index")),
                            0, inputCount - 1, &inputIndex)
            || alignmentSeen.at(inputIndex) || !offsetValue.isDouble()
            || !std::isfinite(offsetValue.toDouble())
            || std::fabs(offsetValue.toDouble())
                > static_cast<double>(kMaxExactJsonInteger)) {
            return fail(error,
                        MultiAudioSelectionModel::tr(
                            "적용된 시간 정렬 정보의 형식이 올바르지 않습니다."));
        }
        alignmentSeen[inputIndex] = true;
        const QString source = item.value(QStringLiteral("source")).toString();
        const qint64 offset = qRound64(offsetValue.toDouble());
        const MultiAudioInput &input = manifestInputs.at(inputIndex);
        bool consistent = false;
        if (inputIndex == referenceIndex) {
            consistent = source == QStringLiteral("reference") && offset == 0;
        } else if (input.alignmentStatus == MultiAudioInput::AlignmentStatus::Manual) {
            consistent = source == QStringLiteral("manual")
                && offset == input.alignmentOffsetMilliseconds;
        } else {
            consistent = source == QStringLiteral("automatic")
                && input.alignmentStatus == MultiAudioInput::AlignmentStatus::Aligned
                && input.hasAlignmentOffset
                && offset == input.alignmentOffsetMilliseconds;
        }
        if (!consistent) {
            return fail(error,
                        MultiAudioSelectionModel::tr(
                            "%1의 시간 정렬 결과가 검토한 값과 다릅니다. 파일이 바뀌었을 수 있으니 시간 정렬 분석을 다시 실행하세요.")
                            .arg(input.alias));
        }
    }

    const QJsonObject selection = payload.value(QStringLiteral("selection")).toObject();
    qint64 sampleRate = 0;
    qint64 timelineSampleCount = 0;
    const QJsonArray rows = selection.value(QStringLiteral("rows")).toArray();
    if (!jsonInteger(selection.value(QStringLiteral("sample_rate")), 1,
                     std::numeric_limits<int>::max(), &sampleRate)
        || !jsonInteger(selection.value(QStringLiteral("timeline_sample_count")),
                        1, kMaxExactJsonInteger, &timelineSampleCount)
        || rows.isEmpty()) {
        return fail(error,
                    MultiAudioSelectionModel::tr(
                        "채널 선택 시간축 정보가 올바르지 않습니다."));
    }

    MultiAudioSelectionPlan result;
    result.sampleRate = static_cast<int>(sampleRate);
    result.timelineSampleCount = timelineSampleCount;
    for (const MultiAudioInput &input : manifestInputs) {
        result.inputPaths.append(input.path);
    }

    qint64 expectedStart = 0;
    for (const QJsonValue &value : rows) {
        const QJsonObject item = value.toObject();
        const QJsonObject automatic = item.value(QStringLiteral("automatic")).toObject();
        const QJsonArray evidenceValues = item.value(QStringLiteral("inputs")).toArray();
        const QString source = item.value(QStringLiteral("selection_source")).toString();
        qint64 startSample = -1;
        qint64 endSample = -1;
        qint64 selectedIndex = -1;
        qint64 automaticIndex = -1;
        qint64 evidenceAutomaticIndex = -1;
        MultiAudioSelectionSegment segment;
        if (!value.isObject()
            || !jsonInteger(item.value(QStringLiteral("start_sample")),
                            0, timelineSampleCount, &startSample)
            || !jsonInteger(item.value(QStringLiteral("end_sample")),
                            0, timelineSampleCount, &endSample)
            || startSample != expectedStart || endSample <= startSample) {
            return fail(error,
                        MultiAudioSelectionModel::tr(
                            "채널 선택 구간이 기준 시간축을 빈틈없이 덮지 않습니다."));
        }
        if (!jsonInteger(item.value(QStringLiteral("selected_input_index")),
                         0, inputCount - 1, &selectedIndex)
            || !jsonInteger(item.value(QStringLiteral("automatic_input_index")),
                            0, inputCount - 1, &automaticIndex)
            || (source != QStringLiteral("automatic")
                && source != QStringLiteral("manual_override"))
            || (source == QStringLiteral("automatic") && selectedIndex != automaticIndex)
            || !automatic.contains(QStringLiteral("quality_score"))
            || !jsonInteger(automatic.value(QStringLiteral("input_index")),
                            0, inputCount - 1, &evidenceAutomaticIndex)
            || evidenceAutomaticIndex != automaticIndex
            || !jsonOptionalScore(automatic.value(QStringLiteral("quality_score")),
                                  &segment.hasAutomaticScore,
                                  &segment.automaticScore)
            || !jsonUnitInterval(automatic.value(QStringLiteral("confidence")),
                                 &segment.automaticConfidence)
            || !jsonReasonCodes(automatic.value(QStringLiteral("reason_codes")),
                                &segment.automaticReasonCodes)
            || !jsonInteger(automatic.value(QStringLiteral("segment_start_sample")),
                            0, startSample, &segment.automaticSegmentStartSample)
            || !jsonInteger(automatic.value(QStringLiteral("segment_end_sample")),
                            endSample, timelineSampleCount,
                            &segment.automaticSegmentEndSample)
            || evidenceValues.size() != inputCount) {
            return fail(error,
                        MultiAudioSelectionModel::tr(
                            "채널 선택 구간의 근거 정보가 올바르지 않습니다."));
        }

        segment.startSample = startSample;
        segment.endSample = endSample;
        segment.automaticInputPath = manifestInputs.at(automaticIndex).path;
        if (source == QStringLiteral("manual_override")) {
            segment.overrideInputPath = manifestInputs.at(selectedIndex).path;
        }
        segment.inputs.resize(manifestInputs.size());
        QVector<bool> evidenceSeen(manifestInputs.size(), false);
        for (const QJsonValue &evidenceValue : evidenceValues) {
            const QJsonObject evidence = evidenceValue.toObject();
            qint64 inputIndex = -1;
            MultiAudioSegmentEvidence parsed;
            if (!evidenceValue.isObject()
                || !jsonInteger(evidence.value(QStringLiteral("input_index")),
                                0, inputCount - 1, &inputIndex)
                || evidenceSeen.at(inputIndex)
                || !evidence.contains(QStringLiteral("quality_score"))
                || !jsonOptionalScore(evidence.value(QStringLiteral("quality_score")),
                                      &parsed.hasScore, &parsed.score)
                || !jsonUnitInterval(evidence.value(QStringLiteral("valid_ratio")),
                                     &parsed.validRatio)) {
                return fail(error,
                            MultiAudioSelectionModel::tr(
                                "마이크별 근거 정보가 올바르지 않습니다."));
            }
            evidenceSeen[inputIndex] = true;
            parsed.path = manifestInputs.at(inputIndex).path;
            segment.inputs[inputIndex] = parsed;
        }
        result.segments.append(segment);
        expectedStart = endSample;
    }
    if (expectedStart != timelineSampleCount) {
        return fail(error,
                    MultiAudioSelectionModel::tr(
                        "채널 선택 구간이 기준 시간축을 빈틈없이 덮지 않습니다."));
    }

    // Preparation warnings and plan warnings. Missing audio under a manual
    // selection is derived from the evidence instead, so it follows later
    // local edits of the overrides.
    for (const QJsonValue &value : payload.value(QStringLiteral("warnings")).toArray()) {
        const QJsonObject warning = value.toObject();
        result.warnings.append(warningText(
            warning.value(QStringLiteral("code")).toString(),
            warning.value(QStringLiteral("message")).toString().trimmed(),
            QString()));
    }
    for (const QJsonValue &value : selection.value(QStringLiteral("warnings")).toArray()) {
        const QJsonObject warning = value.toObject();
        const QString code = warning.value(QStringLiteral("code")).toString();
        if (code == QStringLiteral("manual_override_unavailable")) {
            continue;
        }
        qint64 start = -1;
        qint64 end = -1;
        QString range;
        if (jsonInteger(warning.value(QStringLiteral("start_sample")),
                        0, timelineSampleCount, &start)
            && jsonInteger(warning.value(QStringLiteral("end_sample")),
                           0, timelineSampleCount, &end)) {
            range = MultiAudioSelectionModel::tr("%1–%2").arg(
                MultiAudioSelectionModel::formatPosition(start, result.sampleRate),
                MultiAudioSelectionModel::formatPosition(end, result.sampleRate));
        }
        result.warnings.append(warningText(
            code, warning.value(QStringLiteral("message")).toString().trimmed(),
            range));
    }

    if (plan) {
        *plan = result;
    }
    if (error) {
        error->clear();
    }
    return true;
}

MultiAudioSelectionModel::MultiAudioSelectionModel(QObject *parent)
    : QAbstractTableModel(parent)
{
    qRegisterMetaType<MultiAudioSelectionPlan>();
}

int MultiAudioSelectionModel::rowCount(const QModelIndex &parent) const
{
    return parent.isValid() ? 0 : m_plan.segments.size();
}

int MultiAudioSelectionModel::columnCount(const QModelIndex &parent) const
{
    return parent.isValid() ? 0 : ColumnCount;
}

QVariant MultiAudioSelectionModel::data(const QModelIndex &index, int role) const
{
    if (!index.isValid() || index.column() < 0 || index.column() >= ColumnCount
        || index.row() < 0 || index.row() >= m_plan.segments.size()) {
        return {};
    }

    const MultiAudioSelectionSegment &segment = m_plan.segments.at(index.row());
    switch (role) {
    case StartSampleRole:
        return segment.startSample;
    case EndSampleRole:
        return segment.endSample;
    case AutomaticInputPathRole:
        return segment.automaticInputPath;
    case OverrideInputPathRole:
        return segment.overrideInputPath;
    case FinalInputPathRole:
        return segment.finalInputPath();
    case IsManualRole:
        return segment.isManual();
    case Qt::ToolTipRole:
        return rowToolTip(segment);
    case Qt::TextAlignmentRole:
        if (index.column() == ReasonColumn) {
            return {};
        }
        return static_cast<int>(Qt::AlignCenter);
    case Qt::BackgroundRole:
        // Manual rows stay distinguishable even without reading a column.
        // Translucent amber keeps the palette's text color readable in both
        // light and dark themes.
        return segment.isManual() ? QVariant(QBrush(QColor(255, 193, 7, 72)))
                                  : QVariant();
    case Qt::FontRole:
        if (segment.isManual()
            && (index.column() == FinalInputColumn
                || index.column() == SourceColumn)) {
            QFont font;
            font.setBold(true);
            return font;
        }
        return {};
    case Qt::DisplayRole:
        switch (index.column()) {
        case StartColumn:
            return formatPosition(segment.startSample, m_plan.sampleRate);
        case EndColumn:
            return formatPosition(segment.endSample, m_plan.sampleRate);
        case AutomaticInputColumn:
            return aliasForPath(segment.automaticInputPath);
        case AutomaticScoreColumn:
            return segment.hasAutomaticScore ? formatScore(segment.automaticScore)
                                             : QStringLiteral("—");
        case ConfidenceColumn:
            return formatConfidence(segment.automaticConfidence);
        case ReasonColumn: {
            if (segment.automaticReasonCodes.isEmpty()) {
                return QStringLiteral("—");
            }
            QString label = reasonLabel(segment.automaticReasonCodes.constFirst());
            if (segment.automaticReasonCodes.size() > 1) {
                label += tr(" 외 %1").arg(segment.automaticReasonCodes.size() - 1);
            }
            return label;
        }
        case FinalInputColumn:
            return aliasForPath(segment.finalInputPath());
        case FinalScoreColumn:
            return finalScoreText(segment);
        case SourceColumn:
            return segment.isManual() ? tr("수동") : tr("자동");
        default:
            return {};
        }
    default:
        return {};
    }
}

QVariant MultiAudioSelectionModel::headerData(int section,
                                              Qt::Orientation orientation,
                                              int role) const
{
    if (role != Qt::DisplayRole) {
        return QAbstractTableModel::headerData(section, orientation, role);
    }
    if (orientation == Qt::Vertical) {
        return section + 1;
    }

    switch (section) {
    case StartColumn:
        return tr("시작");
    case EndColumn:
        return tr("끝");
    case AutomaticInputColumn:
        return tr("자동 선택");
    case AutomaticScoreColumn:
        return tr("자동 점수");
    case ConfidenceColumn:
        return tr("자동 신뢰도");
    case ReasonColumn:
        return tr("선택 사유");
    case FinalInputColumn:
        return tr("최종 선택");
    case FinalScoreColumn:
        return tr("최종 점수");
    case SourceColumn:
        return tr("구분");
    default:
        return {};
    }
}

QHash<int, QByteArray> MultiAudioSelectionModel::roleNames() const
{
    QHash<int, QByteArray> roles = QAbstractTableModel::roleNames();
    roles.insert(StartSampleRole, "startSample");
    roles.insert(EndSampleRole, "endSample");
    roles.insert(AutomaticInputPathRole, "automaticInputPath");
    roles.insert(OverrideInputPathRole, "overrideInputPath");
    roles.insert(FinalInputPathRole, "finalInputPath");
    roles.insert(IsManualRole, "manual");
    return roles;
}

MultiAudioSelectionPlan MultiAudioSelectionModel::plan() const
{
    return m_plan;
}

void MultiAudioSelectionModel::setPlan(const MultiAudioSelectionPlan &plan)
{
    beginResetModel();
    m_plan = plan;
    endResetModel();
}

void MultiAudioSelectionModel::setInputContext(const QVector<MultiAudioInput> &inputs,
                                               int referenceRow)
{
    m_inputs = inputs;
    m_referenceRow = referenceRow;
    if (!m_plan.segments.isEmpty()) {
        emit dataChanged(index(0, 0),
                         index(m_plan.segments.size() - 1, ColumnCount - 1),
                         {Qt::DisplayRole, Qt::ToolTipRole});
    }
}

bool MultiAudioSelectionModel::isCurrent() const
{
    return m_plan.isCurrentFor(m_inputs, m_referenceRow);
}

MultiAudioSelectionSegment MultiAudioSelectionModel::segmentAt(int row) const
{
    return row >= 0 && row < m_plan.segments.size() ? m_plan.segments.at(row)
                                                     : MultiAudioSelectionSegment{};
}

int MultiAudioSelectionModel::setOverride(const QVector<int> &rows,
                                          const QString &inputPath)
{
    QString planPath;
    for (const QString &path : m_plan.inputPaths) {
        if (samePath(path, inputPath)) {
            planPath = path;
            break;
        }
    }
    if (!isCurrent() || planPath.isEmpty()) {
        return 0;
    }

    int changed = 0;
    int firstRow = m_plan.segments.size();
    int lastRow = -1;
    for (int row : rows) {
        if (row < 0 || row >= m_plan.segments.size()) {
            continue;
        }
        MultiAudioSelectionSegment &segment = m_plan.segments[row];
        const QString overridePath =
            samePath(planPath, segment.automaticInputPath)
            ? QString()
            : planPath;
        if (segment.overrideInputPath == overridePath) {
            continue;
        }
        segment.overrideInputPath = overridePath;
        ++changed;
        firstRow = qMin(firstRow, row);
        lastRow = qMax(lastRow, row);
    }
    if (changed > 0) {
        emit dataChanged(index(firstRow, 0), index(lastRow, ColumnCount - 1));
        emit overridesChanged();
    }
    return changed;
}

int MultiAudioSelectionModel::clearOverrides(const QVector<int> &rows)
{
    int changed = 0;
    int firstRow = m_plan.segments.size();
    int lastRow = -1;
    for (int row : rows) {
        if (row < 0 || row >= m_plan.segments.size()
            || !m_plan.segments.at(row).isManual()) {
            continue;
        }
        m_plan.segments[row].overrideInputPath.clear();
        ++changed;
        firstRow = qMin(firstRow, row);
        lastRow = qMax(lastRow, row);
    }
    if (changed > 0) {
        emit dataChanged(index(firstRow, 0), index(lastRow, ColumnCount - 1));
        emit overridesChanged();
    }
    return changed;
}

int MultiAudioSelectionModel::clearAllOverrides()
{
    QVector<int> rows;
    for (int row = 0; row < m_plan.segments.size(); ++row) {
        rows.append(row);
    }
    return clearOverrides(rows);
}

QString MultiAudioSelectionModel::aliasForPath(const QString &path) const
{
    if (path.isEmpty()) {
        return QStringLiteral("—");
    }
    for (const MultiAudioInput &input : m_inputs) {
        if (samePath(input.path, path)
            && !input.alias.trimmed().isEmpty()) {
            return input.alias;
        }
    }
    return QFileInfo(path).fileName();
}

QString MultiAudioSelectionModel::reasonLabel(const QString &code)
{
    if (code == QStringLiteral("best_quality")) {
        return tr("최고 품질");
    }
    if (code == QStringLiteral("better_quality")) {
        return tr("품질 우위 전환");
    }
    if (code == QStringLiteral("hysteresis_hold")) {
        return tr("히스테리시스 유지");
    }
    if (code == QStringLiteral("minimum_hold")) {
        return tr("최소 유지 시간");
    }
    if (code == QStringLiteral("single_input_speech")) {
        return tr("단독 발화");
    }
    if (code == QStringLiteral("speech_preservation")) {
        return tr("발화 보존");
    }
    if (code == QStringLiteral("current_input_unavailable")) {
        return tr("이전 채널 녹음 없음");
    }
    if (code == QStringLiteral("current_input_unscoreable")) {
        return tr("이전 채널 점수 없음");
    }
    if (code == QStringLiteral("fallback_most_valid")) {
        return tr("유효 녹음 우선");
    }
    if (code == QStringLiteral("manual_override")) {
        return tr("수동 지정");
    }
    return code;
}

QString MultiAudioSelectionModel::reasonDescription(const QString &code)
{
    if (code == QStringLiteral("best_quality")) {
        return tr("이 마이크의 품질 점수가 가장 높습니다.");
    }
    if (code == QStringLiteral("better_quality")) {
        return tr("최소 유지 시간이 지난 뒤 다른 마이크보다 히스테리시스 기준 이상 점수가 높아 전환했습니다.");
    }
    if (code == QStringLiteral("hysteresis_hold")) {
        return tr("다른 마이크의 점수가 근소하게 높아 불필요한 전환을 막으려고 이전 마이크를 유지했습니다.");
    }
    if (code == QStringLiteral("minimum_hold")) {
        return tr("최소 유지 시간이 지나기 전까지 이전 마이크를 유지했습니다.");
    }
    if (code == QStringLiteral("single_input_speech")) {
        return tr("이 마이크에서만 발화가 감지되어 안정화 규칙보다 우선 선택했습니다.");
    }
    if (code == QStringLiteral("speech_preservation")) {
        return tr("이전 마이크에 없는 발화를 보존하려고 전환했습니다.");
    }
    if (code == QStringLiteral("current_input_unavailable")) {
        return tr("이전에 선택한 마이크에 이 구간 녹음이 없습니다.");
    }
    if (code == QStringLiteral("current_input_unscoreable")) {
        return tr("이전에 선택한 마이크의 품질 점수를 이 구간에서 계산할 수 없습니다.");
    }
    if (code == QStringLiteral("fallback_most_valid")) {
        return tr("품질 점수를 계산할 수 있는 마이크가 없어 녹음이 가장 많은 마이크를 사용합니다.");
    }
    if (code == QStringLiteral("manual_override")) {
        return tr("사용자가 이 구간의 마이크를 직접 지정했습니다.");
    }
    return code;
}

QString MultiAudioSelectionModel::formatPosition(qint64 sample, int sampleRate)
{
    if (sampleRate <= 0 || sample < 0) {
        return QStringLiteral("—");
    }
    const qint64 tenths = (sample * 10 + sampleRate / 2) / sampleRate;
    const qint64 totalSeconds = tenths / 10;
    const qint64 hours = totalSeconds / 3600;
    const qint64 minutes = totalSeconds / 60 % 60;
    const QString seconds = QStringLiteral("%1.%2")
                                .arg(totalSeconds % 60, 2, 10, QLatin1Char('0'))
                                .arg(tenths % 10);
    if (hours > 0) {
        return QStringLiteral("%1:%2:%3")
            .arg(hours)
            .arg(minutes, 2, 10, QLatin1Char('0'))
            .arg(seconds);
    }
    return QStringLiteral("%1:%2")
        .arg(minutes, 2, 10, QLatin1Char('0'))
        .arg(seconds);
}

QString MultiAudioSelectionModel::rowToolTip(
    const MultiAudioSelectionSegment &segment) const
{
    const double seconds = m_plan.sampleRate > 0
        ? static_cast<double>(segment.endSample - segment.startSample)
            / m_plan.sampleRate
        : 0.0;
    QStringList lines;
    lines.append(tr("구간 %1–%2 (%3초)")
                     .arg(formatPosition(segment.startSample, m_plan.sampleRate),
                          formatPosition(segment.endSample, m_plan.sampleRate),
                          QLocale().toString(seconds, 'f', 1)));
    lines.append(tr("자동 선택: %1 (자동 선택 구간 %2–%3 기준 사유·신뢰도)")
                     .arg(aliasForPath(segment.automaticInputPath),
                          formatPosition(segment.automaticSegmentStartSample,
                                         m_plan.sampleRate),
                          formatPosition(segment.automaticSegmentEndSample,
                                         m_plan.sampleRate)));
    for (const QString &code : segment.automaticReasonCodes) {
        lines.append(tr(" · %1: %2").arg(reasonLabel(code), reasonDescription(code)));
    }
    if (segment.isManual()) {
        lines.append(tr("수동 지정: %1 (자동 선택 대신 합성·전사에 사용)")
                         .arg(aliasForPath(segment.overrideInputPath)));
    }
    lines.append(tr("마이크별 근거:"));
    for (const MultiAudioSegmentEvidence &evidence : segment.inputs) {
        lines.append(tr(" · %1: 점수 %2 · 녹음 %3%")
                         .arg(aliasForPath(evidence.path),
                              evidence.hasScore ? formatScore(evidence.score)
                                                : QStringLiteral("—"),
                              QString::number(recordedPercent(evidence.validRatio))));
    }
    const MultiAudioSegmentEvidence *finalEvidence =
        segment.evidence(segment.finalInputPath());
    if (!finalEvidence || finalEvidence->validRatio < 1.0) {
        lines.append(tr("최종 선택 마이크에 녹음이 없는 부분은 무음으로 합성됩니다."));
    }
    return lines.join(QLatin1Char('\n'));
}

QString MultiAudioSelectionModel::finalScoreText(
    const MultiAudioSelectionSegment &segment) const
{
    // A text marker instead of a color stays legible in every theme.
    const MultiAudioSegmentEvidence *evidence =
        segment.evidence(segment.finalInputPath());
    if (!evidence || evidence->validRatio <= 0.0) {
        return tr("⚠ 녹음 없음");
    }
    const QString score = evidence->hasScore ? formatScore(evidence->score)
                                             : QStringLiteral("—");
    if (evidence->validRatio < 1.0) {
        return tr("⚠ %1 (녹음 %2%)")
            .arg(score, QString::number(recordedPercent(evidence->validRatio)));
    }
    return score;
}
