#ifndef MULTIAUDIOINPUTMODEL_H
#define MULTIAUDIOINPUTMODEL_H

#include <QAbstractTableModel>
#include <QString>
#include <QStringList>
#include <QVector>

struct MultiAudioInput
{
    enum class Status
    {
        Pending,
        Inspecting,
        Ready,
        Error
    };

    enum class AlignmentStatus
    {
        NotAnalyzed,
        Analyzing,
        Aligned,
        Warning,
        Failed,
        Manual
    };

    QString path;
    QString alias;
    qint64 durationMilliseconds = -1;
    bool enabled = true;
    bool hasAlignmentOffset = false;
    qint64 alignmentOffsetMilliseconds = 0;
    bool alignmentOffsetIsManual = false;
    bool manualAlignmentCorrectionAllowed = false;
    bool hasDrift = false;
    double driftPpm = 0.0;
    double confidence = -1.0;
    Status status = Status::Pending;
    QString statusDetail;
    AlignmentStatus alignmentStatus = AlignmentStatus::NotAnalyzed;
    QString alignmentDetail;
};

Q_DECLARE_METATYPE(MultiAudioInput)
Q_DECLARE_METATYPE(MultiAudioInput::Status)
Q_DECLARE_METATYPE(MultiAudioInput::AlignmentStatus)

class MultiAudioInputModel final : public QAbstractTableModel
{
    Q_OBJECT

public:
    enum Column
    {
        UseColumn = 0,
        FileColumn,
        DurationColumn,
        AliasColumn,
        OffsetColumn,
        DriftColumn,
        ConfidenceColumn,
        StatusColumn,
        ColumnCount,

        MicrophoneAliasColumn = AliasColumn,
        AlignmentOffsetColumn = OffsetColumn
    };

    enum Role
    {
        PathRole = Qt::UserRole + 1,
        AliasRole,
        DurationRole,
        EnabledRole,
        HasAlignmentOffsetRole,
        AlignmentOffsetRole,
        ConfidenceRole,
        StatusRole,
        StatusDetailRole,
        ReferenceRole,
        AlignmentOffsetIsManualRole,
        HasDriftRole,
        DriftPpmRole,
        AlignmentStatusRole,
        AlignmentDetailRole,

        DurationMillisecondsRole = DurationRole,
        AlignmentOffsetMillisecondsRole = AlignmentOffsetRole,
        IsReferenceRole = ReferenceRole
    };

    explicit MultiAudioInputModel(QObject *parent = nullptr);

    int rowCount(const QModelIndex &parent = QModelIndex()) const override;
    int columnCount(const QModelIndex &parent = QModelIndex()) const override;
    QVariant data(const QModelIndex &index,
                  int role = Qt::DisplayRole) const override;
    QVariant headerData(int section, Qt::Orientation orientation,
                        int role = Qt::DisplayRole) const override;
    Qt::ItemFlags flags(const QModelIndex &index) const override;
    bool setData(const QModelIndex &index, const QVariant &value,
                 int role = Qt::EditRole) override;
    bool removeRows(int row, int count,
                    const QModelIndex &parent = QModelIndex()) override;
    QHash<int, QByteArray> roleNames() const override;

    QVector<MultiAudioInput> inputs() const;
    MultiAudioInput inputAt(int row) const;
    void setInputs(const QVector<MultiAudioInput> &inputs);

    bool addInput(const QString &path, const QString &alias = QString());
    int addInputs(const QStringList &paths);
    bool removeInput(int row);

    int rowForPath(const QString &path) const;

    int referenceRow() const noexcept;
    void setReferenceRow(int row);
    bool restoreReferenceRow(int row);

    bool isConfigurationValid(QString *reason = nullptr) const;
    bool isAlignmentResolved(QString *reason = nullptr) const;

    // A channel-selection result stays valid only while the used inputs, the
    // reference and their reviewed alignment are unchanged. The timeline key
    // identifies just the reference recording that defines the time axis.
    static QString selectionAnalysisKey(const QVector<MultiAudioInput> &inputs,
                                        int referenceRow);
    static QString selectionTimelineKey(const QVector<MultiAudioInput> &inputs,
                                        int referenceRow);

    bool setInspectionStarted(const QString &path);
    bool setInspectionSucceeded(const QString &path,
                                qint64 durationMilliseconds);
    bool setInspectionFailed(const QString &path, const QString &detail);

    bool setAlignmentResult(const QString &path,
                            qint64 offsetMilliseconds,
                            double confidence,
                            const QString &detail = QString());
    bool startAlignmentAnalysis();
    bool applyAlignmentResult(const QString &path,
                              MultiAudioInput::AlignmentStatus status,
                              bool hasOffset,
                              qint64 offsetMilliseconds,
                              double confidence,
                              bool hasDrift,
                              double driftPpm,
                              const QString &detail = QString(),
                              bool allowManualCorrection = true);
    bool setManualAlignmentOffset(const QString &path,
                                  qint64 offsetMilliseconds,
                                  const QString &detail = QString());
    void cancelAlignmentAnalysis();
    bool clearAlignmentResult(const QString &path);
    void clearAlignmentResults();

signals:
    void referenceRowChanged(int row);
    void configurationValidityChanged(bool valid);
    void alignmentResolutionChanged(bool resolved);

private:
    static QString normalizedPath(const QString &path);
    static QString pathKey(const QString &path);
    static QString defaultAlias(const QString &path);
    static QString formatDuration(qint64 milliseconds);
    static QString formatOffset(qint64 milliseconds);
    static QString formatDrift(double driftPpm);
    static QString formatConfidence(double confidence);
    static QString statusText(MultiAudioInput::Status status);
    static QString alignmentStatusText(MultiAudioInput::AlignmentStatus status);

    bool isReferenceCandidate(int row) const;
    void correctReferenceRow();
    void setReferenceRowInternal(int row);
    void emitValidityChangeIfNeeded(bool wasValid);
    void emitAlignmentResolutionChangeIfNeeded(bool wasResolved);
    void emitReferenceRoleChanged(int oldRow, int newRow);
    void clearAlignmentData(MultiAudioInput &input);
    void setReferenceIdentity(int row);
    void emitAlignmentDataChanged(int firstRow, int lastRow);

    QVector<MultiAudioInput> m_inputs;
    int m_referenceRow = -1;
};

#endif // MULTIAUDIOINPUTMODEL_H
