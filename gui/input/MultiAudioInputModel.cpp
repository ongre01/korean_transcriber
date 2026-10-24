#include "MultiAudioInputModel.h"

#include <QDir>
#include <QFileInfo>
#include <QLocale>
#include <QSet>

#include <cmath>

namespace {
bool isValidRow(const QVector<MultiAudioInput> &inputs, int row)
{
    return row >= 0 && row < inputs.size();
}
} // namespace

MultiAudioInputModel::MultiAudioInputModel(QObject *parent)
    : QAbstractTableModel(parent)
{
    qRegisterMetaType<MultiAudioInput>();
    qRegisterMetaType<MultiAudioInput::Status>();
    qRegisterMetaType<MultiAudioInput::AlignmentStatus>();
}

int MultiAudioInputModel::rowCount(const QModelIndex &parent) const
{
    return parent.isValid() ? 0 : m_inputs.size();
}

int MultiAudioInputModel::columnCount(const QModelIndex &parent) const
{
    return parent.isValid() ? 0 : ColumnCount;
}

QVariant MultiAudioInputModel::data(const QModelIndex &index, int role) const
{
    if (!index.isValid() || index.column() < 0 || index.column() >= ColumnCount
        || !isValidRow(m_inputs, index.row())) {
        return {};
    }

    const MultiAudioInput &input = m_inputs.at(index.row());

    switch (role) {
    case PathRole:
        return input.path;
    case AliasRole:
        return input.alias;
    case DurationRole:
        return input.durationMilliseconds;
    case EnabledRole:
        return input.enabled;
    case HasAlignmentOffsetRole:
        return input.hasAlignmentOffset;
    case AlignmentOffsetRole:
        return input.alignmentOffsetMilliseconds;
    case ConfidenceRole:
        return input.confidence;
    case StatusRole:
        return static_cast<int>(input.status);
    case StatusDetailRole:
        return input.statusDetail;
    case ReferenceRole:
        return index.row() == m_referenceRow;
    case AlignmentOffsetIsManualRole:
        return input.alignmentOffsetIsManual;
    case HasDriftRole:
        return input.hasDrift;
    case DriftPpmRole:
        return input.driftPpm;
    case AlignmentStatusRole:
        return static_cast<int>(input.alignmentStatus);
    case AlignmentDetailRole:
        return input.alignmentDetail;
    case Qt::CheckStateRole:
        if (index.column() == UseColumn) {
            return input.enabled ? Qt::Checked : Qt::Unchecked;
        }
        return {};
    case Qt::EditRole:
        if (index.column() == AliasColumn) {
            return input.alias;
        }
        return {};
    case Qt::ToolTipRole:
        if (index.column() == FileColumn) {
            return QDir::toNativeSeparators(input.path);
        }
        if (index.column() == StatusColumn && !input.statusDetail.isEmpty()) {
            return input.statusDetail;
        }
        if (index.column() == StatusColumn && !input.alignmentDetail.isEmpty()) {
            return input.alignmentDetail;
        }
        if (index.column() == OffsetColumn && input.alignmentOffsetIsManual) {
            return tr("사용자가 지정한 정렬 오프셋입니다.");
        }
        return {};
    case Qt::TextAlignmentRole:
        if (index.column() == UseColumn || index.column() == DurationColumn
            || index.column() == OffsetColumn
            || index.column() == DriftColumn
            || index.column() == ConfidenceColumn
            || index.column() == StatusColumn) {
            return static_cast<int>(Qt::AlignCenter);
        }
        return {};
    case Qt::DisplayRole:
        switch (index.column()) {
        case UseColumn:
            return {};
        case FileColumn:
            return QFileInfo(input.path).fileName();
        case DurationColumn:
            return formatDuration(input.durationMilliseconds);
        case AliasColumn:
            return input.alias;
        case OffsetColumn:
            return input.hasAlignmentOffset
                ? formatOffset(input.alignmentOffsetMilliseconds)
                : QStringLiteral("\u2014");
        case DriftColumn:
            return input.hasDrift
                ? formatDrift(input.driftPpm)
                : QStringLiteral("\u2014");
        case ConfidenceColumn:
            return formatConfidence(input.confidence);
        case StatusColumn:
            if (!input.enabled && input.status == MultiAudioInput::Status::Ready) {
                return tr("제외됨");
            }
            return input.status == MultiAudioInput::Status::Ready
                ? alignmentStatusText(input.alignmentStatus)
                : statusText(input.status);
        default:
            return {};
        }
    default:
        return {};
    }
}

QVariant MultiAudioInputModel::headerData(int section,
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
    case UseColumn:
        return tr("사용");
    case FileColumn:
        return tr("파일");
    case DurationColumn:
        return tr("길이");
    case AliasColumn:
        return tr("마이크 별칭");
    case OffsetColumn:
        return tr("정렬 오프셋");
    case DriftColumn:
        return tr("드리프트");
    case ConfidenceColumn:
        return tr("신뢰도");
    case StatusColumn:
        return tr("상태");
    default:
        return {};
    }
}

Qt::ItemFlags MultiAudioInputModel::flags(const QModelIndex &index) const
{
    if (!index.isValid() || !isValidRow(m_inputs, index.row())) {
        return Qt::NoItemFlags;
    }

    Qt::ItemFlags result = Qt::ItemIsEnabled | Qt::ItemIsSelectable;
    if (index.column() == UseColumn) {
        result |= Qt::ItemIsUserCheckable;
    } else if (index.column() == AliasColumn) {
        result |= Qt::ItemIsEditable;
    }
    return result;
}

