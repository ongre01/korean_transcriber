#include "backend/BackendEvent.h"

#include <QFile>
#include <QList>
#include <QTest>

class BackendProtocolTest : public QObject
{
    Q_OBJECT

private slots:
    void successFixture();
    void errorFixture();
    void emptySegmentFixture();
    void multiInputSuccessFixture();
    void multiInputErrorFixture();
    void warningEventsAreParsedAndNonTerminal();
    void invalidEventsAreRejected();
    void terminalAndExitRules();
    void stateValuesRoundTrip();
    void multiInputArtifactsMustAppearTogether();
    void completionProgressIsRejectedInMultiInputStages();
};

namespace {

QList<QByteArray> fixtureLines(const QString &name)
{
    QFile file(QStringLiteral(BACKEND_PROTOCOL_FIXTURE_DIR "/") + name);
    if (!file.open(QIODevice::ReadOnly)) {
        return {};
    }
    QList<QByteArray> result;
    for (const QByteArray &line : file.readAll().split('\n')) {
        QByteArray trimmed = line;
        if (trimmed.endsWith('\r')) {
            trimmed.chop(1);
        }
        if (!trimmed.trimmed().isEmpty()) {
            result.append(trimmed);
        }
    }
    return result;
}

// Parses and stream-validates every line; returns false with a message on the
// first violation, mirroring BackendProcess::consumeLine().
bool validateStream(const QList<QByteArray> &lines, int exitCode,
                    QList<BackendEvent> *events, QString *error)
{
    BackendProtocol::EventStreamValidator validator;
    for (const QByteArray &line : lines) {
        BackendEvent event;
        if (!BackendProtocol::parseEventLine(line, &event, error)
                || !validator.accept(event, error)) {
            return false;
        }
        events->append(event);
    }
    return validator.finish(exitCode, error);
}

BackendEvent parsed(const QByteArray &line)
{
    BackendEvent event;
    QString error;
    if (!BackendProtocol::parseEventLine(line, &event, &error)) {
        qWarning("%s: %s", line.constData(), qPrintable(error));
    }
    return event;
}

const QList<BackendState> &allStates()
{
    static const QList<BackendState> states = {
        BackendState::Preparing,
        BackendState::LoadingModel,
        BackendState::DecodingAudio,
        BackendState::DetectingSpeech,
        BackendState::AligningAudio,
        BackendState::AssessingChannels,
        BackendState::SelectingChannels,
        BackendState::RenderingMergedAudio,
        BackendState::Transcribing,
        BackendState::Diarization,
        BackendState::SavingResult,
    };
    return states;
}

const QList<BackendState> &multiInputStates()
{
    static const QList<BackendState> states = {
        BackendState::AligningAudio,
        BackendState::AssessingChannels,
        BackendState::SelectingChannels,
        BackendState::RenderingMergedAudio,
    };
    return states;
}

} // namespace

void BackendProtocolTest::successFixture()
{
    const QList<QByteArray> lines = fixtureLines(QStringLiteral("success.jsonl"));
    QVERIFY(!lines.isEmpty());

    QList<BackendEvent> events;
    QString error;
    QVERIFY2(validateStream(lines, 0, &events, &error), qPrintable(error));
    QCOMPARE(events.at(1).type, BackendEventType::Progress);
    QVERIFY(!events.at(1).progress.has_value());

    QList<TranscriptSegment> segments;
    for (const BackendEvent &event : events) {
        if (event.type == BackendEventType::Segment) {
            segments.append(event.segment);
        }
    }
    QCOMPARE(segments.size(), 2);
    QVERIFY(segments.at(0).speaker == std::optional<int>(1));
    QCOMPARE(segments.at(0).text, QStringLiteral("안녕하세요."));
    QVERIFY(!segments.at(1).speaker.has_value());

    const BackendEvent &completed = events.constLast();
    QCOMPARE(completed.type, BackendEventType::Completed);
    QCOMPARE(completed.textFile, QStringLiteral("C:/Results/회의 결과.txt"));
    QCOMPARE(completed.srtFile, QStringLiteral("C:/Results/회의 결과.srt"));
    QVERIFY(!completed.hasMultiInputResult());
    QVERIFY(completed.alignmentFile.isEmpty());
    QVERIFY(completed.selectionFile.isEmpty());
}

