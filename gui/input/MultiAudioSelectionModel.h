#ifndef MULTIAUDIOSELECTIONMODEL_H
#define MULTIAUDIOSELECTIONMODEL_H

#include "MultiAudioInputModel.h"

#include <QAbstractTableModel>
#include <QJsonObject>
#include <QString>
#include <QStringList>
#include <QVector>

struct MultiAudioSegmentEvidence
{
    QString path;
    bool hasScore = false;
    double score = 0.0;
    double validRatio = 0.0;
};

// One review row: a part of a final channel-selection segment cut on the
// engine's fixed review grid, so long segments can be checked and overridden
// piecewise.
struct MultiAudioSelectionSegment
{
    // Half-open interval on the 16 kHz reference timeline.
    qint64 startSample = 0;
    qint64 endSample = 0;
    QString automaticInputPath;
    bool hasAutomaticScore = false;
    double automaticScore = 0.0;
    // Confidence and reasons belong to the whole automatic segment below.
    double automaticConfidence = 0.0;
    QStringList automaticReasonCodes;
    qint64 automaticSegmentStartSample = 0;
    qint64 automaticSegmentEndSample = 0;
    // Empty while the automatic choice is used.
    QString overrideInputPath;
    QVector<MultiAudioSegmentEvidence> inputs;

    bool isManual() const noexcept;
    QString finalInputPath() const;
    const MultiAudioSegmentEvidence *evidence(const QString &path) const;
};

struct MultiAudioSelectionPlan
{
    QString analysisKey;
    QString timelineKey;
    // Used inputs in manifest order at analysis time.
    QStringList inputPaths;
    int sampleRate = 0;
    qint64 timelineSampleCount = 0;
    QVector<MultiAudioSelectionSegment> segments;
    QStringList warnings;

    bool isEmpty() const noexcept;
    bool isCurrentFor(const QVector<MultiAudioInput> &inputs,
                      int referenceRow) const;
    int manualSegmentCount() const;
    int switchCount() const;
    // Final segments whose selected microphone did not record all of it.
    int incompleteFinalSegmentCount() const;
};

Q_DECLARE_METATYPE(MultiAudioSelectionPlan)

struct MultiAudioManifestRequest
{
    QJsonObject manifest;
    // Manifest input index -> input row.
    QVector<int> rows;
    int referenceIndex = -1;
    int overrideCount = 0;
    // Manual selections that no longer fit the inputs or reference timeline.
    int droppedOverrideCount = 0;
};

// Builds the version 1 multi-input manifest from the used inputs, their
// reviewed manual offsets and the manual channel overrides of `selection`.
// The same manifest drives quality analysis and, later, merging and
// transcription, so the reviewed choices are the ones that are applied.
MultiAudioManifestRequest buildMultiAudioManifest(
    const QVector<MultiAudioInput> &inputs,
    int referenceRow,
    const MultiAudioSelectionPlan &selection = MultiAudioSelectionPlan());

// Validates a successful `multimic_quality.py` response for the manifest
// built from `manifestInputs` and fills `plan` (except its keys). The applied
// alignment must match the reviewed alignment shown to the user.
bool parseMultiAudioQualityResponse(const QJsonObject &payload,
                                    const QVector<MultiAudioInput> &manifestInputs,
                                    int referenceIndex,
                                    MultiAudioSelectionPlan *plan,
                                    QString *error);

class MultiAudioSelectionModel final : public QAbstractTableModel
{
    Q_OBJECT

public:
    enum Column
    {
        StartColumn = 0,
        EndColumn,
        AutomaticInputColumn,
        AutomaticScoreColumn,
        ConfidenceColumn,
        ReasonColumn,
        FinalInputColumn,
        FinalScoreColumn,
        SourceColumn,
        ColumnCount
    };

    enum Role
    {
        StartSampleRole = Qt::UserRole + 1,
        EndSampleRole,
        AutomaticInputPathRole,
        OverrideInputPathRole,
        FinalInputPathRole,
        IsManualRole
    };

    explicit MultiAudioSelectionModel(QObject *parent = nullptr);

    int rowCount(const QModelIndex &parent = QModelIndex()) const override;
    int columnCount(const QModelIndex &parent = QModelIndex()) const override;
    QVariant data(const QModelIndex &index,
                  int role = Qt::DisplayRole) const override;
    QVariant headerData(int section, Qt::Orientation orientation,
                        int role = Qt::DisplayRole) const override;
    QHash<int, QByteArray> roleNames() const override;

    MultiAudioSelectionPlan plan() const;
    void setPlan(const MultiAudioSelectionPlan &plan);
    void setInputContext(const QVector<MultiAudioInput> &inputs, int referenceRow);
    bool isCurrent() const;
    MultiAudioSelectionSegment segmentAt(int row) const;

    // Choosing the automatically selected microphone clears the override, so
    // a row is manual only when it really differs from the automatic plan.
    int setOverride(const QVector<int> &rows, const QString &inputPath);
    int clearOverrides(const QVector<int> &rows);
    int clearAllOverrides();

    QString aliasForPath(const QString &path) const;
    static QString reasonLabel(const QString &code);
    static QString reasonDescription(const QString &code);
    static QString formatPosition(qint64 sample, int sampleRate);

signals:
    void overridesChanged();

private:
    QString rowToolTip(const MultiAudioSelectionSegment &segment) const;
    QString finalScoreText(const MultiAudioSelectionSegment &segment) const;

    MultiAudioSelectionPlan m_plan;
    QVector<MultiAudioInput> m_inputs;
    int m_referenceRow = -1;
};

#endif // MULTIAUDIOSELECTIONMODEL_H
