from __future__ import annotations

from pathlib import Path
import sys
import unittest


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from backend_protocol import WarningEvent, parse_event_line, validate_stream


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


if __name__ == "__main__":
    unittest.main()