void BackendProtocolTest::errorFixture()
{
    const QList<QByteArray> lines = fixtureLines(QStringLiteral("error.jsonl"));
    QVERIFY(!lines.isEmpty());

    QList<BackendEvent> events;
    QString error;
    QVERIFY2(validateStream(lines, 1, &events, &error), qPrintable(error));
    QCOMPARE(events.constLast().type, BackendEventType::Error);
    QVERIFY(events.constLast().errorMessage.contains(QStringLiteral("model")));
}

void BackendProtocolTest::emptySegmentFixture()
{
    const QList<QByteArray> lines = fixtureLines(QStringLiteral("empty_segments.jsonl"));
    QVERIFY(!lines.isEmpty());

    QList<BackendEvent> events;
    QString error;
    QVERIFY2(validateStream(lines, 0, &events, &error), qPrintable(error));
    for (const BackendEvent &event : events) {
        QVERIFY(event.type != BackendEventType::Segment);
    }
}

void BackendProtocolTest::multiInputSuccessFixture()
{
    const QList<QByteArray> lines = fixtureLines(QStringLiteral("multi_input_success.jsonl"));
    QVERIFY(!lines.isEmpty());

    QList<BackendEvent> events;
    QString error;
    QVERIFY2(validateStream(lines, 0, &events, &error), qPrintable(error));

    QList<BackendState> seenStates;
    for (qsizetype index = 0; index + 1 < events.size(); ++index) {
        const BackendEvent &event = events.at(index);
        if (event.type != BackendEventType::State) {
            continue;
        }
        seenStates.append(event.state);
        if (multiInputStates().contains(event.state)) {
            const BackendEvent &next = events.at(index + 1);
            QCOMPARE(next.type, BackendEventType::Progress);
            QVERIFY(!next.progress.has_value());
            QVERIFY(!next.processedSeconds.has_value());
        }
    }
    for (BackendState state : multiInputStates()) {
        QVERIFY2(seenStates.contains(state),
                 qPrintable(BackendProtocol::stateValue(state)));
    }

    const BackendEvent &completed = events.constLast();
    QCOMPARE(completed.type, BackendEventType::Completed);
    QVERIFY(completed.hasMultiInputResult());
    QCOMPARE(completed.textFile, QStringLiteral("C:/Results/회의 다중.txt"));
    QCOMPARE(completed.srtFile, QStringLiteral("C:/Results/회의 다중.srt"));
    QCOMPARE(completed.mergedAudioFile, QStringLiteral("C:/Results/merged_16k_mono.wav"));
    QCOMPARE(completed.alignmentFile, QStringLiteral("C:/Results/alignment.json"));
    QCOMPARE(completed.selectionFile, QStringLiteral("C:/Results/channel_selection.json"));
}

void BackendProtocolTest::multiInputErrorFixture()
{
    const QList<QByteArray> lines = fixtureLines(QStringLiteral("multi_input_error.jsonl"));
    QVERIFY(!lines.isEmpty());

    QList<BackendEvent> events;
    QString error;
    QVERIFY2(validateStream(lines, 1, &events, &error), qPrintable(error));
    QCOMPARE(events.at(4).type, BackendEventType::State);
    QCOMPARE(events.at(4).state, BackendState::AligningAudio);
    QCOMPARE(events.constLast().type, BackendEventType::Error);

    QList<BackendEvent> ignored;
    QVERIFY(!validateStream(lines, 0, &ignored, &error));
}