bool MultiAudioInputModel::setData(const QModelIndex &index,
                                   const QVariant &value,
                                   int role)
{
    if (!index.isValid() || !isValidRow(m_inputs, index.row())) {
        return false;
    }

    const bool wasValid = isConfigurationValid();
    const bool wasResolved = isAlignmentResolved();
    MultiAudioInput &input = m_inputs[index.row()];

    if (index.column() == AliasColumn && (role == Qt::EditRole || role == AliasRole)) {
        const QString alias = value.toString().trimmed();
        if (input.alias == alias) {
            return true;
        }
        input.alias = alias;
        emit dataChanged(index, index, {Qt::DisplayRole, Qt::EditRole, AliasRole});
        emitValidityChangeIfNeeded(wasValid);
        emitAlignmentResolutionChangeIfNeeded(wasResolved);
        return true;
    }

    if (index.column() == UseColumn
        && (role == Qt::CheckStateRole || role == EnabledRole)) {
        const bool enabled = role == Qt::CheckStateRole
            ? value.toInt() == static_cast<int>(Qt::Checked)
            : value.toBool();
        if (input.enabled == enabled) {
            return true;
        }

        const int oldReference = m_referenceRow;
        const QString oldReferencePath = isValidRow(m_inputs, oldReference)
            ? m_inputs.at(oldReference).path
            : QString();
        input.enabled = enabled;
        emit dataChanged(index, index, {Qt::CheckStateRole, EnabledRole});
        const QModelIndex statusIndex = this->index(index.row(), StatusColumn);
        emit dataChanged(statusIndex, statusIndex, {Qt::DisplayRole});
        correctReferenceRow();
        const QString newReferencePath = isValidRow(m_inputs, m_referenceRow)
            ? m_inputs.at(m_referenceRow).path
            : QString();
        if (pathKey(oldReferencePath) != pathKey(newReferencePath)) {
            for (MultiAudioInput &item : m_inputs) {
                clearAlignmentData(item);
            }
            setReferenceIdentity(m_referenceRow);
            emitAlignmentDataChanged(0, m_inputs.size() - 1);
        }
        emitReferenceRoleChanged(oldReference, m_referenceRow);
        emitValidityChangeIfNeeded(wasValid);
        emitAlignmentResolutionChangeIfNeeded(wasResolved);
        return true;
    }

    return false;
}

bool MultiAudioInputModel::removeRows(int row, int count,
                                      const QModelIndex &parent)
{
    if (parent.isValid() || count <= 0 || row < 0
        || row > m_inputs.size() - count) {
        return false;
    }

    const bool wasValid = isConfigurationValid();
    const bool wasResolved = isAlignmentResolved();
    const int oldReference = m_referenceRow;
    const QString oldReferencePath = isValidRow(m_inputs, oldReference)
        ? m_inputs.at(oldReference).path
        : QString();
    const bool referenceRemoved = oldReference >= row
        && oldReference < row + count;

    beginRemoveRows(parent, row, row + count - 1);
    m_inputs.remove(row, count);
    if (referenceRemoved) {
        m_referenceRow = -1;
    } else if (oldReference >= row + count) {
        m_referenceRow = oldReference - count;
    }
    endRemoveRows();

    const int adjustedReference = m_referenceRow;
    correctReferenceRow();
    const QString newReferencePath = isValidRow(m_inputs, m_referenceRow)
        ? m_inputs.at(m_referenceRow).path
        : QString();
    if (pathKey(oldReferencePath) != pathKey(newReferencePath)) {
        for (MultiAudioInput &input : m_inputs) {
            clearAlignmentData(input);
        }
        setReferenceIdentity(m_referenceRow);
        emitAlignmentDataChanged(0, m_inputs.size() - 1);
    }
    if (adjustedReference == m_referenceRow && m_referenceRow != oldReference) {
        emit referenceRowChanged(m_referenceRow);
    }
    emitReferenceRoleChanged(referenceRemoved ? -1 : oldReference,
                             m_referenceRow);
    emitValidityChangeIfNeeded(wasValid);
    emitAlignmentResolutionChangeIfNeeded(wasResolved);
    return true;
}

QHash<int, QByteArray> MultiAudioInputModel::roleNames() const
{
    QHash<int, QByteArray> roles = QAbstractTableModel::roleNames();
    roles.insert(PathRole, "path");
    roles.insert(AliasRole, "alias");
    roles.insert(DurationRole, "durationMilliseconds");
    roles.insert(EnabledRole, "enabled");
    roles.insert(HasAlignmentOffsetRole, "hasAlignmentOffset");
    roles.insert(AlignmentOffsetRole, "alignmentOffsetMilliseconds");
    roles.insert(ConfidenceRole, "confidence");
    roles.insert(StatusRole, "status");
    roles.insert(StatusDetailRole, "statusDetail");
    roles.insert(ReferenceRole, "reference");
    roles.insert(AlignmentOffsetIsManualRole, "alignmentOffsetIsManual");
    roles.insert(HasDriftRole, "hasDrift");
    roles.insert(DriftPpmRole, "driftPpm");
    roles.insert(AlignmentStatusRole, "alignmentStatus");
    roles.insert(AlignmentDetailRole, "alignmentDetail");
    return roles;
}

