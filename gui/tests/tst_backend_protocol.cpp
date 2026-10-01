#include "backend/BackendEvent.h"
#include "backend/TranscribeOptions.h"

#include <QFile>
#include <QTest>

class BackendProtocolTest : public QObject
{
    Q_OBJECT

private slots:
    void successFixture();
    void errorFixture();
    void emptySegmentFixture();
    void invalidEventsAreRejected();
    void terminalAndExitRules();
    void transcribeOptionsContract();
};

static QList<QByteArray> fixtureLines(const QString &name)
{
    const QString relativePath = QStringLiteral("../../tests/fixtures/backend_protocol/") + name;
    const QString path = QFINDTESTDATA(qPrintable(relativePath));
    if (path.isEmpty()) {
        return {};
    }

    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return {};
    }
    QList<QByteArray> result;
    for (const QByteArray &line : file.readAll().split('\n')) {
        if (!line.trimmed().isEmpty()) {
            result.append(line);
        }
    }
    return result;
}

void BackendProtocolTest::successFixture()
{
    const QList<QByteArray> lines = fixtureLines(QStringLiteral("success.jsonl"));
    QVERIFY(!lines.isEmpty());

    BackendProtocol::EventStreamValidator validator;
    QList<BackendEvent> events;
    QString error;
    for (const QByteArray &line : lines) {
        BackendEvent event;
        QVERIFY2(BackendProtocol::parseEventLine(line, &event, &error), qPrintable(error));
        QVERIFY2(validator.accept(event, &error), qPrintable(error));
        events.append(event);
    }
    QVERIFY2(validator.finish(0, &error), qPrintable(error));
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
    QCOMPARE(events.constLast().type, BackendEventType::Completed);
    QCOMPARE(events.constLast().textFile, QStringLiteral("C:/Results/회의 결과.txt"));
}

void BackendProtocolTest::errorFixture()
{
    const QList<QByteArray> lines = fixtureLines(QStringLiteral("error.jsonl"));
    QVERIFY(!lines.isEmpty());

    BackendProtocol::EventStreamValidator validator;
    BackendEvent event;
    QString error;
    for (const QByteArray &line : lines) {
        QVERIFY2(BackendProtocol::parseEventLine(line, &event, &error), qPrintable(error));
        QVERIFY2(validator.accept(event, &error), qPrintable(error));
    }
    QVERIFY2(validator.finish(1, &error), qPrintable(error));
    QCOMPARE(event.type, BackendEventType::Error);
    QVERIFY(event.errorMessage.contains(QStringLiteral("model")));
}

void BackendProtocolTest::emptySegmentFixture()
{
    const QList<QByteArray> lines = fixtureLines(QStringLiteral("empty_segments.jsonl"));
    QVERIFY(!lines.isEmpty());

    BackendProtocol::EventStreamValidator validator;
    QString error;
    for (const QByteArray &line : lines) {
        BackendEvent event;
        QVERIFY2(BackendProtocol::parseEventLine(line, &event, &error), qPrintable(error));
        QVERIFY(event.type != BackendEventType::Segment);
        QVERIFY2(validator.accept(event, &error), qPrintable(error));
    }
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

    BackendEvent completed;
    QVERIFY(BackendProtocol::parseEventLine(
        QByteArrayLiteral("{\"type\":\"completed\",\"text_file\":\"C:/a.txt\",\"srt_file\":\"C:/a.srt\"}"),
        &completed, &error));
    BackendProtocol::EventStreamValidator badCompletedExit;
    QVERIFY(badCompletedExit.accept(completed, &error));
    QVERIFY(!badCompletedExit.finish(1, &error));

    BackendEvent failure;
    QVERIFY(BackendProtocol::parseEventLine(
        QByteArrayLiteral("{\"type\":\"error\",\"message\":\"failed\"}"),
        &failure, &error));
    BackendProtocol::EventStreamValidator badErrorExit;
    QVERIFY(badErrorExit.accept(failure, &error));
    QVERIFY(!badErrorExit.finish(0, &error));

    BackendProtocol::EventStreamValidator trailingEvent;
    QVERIFY(trailingEvent.accept(completed, &error));
    BackendEvent state;
    QVERIFY(BackendProtocol::parseEventLine(
        QByteArrayLiteral("{\"type\":\"state\",\"value\":\"preparing\"}"),
        &state, &error));
    QVERIFY(!trailingEvent.accept(state, &error));
}

void BackendProtocolTest::transcribeOptionsContract()
{
    TranscribeOptions options;
    options.inputFile = QStringLiteral("C:/Audio/회의.wav");
    options.modelDirectory = QStringLiteral("C:/Models/whisper");
    options.outputDirectory = QStringLiteral("C:/Results");
    options.device = BackendDevice::Gpu;
    options.diarizationEnabled = true;
    options.speakerCount = 3;

    QString error;
    QVERIFY2(options.isValid(&error), qPrintable(error));
    const QStringList arguments = options.toBridgeArguments();
    QCOMPARE(arguments.at(arguments.indexOf(QStringLiteral("--device")) + 1),
             QStringLiteral("GPU"));
    QCOMPARE(arguments.at(arguments.indexOf(QStringLiteral("--num-speakers")) + 1),
             QStringLiteral("3"));
    QCOMPARE(arguments.at(arguments.indexOf(QStringLiteral("--output-dir")) + 1),
             QStringLiteral("C:/Results"));

    options.speakerCount.reset();
    const QStringList autoArguments = options.toBridgeArguments();
    QCOMPARE(autoArguments.at(autoArguments.indexOf(QStringLiteral("--num-speakers")) + 1),
             QStringLiteral("auto"));

    options.diarizationEnabled = false;
    const QStringList noDiarizationArguments = options.toBridgeArguments();
    QVERIFY(!noDiarizationArguments.contains(QStringLiteral("--diarization")));
    QVERIFY(!noDiarizationArguments.contains(QStringLiteral("--num-speakers")));

    options.speakerCount = 1;
    QVERIFY(!options.isValid(&error));
}

QTEST_APPLESS_MAIN(BackendProtocolTest)

#include "tst_backend_protocol.moc"
