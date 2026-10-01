#ifndef TRANSCRIPTSEGMENT_H
#define TRANSCRIPTSEGMENT_H

#include <QMetaType>
#include <QString>

#include <optional>

// speaker is the one-based value used on the JSON wire and in the UI.
// std::nullopt represents an unassigned speaker (JSON null).
struct TranscriptSegment
{
    double startTime = 0.0;
    double endTime = 0.0;
    std::optional<int> speaker;
    QString text;
};

Q_DECLARE_METATYPE(TranscriptSegment)

#endif // TRANSCRIPTSEGMENT_H