QVector<MultiAudioInput> MultiAudioInputModel::inputs() const
{
    return m_inputs;
}

MultiAudioInput MultiAudioInputModel::inputAt(int row) const
{
    return isValidRow(m_inputs, row) ? m_inputs.at(row) : MultiAudioInput{};
}

void MultiAudioInputModel::setInputs(const QVector<MultiAudioInput> &inputs)
{
    const bool wasValid = isConfigurationValid();
    const bool wasResolved = isAlignmentResolved();
    const int oldReference = m_referenceRow;
    const QString previousReferencePath = isValidRow(m_inputs, m_referenceRow)
        ? m_inputs.at(m_referenceRow).path
        : QString();

    QVector<MultiAudioInput> normalizedInputs;
    normalizedInputs.reserve(inputs.size());
    QSet<QString> paths;
    for (MultiAudioInput input : inputs) {
        input.path = normalizedPath(input.path);
        const QString key = pathKey(input.path);
        if (key.isEmpty() || paths.contains(key)) {
            continue;
        }
        paths.insert(key);
        normalizedInputs.append(input);
    }

    const auto isCandidate = [&normalizedInputs](int row) {
        if (!isValidRow(normalizedInputs, row)) {
            return false;
        }
        const MultiAudioInput &input = normalizedInputs.at(row);
        return input.enabled && input.status != MultiAudioInput::Status::Error;
    };

    int newReference = -1;
    if (!previousReferencePath.isEmpty()) {
        const QString previousKey = pathKey(previousReferencePath);
        for (int row = 0; row < normalizedInputs.size(); ++row) {
            if (pathKey(normalizedInputs.at(row).path) == previousKey
                && isCandidate(row)) {
                newReference = row;
                break;
            }
        }
    }
    if (newReference < 0) {
        for (int row = 0; row < normalizedInputs.size(); ++row) {
            const MultiAudioInput &input = normalizedInputs.at(row);
            if (input.enabled && input.status == MultiAudioInput::Status::Ready) {
                newReference = row;
                break;
            }
        }
    }
    if (newReference < 0) {
        for (int row = 0; row < normalizedInputs.size(); ++row) {
            if (isCandidate(row)) {
                newReference = row;
                break;
            }
        }
    }

    const QString newReferencePath = isValidRow(normalizedInputs, newReference)
        ? normalizedInputs.at(newReference).path
        : QString();
    const bool referenceChanged = oldReference != newReference
        || pathKey(previousReferencePath) != pathKey(newReferencePath);

    beginResetModel();
    m_inputs = normalizedInputs;
    m_referenceRow = newReference;
    endResetModel();

    if (referenceChanged) {
        emit referenceRowChanged(m_referenceRow);
    }
    emitValidityChangeIfNeeded(wasValid);
    emitAlignmentResolutionChangeIfNeeded(wasResolved);
}

bool MultiAudioInputModel::addInput(const QString &path, const QString &alias)
{
    const QString normalized = normalizedPath(path);
    if (normalized.isEmpty() || rowForPath(normalized) >= 0) {
        return false;
    }

    const bool wasValid = isConfigurationValid();
    const bool wasResolved = isAlignmentResolved();
    const int oldReference = m_referenceRow;
    const QString oldReferencePath = isValidRow(m_inputs, oldReference)
        ? m_inputs.at(oldReference).path
        : QString();
    MultiAudioInput input;
    input.path = normalized;
    input.alias = alias.trimmed();
    if (input.alias.isEmpty()) {
        input.alias = defaultAlias(normalized);
    }

    const int row = m_inputs.size();
    beginInsertRows(QModelIndex(), row, row);
    m_inputs.append(input);
    endInsertRows();

    correctReferenceRow();
    const QString newReferencePath = isValidRow(m_inputs, m_referenceRow)
        ? m_inputs.at(m_referenceRow).path
        : QString();
    if (pathKey(oldReferencePath) != pathKey(newReferencePath)) {
        for (MultiAudioInput &item : m_inputs) {
            clearAlignmentData(item);
        }
        setReferenceIdentity(m_referenceRow);
        emitAlignmentDataChanged(0, m_inputs.size() - 1);
    }
    emitReferenceRoleChanged(oldReference, m_referenceRow);
    emitValidityChangeIfNeeded(wasValid);
    emitAlignmentResolutionChangeIfNeeded(wasResolved);
    return true;
}

int MultiAudioInputModel::addInputs(const QStringList &paths)
{
    int added = 0;
    for (const QString &path : paths) {
        if (addInput(path)) {
            ++added;
        }
    }
    return added;
}

bool MultiAudioInputModel::removeInput(int row)
{
    return removeRows(row, 1);
}

int MultiAudioInputModel::rowForPath(const QString &path) const
{
    const QString key = pathKey(path);
    if (key.isEmpty()) {
        return -1;
    }
    for (int row = 0; row < m_inputs.size(); ++row) {
        if (pathKey(m_inputs.at(row).path) == key) {
            return row;
        }
    }
    return -1;
}

int MultiAudioInputModel::referenceRow() const noexcept
{
    return m_referenceRow;
}

