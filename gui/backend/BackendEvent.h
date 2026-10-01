#ifndef BACKENDEVENT_H
#define BACKENDEVENT_H

#include "TranscriptSegment.h"

#include <QByteArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>
#include <QString>

#include <cmath>
#include <limits>
#include <optional>

enum class BackendEventType
{
    State,
    Progress,
    Segment,
    Completed,
    Error
};

enum class BackendState
{
    Preparing,
    LoadingModel,
    DecodingAudio,
    Transcribing,
    Diarization,
    SavingResult
};

struct BackendEvent
{
    BackendEventType type = BackendEventType::State;
    BackendState state = BackendState::Preparing;
    std::optional<int> progress;
    std::optional<double> processedSeconds;
    std::optional<double> totalSeconds;
    TranscriptSegment segment;
    QString textFile;
    QString srtFile;
    QString errorMessage;
};

namespace BackendProtocol {

inline bool fail(QString *errorMessage, const QString &message)
{
    if (errorMessage) {
        *errorMessage = message;
    }
    return false;
}

inline bool requiredString(const QJsonObject &object, const QString &name,
                           QString *value, QString *errorMessage,
                           bool nonEmpty = false)
{
    if (!object.contains(name)) {
        return fail(errorMessage, QStringLiteral("missing required field: %1").arg(name));
    }
    const QJsonValue jsonValue = object.value(name);
    if (!jsonValue.isString()) {
        return fail(errorMessage, QStringLiteral("%1 must be a string").arg(name));
    }
    *value = jsonValue.toString();
    if (nonEmpty && value->trimmed().isEmpty()) {
        return fail(errorMessage, QStringLiteral("%1 must not be empty").arg(name));
    }
    return true;
}

inline bool nonNegativeNumber(const QJsonObject &object, const QString &name,
                              double *value, QString *errorMessage)
{
    if (!object.contains(name)) {
        return fail(errorMessage, QStringLiteral("missing required field: %1").arg(name));
    }
    const QJsonValue jsonValue = object.value(name);
    if (!jsonValue.isDouble()) {
        return fail(errorMessage, QStringLiteral("%1 must be a number").arg(name));
    }
    *value = jsonValue.toDouble();
    if (!std::isfinite(*value)) {
        return fail(errorMessage, QStringLiteral("%1 must be finite").arg(name));
    }
    if (*value < 0.0) {
        return fail(errorMessage, QStringLiteral("%1 must be non-negative").arg(name));
    }
    return true;
}

inline bool parseState(const QString &value, BackendState *state)
{
    if (value == QStringLiteral("preparing")) {
        *state = BackendState::Preparing;
    } else if (value == QStringLiteral("loading_model")) {
        *state = BackendState::LoadingModel;
    } else if (value == QStringLiteral("decoding_audio")) {
        *state = BackendState::DecodingAudio;
    } else if (value == QStringLiteral("transcribing")) {
        *state = BackendState::Transcribing;
    } else if (value == QStringLiteral("diarization")) {
        *state = BackendState::Diarization;
    } else if (value == QStringLiteral("saving_result")) {
        *state = BackendState::SavingResult;
    } else {
        return false;
    }
    return true;
}

inline bool isValidUtf8(const QByteArray &bytes)
{
    const auto *data = reinterpret_cast<const unsigned char *>(bytes.constData());
    qsizetype index = 0;
    while (index < bytes.size()) {
        const unsigned char first = data[index];
        if (first <= 0x7f) {
            ++index;
            continue;
        }

        int continuationCount = 0;
        if (first >= 0xc2 && first <= 0xdf) {
            continuationCount = 1;
        } else if (first >= 0xe0 && first <= 0xef) {
            continuationCount = 2;
        } else if (first >= 0xf0 && first <= 0xf4) {
            continuationCount = 3;
        } else {
            return false;
        }
        if (index + continuationCount >= bytes.size()) {
            return false;
        }

        const unsigned char second = data[index + 1];
        if ((second & 0xc0) != 0x80) {
            return false;
        }
        if ((first == 0xe0 && second < 0xa0)
                || (first == 0xed && second >= 0xa0)
                || (first == 0xf0 && second < 0x90)
                || (first == 0xf4 && second >= 0x90)) {
            return false;
        }
        for (int offset = 2; offset <= continuationCount; ++offset) {
            if ((data[index + offset] & 0xc0) != 0x80) {
                return false;
            }
        }
        index += continuationCount + 1;
    }
    return true;
}

inline bool parseEventLine(const QByteArray &line, BackendEvent *event,
                           QString *errorMessage = nullptr)
{
    if (!event) {
        return fail(errorMessage, QStringLiteral("event output must not be null"));
    }
    if (line.trimmed().isEmpty()) {
        return fail(errorMessage, QStringLiteral("event line must not be empty"));
    }
    if (line.startsWith(QByteArrayLiteral("\xef\xbb\xbf"))) {
        return fail(errorMessage, QStringLiteral("event must not contain a UTF-8 BOM"));
    }
    if (!isValidUtf8(line)) {
        return fail(errorMessage, QStringLiteral("event is not valid UTF-8"));
    }

    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(line, &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        return fail(errorMessage, QStringLiteral("event line must be one JSON object"));
    }
    const QJsonObject object = document.object();
    QString type;
    if (!requiredString(object, QStringLiteral("type"), &type, errorMessage, true)) {
        return false;
    }

    BackendEvent parsed;
    if (type == QStringLiteral("state")) {
        QString value;
        if (!requiredString(object, QStringLiteral("value"), &value, errorMessage, true)) {
            return false;
        }
        if (!parseState(value, &parsed.state)) {
            return fail(errorMessage, QStringLiteral("unknown state value: %1").arg(value));
        }
        parsed.type = BackendEventType::State;
    } else if (type == QStringLiteral("progress")) {
        if (!object.contains(QStringLiteral("value"))) {
            return fail(errorMessage, QStringLiteral("missing required field: value"));
        }
        const QJsonValue value = object.value(QStringLiteral("value"));
        if (value.isNull()) {
            parsed.progress = std::nullopt;
        } else if (value.isDouble()
                   && std::floor(value.toDouble()) == value.toDouble()
                   && value.toDouble() >= 0.0 && value.toDouble() <= 100.0) {
            parsed.progress = static_cast<int>(value.toDouble());
        } else {
            return fail(errorMessage,
                        QStringLiteral("progress value must be an integer between 0 and 100 or null"));
        }

        const bool hasProcessed = object.contains(QStringLiteral("processed_seconds"));
        const bool hasTotal = object.contains(QStringLiteral("total_seconds"));
        if (hasProcessed != hasTotal) {
            return fail(errorMessage,
                        QStringLiteral("processed_seconds and total_seconds must appear together"));
        }
        if (!parsed.progress && hasProcessed) {
            return fail(errorMessage,
                        QStringLiteral("indeterminate progress must not contain time fields"));
        }
        if (hasProcessed) {
            double processed = 0.0;
            double total = 0.0;
            if (!nonNegativeNumber(object, QStringLiteral("processed_seconds"),
                                   &processed, errorMessage)
                    || !nonNegativeNumber(object, QStringLiteral("total_seconds"),
                                          &total, errorMessage)) {
                return false;
            }
            if (processed > total) {
                return fail(errorMessage,
                            QStringLiteral("processed_seconds must not exceed total_seconds"));
            }
            parsed.processedSeconds = processed;
            parsed.totalSeconds = total;
        }
        parsed.type = BackendEventType::Progress;
    } else if (type == QStringLiteral("segment")) {
        if (!nonNegativeNumber(object, QStringLiteral("start"),
                               &parsed.segment.startTime, errorMessage)
                || !nonNegativeNumber(object, QStringLiteral("end"),
                                      &parsed.segment.endTime, errorMessage)) {
            return false;
        }
        if (parsed.segment.endTime < parsed.segment.startTime) {
            return fail(errorMessage, QStringLiteral("segment end must not precede start"));
        }
        if (!object.contains(QStringLiteral("speaker"))) {
            return fail(errorMessage, QStringLiteral("missing required field: speaker"));
        }
        const QJsonValue speaker = object.value(QStringLiteral("speaker"));
        if (speaker.isNull()) {
            parsed.segment.speaker = std::nullopt;
        } else if (speaker.isDouble()
                   && std::floor(speaker.toDouble()) == speaker.toDouble()
                   && speaker.toDouble() >= 1.0
                   && speaker.toDouble() <= std::numeric_limits<int>::max()) {
            parsed.segment.speaker = static_cast<int>(speaker.toDouble());
        } else {
            return fail(errorMessage,
                        QStringLiteral("speaker must be a positive integer or null"));
        }
        if (!requiredString(object, QStringLiteral("text"), &parsed.segment.text,
                            errorMessage, true)) {
            return false;
        }
        parsed.type = BackendEventType::Segment;
    } else if (type == QStringLiteral("completed")) {
        if (!requiredString(object, QStringLiteral("text_file"), &parsed.textFile,
                            errorMessage, true)
                || !requiredString(object, QStringLiteral("srt_file"), &parsed.srtFile,
                                   errorMessage, true)) {
            return false;
        }
        parsed.type = BackendEventType::Completed;
    } else if (type == QStringLiteral("error")) {
        if (!requiredString(object, QStringLiteral("message"), &parsed.errorMessage,
                            errorMessage, true)) {
            return false;
        }
        parsed.type = BackendEventType::Error;
    } else {
        return fail(errorMessage, QStringLiteral("unknown event type: %1").arg(type));
    }

    *event = parsed;
    if (errorMessage) {
        errorMessage->clear();
    }
    return true;
}

class EventStreamValidator
{
public:
    bool accept(const BackendEvent &event, QString *errorMessage = nullptr)
    {
        if (m_terminal != Terminal::None) {
            return fail(errorMessage, QStringLiteral("event received after terminal event"));
        }
        if (event.type == BackendEventType::Completed) {
            m_terminal = Terminal::Completed;
        } else if (event.type == BackendEventType::Error) {
            m_terminal = Terminal::Error;
        }
        return true;
    }

    bool finish(int exitCode, QString *errorMessage = nullptr) const
    {
        if (m_terminal == Terminal::None) {
            return fail(errorMessage,
                        QStringLiteral("stream ended without completed or error event"));
        }
        if (m_terminal == Terminal::Completed && exitCode != 0) {
            return fail(errorMessage, QStringLiteral("completed event requires exit code 0"));
        }
        if (m_terminal == Terminal::Error && exitCode == 0) {
            return fail(errorMessage,
                        QStringLiteral("error event requires a non-zero exit code"));
        }
        return true;
    }

private:
    enum class Terminal
    {
        None,
        Completed,
        Error
    };
    Terminal m_terminal = Terminal::None;
};

} // namespace BackendProtocol

#endif // BACKENDEVENT_H