void BackendProtocolTest::warningEventsAreParsedAndNonTerminal()
{
    BackendEvent warning;
    QString error;
    QVERIFY2(BackendProtocol::parseEventLine(
                 QByteArrayLiteral(
                     "{\"type\":\"warning\",\"message\":\"정렬 신뢰도가 낮습니다.\"}"),
                 &warning, &error),
             qPrintable(error));
    QCOMPARE(warning.type, BackendEventType::Warning);
    QCOMPARE(warning.warningMessage, QStringLiteral("정렬 신뢰도가 낮습니다."));

    BackendEvent blankWarning;
    QVERIFY(!BackendProtocol::parseEventLine(
        QByteArrayLiteral("{\"type\":\"warning\",\"message\":\"   \"}"),
        &blankWarning, &error));
    QVERIFY(!error.isEmpty());

    const BackendEvent state = parsed(QByteArrayLiteral(
        "{\"type\":\"state\",\"value\":\"aligning_audio\"}"));
    const BackendEvent completed = parsed(QByteArrayLiteral(
        "{\"type\":\"completed\",\"text_file\":\"C:/a.txt\","
        "\"srt_file\":\"C:/a.srt\"}"));
    BackendProtocol::EventStreamValidator validator;
    QVERIFY(validator.accept(state, &error));
    QVERIFY(validator.accept(warning, &error));
    QVERIFY(validator.accept(completed, &error));
    QVERIFY2(validator.finish(0, &error), qPrintable(error));
}

void BackendProtocolTest::invalidEventsAreRejected()
{
    const QList<QByteArray> lines = fixtureLines(QStringLiteral("invalid_events.jsonl"));
    QVERIFY(!lines.isEmpty());

    for (const QByteArray &line : lines) {
        BackendEvent event;
        QString error;
        QVERIFY2(!BackendProtocol::parseEventLine(line, &event, &error), line.constData());
        QVERIFY(!error.isEmpty());
    }
    BackendEvent event;
    QString error;
    QVERIFY(!BackendProtocol::parseEventLine(
        QByteArray("{\"type\":\"error\",\"message\":\"") + QByteArray("\xc3", 1)
            + QByteArrayLiteral("\"}"),
        &event, &error));
    QVERIFY(!BackendProtocol::parseEventLine(
        QByteArrayLiteral("\xef\xbb\xbf{\"type\":\"error\",\"message\":\"failed\"}"),
        &event, &error));
}

void BackendProtocolTest::terminalAndExitRules()
{
    QString error;
    BackendProtocol::EventStreamValidator missingTerminal;
    QVERIFY(!missingTerminal.finish(0, &error));

    const BackendEvent completed = parsed(QByteArrayLiteral(
        "{\"type\":\"completed\",\"text_file\":\"C:/a.txt\",\"srt_file\":\"C:/a.srt\"}"));
    QCOMPARE(completed.type, BackendEventType::Completed);
    BackendProtocol::EventStreamValidator badCompletedExit;
    QVERIFY(badCompletedExit.accept(completed, &error));
    QVERIFY(!badCompletedExit.finish(1, &error));

    const BackendEvent failure = parsed(QByteArrayLiteral(
        "{\"type\":\"error\",\"message\":\"failed\"}"));
    QCOMPARE(failure.type, BackendEventType::Error);
    BackendProtocol::EventStreamValidator badErrorExit;
    QVERIFY(badErrorExit.accept(failure, &error));
    QVERIFY(!badErrorExit.finish(0, &error));

    const BackendEvent state = parsed(QByteArrayLiteral(
        "{\"type\":\"state\",\"value\":\"preparing\"}"));
    BackendProtocol::EventStreamValidator trailingEvent;
    QVERIFY(trailingEvent.accept(completed, &error));
    QVERIFY(!trailingEvent.accept(state, &error));
    QVERIFY(!trailingEvent.accept(failure, &error));

    BackendProtocol::EventStreamValidator errorThenCompleted;
    QVERIFY(errorThenCompleted.accept(failure, &error));
    QVERIFY(!errorThenCompleted.accept(completed, &error));

    const BackendEvent fullProgress = parsed(QByteArrayLiteral(
        "{\"type\":\"progress\",\"value\":100}"));
    BackendProtocol::EventStreamValidator prematureProgress;
    QVERIFY(prematureProgress.accept(state, &error));
    QVERIFY(!prematureProgress.accept(fullProgress, &error));

    const BackendEvent savingState = parsed(QByteArrayLiteral(
        "{\"type\":\"state\",\"value\":\"saving_result\"}"));
    BackendProtocol::EventStreamValidator savedProgress;
    QVERIFY(savedProgress.accept(savingState, &error));
    QVERIFY(savedProgress.accept(fullProgress, &error));
}