void MultiAudioInputModel::setReferenceRow(int row)
{
    if (row == m_referenceRow || !isReferenceCandidate(row)) {
        return;
    }

    const bool wasValid = isConfigurationValid();
    const bool wasResolved = isAlignmentResolved();
    const int oldReference = m_referenceRow;
    for (MultiAudioInput &input : m_inputs) {
        clearAlignmentData(input);
    }
    setReferenceRowInternal(row);
    setReferenceIdentity(m_referenceRow);
    emitAlignmentDataChanged(0, m_inputs.size() - 1);
    emitReferenceRoleChanged(oldReference, m_referenceRow);
    emitValidityChangeIfNeeded(wasValid);
    emitAlignmentResolutionChangeIfNeeded(wasResolved);
}

bool MultiAudioInputModel::restoreReferenceRow(int row)
{
    if (!isReferenceCandidate(row)) {
        return false;
    }

    const bool wasValid = isConfigurationValid();
    const bool wasResolved = isAlignmentResolved();
    const int oldReference = m_referenceRow;
    setReferenceRowInternal(row);
    const auto restoredStatus = m_inputs.at(row).alignmentStatus;
    if (restoredStatus == MultiAudioInput::AlignmentStatus::NotAnalyzed
        || restoredStatus == MultiAudioInput::AlignmentStatus::Aligned) {
        setReferenceIdentity(m_referenceRow);
    }
    emitAlignmentDataChanged(row, row);
    emitReferenceRoleChanged(oldReference, m_referenceRow);
    emitValidityChangeIfNeeded(wasValid);
    emitAlignmentResolutionChangeIfNeeded(wasResolved);
    return true;
}

bool MultiAudioInputModel::isConfigurationValid(QString *reason) const
{
    int readyEnabledCount = 0;
    for (const MultiAudioInput &input : m_inputs) {
        if (!input.enabled) {
            continue;
        }
        if (input.alias.trimmed().isEmpty()) {
            if (reason) {
                *reason = tr("사용할 모든 파일에 마이크 별칭을 입력하세요.");
            }
            return false;
        }
        if (!QFileInfo(input.path).isFile()) {
            if (reason) {
                *reason = tr("사용할 오디오 파일을 찾을 수 없습니다.");
            }
            return false;
        }
        if (input.status != MultiAudioInput::Status::Ready) {
            if (reason) {
                *reason = tr("사용할 모든 오디오 파일의 검사가 완료되어야 합니다.");
            }
            return false;
        }
        ++readyEnabledCount;
    }

    if (readyEnabledCount < 2) {
        if (reason) {
            *reason = tr("검사가 완료된 오디오 파일을 2개 이상 사용해야 합니다.");
        }
        return false;
    }

    if (!isValidRow(m_inputs, m_referenceRow)) {
        if (reason) {
            *reason = tr("기준 파일을 선택하세요.");
        }
        return false;
    }

    const MultiAudioInput &reference = m_inputs.at(m_referenceRow);
    if (!reference.enabled || reference.status != MultiAudioInput::Status::Ready
        || reference.alias.trimmed().isEmpty()) {
        if (reason) {
            *reason = tr("검사가 완료된 사용 파일을 기준 파일로 선택하세요.");
        }
        return false;
    }

    if (reason) {
        reason->clear();
    }
    return true;
}

bool MultiAudioInputModel::isAlignmentResolved(QString *reason) const
{
    if (!isConfigurationValid(reason)) {
        return false;
    }

    const MultiAudioInput &reference = m_inputs.at(m_referenceRow);
    const bool referenceIsIdentity =
        reference.alignmentStatus == MultiAudioInput::AlignmentStatus::Aligned
        && reference.hasAlignmentOffset
        && reference.alignmentOffsetMilliseconds == 0
        && !reference.alignmentOffsetIsManual
        && reference.hasDrift
        && std::isfinite(reference.driftPpm)
        && reference.driftPpm == 0.0;
    if (!referenceIsIdentity) {
        if (reason) {
            if (reference.alignmentStatus
                    == MultiAudioInput::AlignmentStatus::Warning) {
                *reason = tr("기준 파일의 정렬 경고를 확인하고 기준 파일을 변경하거나 제외하세요.");
            } else if (reference.alignmentStatus
                       == MultiAudioInput::AlignmentStatus::Failed) {
                *reason = tr("기준 파일의 시간 정렬에 실패했습니다. 다른 기준 파일을 선택하거나 해당 파일을 제외하세요.");
            } else {
                *reason = tr("기준 파일의 시간 정렬을 완료하세요.");
            }
        }
        return false;
    }

    for (int row = 0; row < m_inputs.size(); ++row) {
        const MultiAudioInput &input = m_inputs.at(row);
        if (!input.enabled || row == m_referenceRow) {
            continue;
        }

        const bool aligned = input.alignmentStatus
                == MultiAudioInput::AlignmentStatus::Aligned
            && input.hasAlignmentOffset;
        const bool manual = input.alignmentStatus
                == MultiAudioInput::AlignmentStatus::Manual
            && input.hasAlignmentOffset
            && input.alignmentOffsetIsManual
            && input.manualAlignmentCorrectionAllowed;
        if (aligned || manual) {
            continue;
        }

        if (reason) {
            switch (input.alignmentStatus) {
            case MultiAudioInput::AlignmentStatus::Analyzing:
                *reason = tr("시간 정렬 분석이 진행 중입니다.");
                break;
            case MultiAudioInput::AlignmentStatus::Warning:
                *reason = tr("정렬 경고가 있는 파일을 수동 보정하거나 제외하세요.");
                break;
            case MultiAudioInput::AlignmentStatus::Failed:
                *reason = tr("정렬에 실패한 파일을 수동 보정하거나 제외하세요.");
                break;
            case MultiAudioInput::AlignmentStatus::NotAnalyzed:
            case MultiAudioInput::AlignmentStatus::Aligned:
            case MultiAudioInput::AlignmentStatus::Manual:
                *reason = tr("사용할 모든 파일의 시간 정렬을 완료하세요.");
                break;
            }
        }
        return false;
    }

    if (reason) {
        reason->clear();
    }
    return true;
}

