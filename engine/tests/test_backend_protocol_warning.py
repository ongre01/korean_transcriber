from __future__ import annotations

from pathlib import Path
import sys
import unittest


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from backend_protocol import (
    ErrorEvent,
    ProtocolError,
    WarningEvent,
    parse_event_line,
    validate_stream,
)


class BackendProtocolWarningTests(unittest.TestCase):
    def test_warning_event_is_parsed(self) -> None:
        event = parse_event_line('{"type":"warning","message":"initial prompt disabled"}')

        self.assertEqual(event, WarningEvent("initial prompt disabled"))

    def test_warning_event_is_non_terminal(self) -> None:
        events = validate_stream(
            [
                '{"type":"state","value":"preparing"}',
                '{"type":"warning","message":"initial prompt disabled"}',
                '{"type":"completed","text_file":"result.txt","srt_file":"result.srt"}',
            ],
            exit_code=0,
        )

        self.assertIsInstance(events[1], WarningEvent)

    def test_warning_message_must_be_a_nonempty_string(self) -> None:
        cases = (
            ('{"type":"warning"}', "missing required field: message"),
            ('{"type":"warning","message":17}', "message must be a string"),
            ('{"type":"warning","message":"  "}', "message must not be empty"),
        )

        for line, expected in cases:
            with self.subTest(line=line), self.assertRaisesRegex(
                ProtocolError, expected
            ):
                parse_event_line(line)

    def test_warning_can_precede_a_terminal_error(self) -> None:
        events = validate_stream(
            [
                '{"type":"warning","message":"alignment confidence is low"}',
                '{"type":"error","message":"manual review required"}',
            ],
            exit_code=1,
        )

        self.assertEqual(
            events,
            [
                WarningEvent("alignment confidence is low"),
                ErrorEvent("manual review required"),
            ],
        )

    def test_warning_after_terminal_event_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ProtocolError, "event received after terminal completed event"
        ):
            validate_stream(
                [
                    '{"type":"completed","text_file":"result.txt",'
                    '"srt_file":"result.srt"}',
                    '{"type":"warning","message":"too late"}',
                ],
                exit_code=0,
            )


if __name__ == "__main__":
    unittest.main()