void BackendProtocolTest::stateValuesRoundTrip()
{
    for (BackendState state : allStates()) {
        const QString value = BackendProtocol::stateValue(state);
        QVERIFY(!value.isEmpty());

        BackendEvent event;
        QString error;
        const QByteArray line = QByteArrayLiteral("{\"type\":\"state\",\"value\":\"")
            + value.toUtf8() + QByteArrayLiteral("\"}");
        QVERIFY2(BackendProtocol::parseEventLine(line, &event, &error), qPrintable(error));
        QCOMPARE(event.type, BackendEventType::State);
        QCOMPARE(event.state, state);
    }
    QCOMPARE(BackendProtocol::stateValue(BackendState::AligningAudio),
             QStringLiteral("aligning_audio"));
    QCOMPARE(BackendProtocol::stateValue(BackendState::AssessingChannels),
             QStringLiteral("assessing_channels"));
    QCOMPARE(BackendProtocol::stateValue(BackendState::SelectingChannels),
             QStringLiteral("selecting_channels"));
    QCOMPARE(BackendProtocol::stateValue(BackendState::RenderingMergedAudio),
             QStringLiteral("rendering_merged_audio"));
}

void BackendProtocolTest::multiInputArtifactsMustAppearTogether()
{
    const QByteArray prefix = QByteArrayLiteral(
        "{\"type\":\"completed\",\"text_file\":\"C:/a.txt\",\"srt_file\":\"C:/a.srt\"");
    const QByteArray merged = QByteArrayLiteral(",\"merged_audio_file\":\"C:/m.wav\"");
    const QByteArray alignment = QByteArrayLiteral(",\"alignment_file\":\"C:/a.json\"");
    const QByteArray selection = QByteArrayLiteral(",\"selection_file\":\"C:/s.json\"");

    const QList<QByteArray> partial = {
        prefix + merged + "}",
        prefix + alignment + "}",
        prefix + selection + "}",
        prefix + merged + alignment + "}",
        prefix + alignment + selection + "}",
        prefix + merged + selection + "}",
    };
    for (const QByteArray &line : partial) {
        BackendEvent event;
        QString error;
        QVERIFY2(!BackendProtocol::parseEventLine(line, &event, &error), line.constData());
        QVERIFY(error.contains(QStringLiteral("must appear together")));
    }

    const BackendEvent complete = parsed(prefix + merged + alignment + selection + "}");
    QCOMPARE(complete.type, BackendEventType::Completed);
    QVERIFY(complete.hasMultiInputResult());
    QCOMPARE(complete.mergedAudioFile, QStringLiteral("C:/m.wav"));
    QCOMPARE(complete.alignmentFile, QStringLiteral("C:/a.json"));
    QCOMPARE(complete.selectionFile, QStringLiteral("C:/s.json"));

    const BackendEvent futureField = parsed(prefix + ",\"future_field\":1}");
    QCOMPARE(futureField.type, BackendEventType::Completed);
    QVERIFY(!futureField.hasMultiInputResult());
}

void BackendProtocolTest::completionProgressIsRejectedInMultiInputStages()
{
    const BackendEvent fullProgress = parsed(QByteArrayLiteral(
        "{\"type\":\"progress\",\"value\":100}"));
    const BackendEvent indeterminate = parsed(QByteArrayLiteral(
        "{\"type\":\"progress\",\"value\":null}"));
    for (BackendState state : multiInputStates()) {
        BackendEvent stateEvent;
        stateEvent.type = BackendEventType::State;
        stateEvent.state = state;

        QString error;
        BackendProtocol::EventStreamValidator validator;
        QVERIFY(validator.accept(stateEvent, &error));
        QVERIFY(validator.accept(indeterminate, &error));
        QVERIFY2(!validator.accept(fullProgress, &error),
                 qPrintable(BackendProtocol::stateValue(state)));
    }
}

QTEST_APPLESS_MAIN(BackendProtocolTest)

#include "tst_backend_protocol.moc"