QString MultiAudioInputModel::selectionAnalysisKey(
    const QVector<MultiAudioInput> &inputs, int referenceRow)
{
    const QString timelineKey = selectionTimelineKey(inputs, referenceRow);
    if (timelineKey.isEmpty()) {
        return {};
    }

    QStringList parts{timelineKey};
    for (const MultiAudioInput &input : inputs) {
        if (!input.enabled) {
            continue;
        }
        // Joined without QString::arg() so a '%' in a path cannot be
        // mistaken for a placeholder.
        parts.append(QStringList{
            pathKey(input.path),
            QString::number(input.durationMilliseconds),
            QString::number(static_cast<int>(input.alignmentStatus)),
            input.hasAlignmentOffset
                ? QString::number(input.alignmentOffsetMilliseconds)
                : QStringLiteral("-"),
            input.alignmentOffsetIsManual ? QStringLiteral("manual")
                                          : QStringLiteral("auto"),
            input.hasDrift ? QString::number(input.driftPpm, 'g', 17)
                           : QStringLiteral("-")}
                         .join(QLatin1Char('|')));
    }
    return parts.join(QLatin1Char('\n'));
}

QString MultiAudioInputModel::selectionTimelineKey(
    const QVector<MultiAudioInput> &inputs, int referenceRow)
{
    if (!isValidRow(inputs, referenceRow)) {
        return {};
    }
    const MultiAudioInput &reference = inputs.at(referenceRow);
    return pathKey(reference.path) + QLatin1Char('|')
        + QString::number(reference.durationMilliseconds);
}

bool MultiAudioInputModel::setInspectionStarted(const QString &path)
{
    const int row = rowForPath(path);
    if (row < 0) {
        return false;
    }

    const bool wasValid = isConfigurationValid();
    const bool wasResolved = isAlignmentResolved();
    const int oldReference = m_referenceRow;
    const QString oldReferencePath = isValidRow(m_inputs, oldReference)
        ? m_inputs.at(oldReference).path
        : QString();
    const bool inspectingReference = row == oldReference;
    if (inspectingReference) {
        for (MultiAudioInput &item : m_inputs) {
            clearAlignmentData(item);
        }
    } else {
        clearAlignmentData(m_inputs[row]);
    }
    MultiAudioInput &input = m_inputs[row];
    input.durationMilliseconds = -1;
    input.status = MultiAudioInput::Status::Inspecting;
    input.statusDetail.clear();
    correctReferenceRow();
    const QString newReferencePath = isValidRow(m_inputs, m_referenceRow)
        ? m_inputs.at(m_referenceRow).path
        : QString();
    if (pathKey(oldReferencePath) != pathKey(newReferencePath)) {
        for (MultiAudioInput &item : m_inputs) {
            clearAlignmentData(item);
        }
    }
    emit dataChanged(index(row, DurationColumn), index(row, StatusColumn),
                     {Qt::DisplayRole, Qt::ToolTipRole, DurationRole,
                      StatusRole, StatusDetailRole});
    emitAlignmentDataChanged(inspectingReference ? 0 : row,
                             inspectingReference ? m_inputs.size() - 1 : row);
    emitReferenceRoleChanged(oldReference, m_referenceRow);
    emitValidityChangeIfNeeded(wasValid);
    emitAlignmentResolutionChangeIfNeeded(wasResolved);
    return true;
}

bool MultiAudioInputModel::setInspectionSucceeded(const QString &path,
                                                  qint64 durationMilliseconds)
{
    const int row = rowForPath(path);
    if (row < 0 || durationMilliseconds < 0
        || m_inputs.at(row).status != MultiAudioInput::Status::Inspecting) {
        return false;
    }

    const bool wasValid = isConfigurationValid();
    const bool wasResolved = isAlignmentResolved();
    const int oldReference = m_referenceRow;
    const QString oldReferencePath = isValidRow(m_inputs, oldReference)
        ? m_inputs.at(oldReference).path
        : QString();
    MultiAudioInput &input = m_inputs[row];
    input.durationMilliseconds = durationMilliseconds;
    input.status = MultiAudioInput::Status::Ready;
    input.statusDetail.clear();
    correctReferenceRow();
    const QString newReferencePath = isValidRow(m_inputs, m_referenceRow)
        ? m_inputs.at(m_referenceRow).path
        : QString();
    if (pathKey(oldReferencePath) != pathKey(newReferencePath)) {
        for (MultiAudioInput &item : m_inputs) {
            clearAlignmentData(item);
        }
        emitAlignmentDataChanged(0, m_inputs.size() - 1);
    }
    setReferenceIdentity(m_referenceRow);
    emit dataChanged(index(row, DurationColumn), index(row, StatusColumn),
                     {Qt::DisplayRole, Qt::ToolTipRole, DurationRole,
                      StatusRole, StatusDetailRole, HasAlignmentOffsetRole,
                      AlignmentOffsetRole, AlignmentOffsetIsManualRole,
                      HasDriftRole, DriftPpmRole, ConfidenceRole,
                      AlignmentStatusRole, AlignmentDetailRole});
    emitReferenceRoleChanged(oldReference, m_referenceRow);
    emitValidityChangeIfNeeded(wasValid);
    emitAlignmentResolutionChangeIfNeeded(wasResolved);
    return true;
}

bool MultiAudioInputModel::setInspectionFailed(const QString &path,
                                               const QString &detail)
{
    const int row = rowForPath(path);
    if (row < 0
        || m_inputs.at(row).status != MultiAudioInput::Status::Inspecting) {
        return false;
    }

    const bool wasValid = isConfigurationValid();
    const bool wasResolved = isAlignmentResolved();
    const int oldReference = m_referenceRow;
    const QString oldReferencePath = isValidRow(m_inputs, oldReference)
        ? m_inputs.at(oldReference).path
        : QString();
    if (row == oldReference) {
        for (MultiAudioInput &item : m_inputs) {
            clearAlignmentData(item);
        }
    } else {
        clearAlignmentData(m_inputs[row]);
    }
    MultiAudioInput &input = m_inputs[row];
    input.durationMilliseconds = -1;
    input.status = MultiAudioInput::Status::Error;
    input.statusDetail = detail.trimmed();
    correctReferenceRow();
    const QString newReferencePath = isValidRow(m_inputs, m_referenceRow)
        ? m_inputs.at(m_referenceRow).path
        : QString();
    if (pathKey(oldReferencePath) != pathKey(newReferencePath)) {
        for (MultiAudioInput &item : m_inputs) {
            clearAlignmentData(item);
        }
    }
    setReferenceIdentity(m_referenceRow);
    emit dataChanged(index(row, DurationColumn), index(row, StatusColumn),
                     {Qt::DisplayRole, Qt::ToolTipRole, DurationRole,
                      StatusRole, StatusDetailRole});
    emitAlignmentDataChanged(0, m_inputs.size() - 1);
    emitReferenceRoleChanged(oldReference, m_referenceRow);
    emitValidityChangeIfNeeded(wasValid);
    emitAlignmentResolutionChangeIfNeeded(wasResolved);
    return true;
}

bool MultiAudioInputModel::setAlignmentResult(const QString &path,
                                              qint64 offsetMilliseconds,
                                              double confidence,
                                              const QString &detail)
{
    return applyAlignmentResult(path,
                                MultiAudioInput::AlignmentStatus::Aligned,
                                true,
                                offsetMilliseconds,
                                confidence,
                                false,
                                0.0,
                                detail);
}

bool MultiAudioInputModel::startAlignmentAnalysis()
{
    if (!isConfigurationValid()) {
        return false;
    }

    const bool wasResolved = isAlignmentResolved();
    for (int row = 0; row < m_inputs.size(); ++row) {
        MultiAudioInput &input = m_inputs[row];
        clearAlignmentData(input);
        if (input.enabled && row != m_referenceRow) {
            input.alignmentStatus = MultiAudioInput::AlignmentStatus::Analyzing;
        }
    }
    setReferenceIdentity(m_referenceRow);
    emitAlignmentDataChanged(0, m_inputs.size() - 1);
    emitAlignmentResolutionChangeIfNeeded(wasResolved);
    return true;
}

bool MultiAudioInputModel::applyAlignmentResult(
    const QString &path,
    MultiAudioInput::AlignmentStatus status,
    bool hasOffset,
    qint64 offsetMilliseconds,
    double confidence,
    bool hasDrift,
    double driftPpm,
    const QString &detail,
    bool allowManualCorrection)
{
    const int row = rowForPath(path);
    if (row < 0 || m_inputs.at(row).status != MultiAudioInput::Status::Ready
        || !std::isfinite(confidence) || confidence < 0.0 || confidence > 1.0
        || (hasDrift && !std::isfinite(driftPpm))) {
        return false;
    }
    if (status != MultiAudioInput::AlignmentStatus::Aligned
        && status != MultiAudioInput::AlignmentStatus::Warning
        && status != MultiAudioInput::AlignmentStatus::Failed) {
        return false;
    }
    if (status == MultiAudioInput::AlignmentStatus::Aligned && !hasOffset) {
        return false;
    }

    const bool wasResolved = isAlignmentResolved();
    if (row == m_referenceRow
        && status == MultiAudioInput::AlignmentStatus::Aligned) {
        setReferenceIdentity(row);
    } else {
        MultiAudioInput &input = m_inputs[row];
        input.hasAlignmentOffset = hasOffset;
        input.alignmentOffsetMilliseconds = hasOffset ? offsetMilliseconds : 0;
        input.alignmentOffsetIsManual = false;
        input.manualAlignmentCorrectionAllowed =
            allowManualCorrection
            && (status == MultiAudioInput::AlignmentStatus::Warning
                || status == MultiAudioInput::AlignmentStatus::Failed);
        input.hasDrift = hasDrift;
        input.driftPpm = hasDrift ? driftPpm : 0.0;
        input.confidence = confidence;
        input.alignmentStatus = status;
        input.alignmentDetail = detail.trimmed();
    }
    emitAlignmentDataChanged(row, row);
    emitAlignmentResolutionChangeIfNeeded(wasResolved);
    return true;
}

bool MultiAudioInputModel::setManualAlignmentOffset(
    const QString &path,
    qint64 offsetMilliseconds,
    const QString &detail)
{
    const int row = rowForPath(path);
    if (row < 0 || row == m_referenceRow
        || m_inputs.at(row).status != MultiAudioInput::Status::Ready
        || !m_inputs.at(row).manualAlignmentCorrectionAllowed
        || (m_inputs.at(row).alignmentStatus
                != MultiAudioInput::AlignmentStatus::Warning
            && m_inputs.at(row).alignmentStatus
                != MultiAudioInput::AlignmentStatus::Failed
            && m_inputs.at(row).alignmentStatus
                != MultiAudioInput::AlignmentStatus::Manual)) {
        return false;
    }

    const bool wasResolved = isAlignmentResolved();
    MultiAudioInput &input = m_inputs[row];
    input.hasAlignmentOffset = true;
    input.alignmentOffsetMilliseconds = offsetMilliseconds;
    input.alignmentOffsetIsManual = true;
    input.manualAlignmentCorrectionAllowed = true;
    input.alignmentStatus = MultiAudioInput::AlignmentStatus::Manual;
    input.alignmentDetail = detail.trimmed().isEmpty()
        ? tr("사용자가 정렬 오프셋을 수동으로 지정했습니다.")
        : detail.trimmed();
    emitAlignmentDataChanged(row, row);
    emitAlignmentResolutionChangeIfNeeded(wasResolved);
    return true;
}

void MultiAudioInputModel::cancelAlignmentAnalysis()
{
    const bool wasResolved = isAlignmentResolved();
    int firstChanged = m_inputs.size();
    int lastChanged = -1;
    for (int row = 0; row < m_inputs.size(); ++row) {
        if (m_inputs.at(row).alignmentStatus
            != MultiAudioInput::AlignmentStatus::Analyzing) {
            continue;
        }
        clearAlignmentData(m_inputs[row]);
        firstChanged = qMin(firstChanged, row);
        lastChanged = qMax(lastChanged, row);
    }
    setReferenceIdentity(m_referenceRow);
    if (lastChanged >= firstChanged) {
        emitAlignmentDataChanged(firstChanged, lastChanged);
    }
    emitAlignmentResolutionChangeIfNeeded(wasResolved);
}

bool MultiAudioInputModel::clearAlignmentResult(const QString &path)
{
    const int row = rowForPath(path);
    if (row < 0) {
        return false;
    }

    const bool wasResolved = isAlignmentResolved();
    clearAlignmentData(m_inputs[row]);
    if (row == m_referenceRow) {
        setReferenceIdentity(row);
    }
    emitAlignmentDataChanged(row, row);
    emitAlignmentResolutionChangeIfNeeded(wasResolved);
    return true;
}

void MultiAudioInputModel::clearAlignmentResults()
{
    const bool wasResolved = isAlignmentResolved();
    for (MultiAudioInput &input : m_inputs) {
        clearAlignmentData(input);
    }
    setReferenceIdentity(m_referenceRow);
    emitAlignmentDataChanged(0, m_inputs.size() - 1);
    emitAlignmentResolutionChangeIfNeeded(wasResolved);
}

QString MultiAudioInputModel::normalizedPath(const QString &path)
{
    const QString trimmed = path.trimmed();
    if (trimmed.isEmpty()) {
        return {};
    }

    const QFileInfo file(trimmed);
    const QString canonical = file.canonicalFilePath();
    const QString absolute = canonical.isEmpty() ? file.absoluteFilePath() : canonical;
    return QDir::cleanPath(QDir::fromNativeSeparators(absolute));
}

QString MultiAudioInputModel::pathKey(const QString &path)
{
    QString key = normalizedPath(path);
#ifdef Q_OS_WIN
    key = key.toCaseFolded();
#endif
    return key;
}

QString MultiAudioInputModel::defaultAlias(const QString &path)
{
    const QFileInfo file(path);
    const QString completeBaseName = file.completeBaseName().trimmed();
    return completeBaseName.isEmpty() ? file.fileName() : completeBaseName;
}

QString MultiAudioInputModel::formatDuration(qint64 milliseconds)
{
    if (milliseconds < 0) {
        return QStringLiteral("\u2014");
    }

    const qint64 totalSeconds = milliseconds / 1000;
    const qint64 seconds = totalSeconds % 60;
    const qint64 totalMinutes = totalSeconds / 60;
    const qint64 minutes = totalMinutes % 60;
    const qint64 hours = totalMinutes / 60;
    if (hours > 0) {
        return QStringLiteral("%1:%2:%3")
            .arg(hours)
            .arg(minutes, 2, 10, QLatin1Char('0'))
            .arg(seconds, 2, 10, QLatin1Char('0'));
    }
    return QStringLiteral("%1:%2")
        .arg(minutes, 2, 10, QLatin1Char('0'))
        .arg(seconds, 2, 10, QLatin1Char('0'));
}

QString MultiAudioInputModel::formatOffset(qint64 milliseconds)
{
    const QString prefix = milliseconds > 0 ? QStringLiteral("+") : QString();
    return tr("%1%2 ms").arg(prefix).arg(milliseconds);
}

QString MultiAudioInputModel::formatDrift(double driftPpm)
{
    if (!std::isfinite(driftPpm)) {
        return QStringLiteral("\u2014");
    }
    const QString prefix = driftPpm > 0.0 ? QStringLiteral("+") : QString();
    return tr("%1%2 ppm")
        .arg(prefix, QLocale().toString(driftPpm, 'f', 1));
}

QString MultiAudioInputModel::formatConfidence(double confidence)
{
    if (!std::isfinite(confidence) || confidence < 0.0) {
        return QStringLiteral("\u2014");
    }
    return QLocale().toString(confidence * 100.0, 'f', 1)
        + QStringLiteral(" %");
}

QString MultiAudioInputModel::statusText(MultiAudioInput::Status status)
{
    switch (status) {
    case MultiAudioInput::Status::Pending:
        return tr("대기");
    case MultiAudioInput::Status::Inspecting:
        return tr("검사 중");
    case MultiAudioInput::Status::Ready:
        return tr("준비됨");
    case MultiAudioInput::Status::Error:
        return tr("오류");
    }
    return {};
}

QString MultiAudioInputModel::alignmentStatusText(
    MultiAudioInput::AlignmentStatus status)
{
    switch (status) {
    case MultiAudioInput::AlignmentStatus::NotAnalyzed:
        return tr("분석 전");
    case MultiAudioInput::AlignmentStatus::Analyzing:
        return tr("분석 중");
    case MultiAudioInput::AlignmentStatus::Aligned:
        return tr("성공");
    case MultiAudioInput::AlignmentStatus::Warning:
        return tr("경고");
    case MultiAudioInput::AlignmentStatus::Failed:
        return tr("실패");
    case MultiAudioInput::AlignmentStatus::Manual:
        return tr("수동 보정");
    }
    return {};
}

bool MultiAudioInputModel::isReferenceCandidate(int row) const
{
    if (!isValidRow(m_inputs, row)) {
        return false;
    }
    const MultiAudioInput &input = m_inputs.at(row);
    return input.enabled && input.status != MultiAudioInput::Status::Error;
}

void MultiAudioInputModel::correctReferenceRow()
{
    if (isReferenceCandidate(m_referenceRow)) {
        return;
    }

    int candidate = -1;
    for (int row = 0; row < m_inputs.size(); ++row) {
        const MultiAudioInput &input = m_inputs.at(row);
        if (input.enabled && input.status == MultiAudioInput::Status::Ready) {
            candidate = row;
            break;
        }
    }
    if (candidate < 0) {
        for (int row = 0; row < m_inputs.size(); ++row) {
            if (isReferenceCandidate(row)) {
                candidate = row;
                break;
            }
        }
    }
    setReferenceRowInternal(candidate);
}

void MultiAudioInputModel::setReferenceRowInternal(int row)
{
    if (m_referenceRow == row) {
        return;
    }
    m_referenceRow = row;
    emit referenceRowChanged(m_referenceRow);
}

void MultiAudioInputModel::emitValidityChangeIfNeeded(bool wasValid)
{
    const bool valid = isConfigurationValid();
    if (valid != wasValid) {
        emit configurationValidityChanged(valid);
    }
}

void MultiAudioInputModel::emitAlignmentResolutionChangeIfNeeded(
    bool wasResolved)
{
    const bool resolved = isAlignmentResolved();
    if (resolved != wasResolved) {
        emit alignmentResolutionChanged(resolved);
    }
}

void MultiAudioInputModel::emitReferenceRoleChanged(int oldRow, int newRow)
{
    if (oldRow == newRow) {
        return;
    }
    if (isValidRow(m_inputs, oldRow)) {
        const QModelIndex changed = index(oldRow, 0);
        emit dataChanged(changed, index(oldRow, ColumnCount - 1), {ReferenceRole});
    }
    if (isValidRow(m_inputs, newRow)) {
        const QModelIndex changed = index(newRow, 0);
        emit dataChanged(changed, index(newRow, ColumnCount - 1), {ReferenceRole});
    }
}

void MultiAudioInputModel::clearAlignmentData(MultiAudioInput &input)
{
    input.hasAlignmentOffset = false;
    input.alignmentOffsetMilliseconds = 0;
    input.alignmentOffsetIsManual = false;
    input.manualAlignmentCorrectionAllowed = false;
    input.hasDrift = false;
    input.driftPpm = 0.0;
    input.confidence = -1.0;
    input.alignmentStatus = MultiAudioInput::AlignmentStatus::NotAnalyzed;
    input.alignmentDetail.clear();
}

void MultiAudioInputModel::setReferenceIdentity(int row)
{
    if (!isValidRow(m_inputs, row)
        || m_inputs.at(row).status != MultiAudioInput::Status::Ready) {
        return;
    }

    MultiAudioInput &input = m_inputs[row];
    clearAlignmentData(input);
    input.hasAlignmentOffset = true;
    input.alignmentOffsetMilliseconds = 0;
    input.hasDrift = true;
    input.driftPpm = 0.0;
    input.confidence = 1.0;
    input.alignmentStatus = MultiAudioInput::AlignmentStatus::Aligned;
}

void MultiAudioInputModel::emitAlignmentDataChanged(int firstRow, int lastRow)
{
    if (m_inputs.isEmpty() || firstRow < 0 || lastRow < firstRow
        || lastRow >= m_inputs.size()) {
        return;
    }

    emit dataChanged(index(firstRow, OffsetColumn), index(lastRow, StatusColumn),
                     {Qt::DisplayRole, Qt::ToolTipRole,
                      HasAlignmentOffsetRole, AlignmentOffsetRole,
                      AlignmentOffsetIsManualRole, HasDriftRole, DriftPpmRole,
                      ConfidenceRole, AlignmentStatusRole,
                      AlignmentDetailRole});
}
