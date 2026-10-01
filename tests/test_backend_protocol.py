from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from backend_protocol import (  # noqa: E402
    CompletedEvent,
    ErrorEvent,
    ProgressEvent,
    ProtocolError,
    SegmentEvent,
    parse_event_line,
    validate_stream,
)


FIXTURES = ROOT / "tests" / "fixtures" / "backend_protocol"


def fixture_lines(name: str) -> list[bytes]:
    return [line for line in (FIXTURES / name).read_bytes().splitlines() if line]


class BackendProtocolTest(unittest.TestCase):
    def test_success_fixture(self):
        events = validate_stream(fixture_lines("success.jsonl"), exit_code=0)
        self.assertIsInstance(events[1], ProgressEvent)
        self.assertIsNone(events[1].value)

        segments = [event for event in events if isinstance(event, SegmentEvent)]
        self.assertEqual([segment.speaker for segment in segments], [1, None])
        self.assertEqual(segments[0].text, "안녕하세요.")
        self.assertIsInstance(events[-1], CompletedEvent)
        self.assertEqual(events[-1].text_file, "C:/Results/회의 결과.txt")

    def test_error_fixture(self):
        events = validate_stream(fixture_lines("error.jsonl"), exit_code=1)
        self.assertIsInstance(events[-1], ErrorEvent)
        self.assertIn("model", events[-1].message)

    def test_empty_segment_fixture(self):
        events = validate_stream(fixture_lines("empty_segments.jsonl"), exit_code=0)
        self.assertFalse(any(isinstance(event, SegmentEvent) for event in events))

    def test_invalid_events_are_rejected(self):
        for line in fixture_lines("invalid_events.jsonl"):
            with self.subTest(line=line):
                with self.assertRaises(ProtocolError):
                    parse_event_line(line)
        with self.assertRaises(ProtocolError):
            parse_event_line(b'{"type":"error","message":"\xc3"}')
        with self.assertRaises(ProtocolError):
            parse_event_line(b'\xef\xbb\xbf{"type":"error","message":"failed"}')

    def test_terminal_and_exit_rules(self):
        completed = parse_event_line(
            b'{"type":"completed","text_file":"C:/a.txt","srt_file":"C:/a.srt"}'
        )
        with self.assertRaises(ProtocolError):
            validate_stream([], exit_code=0)
        with self.assertRaises(ProtocolError):
            validate_stream([
                b'{"type":"completed","text_file":"C:/a.txt","srt_file":"C:/a.srt"}'
            ], exit_code=1)
        with self.assertRaises(ProtocolError):
            validate_stream([
                b'{"type":"error","message":"failed"}'
            ], exit_code=0)
        with self.assertRaises(ProtocolError):
            validate_stream([
                b'{"type":"completed","text_file":"C:/a.txt","srt_file":"C:/a.srt"}',
                b'{"type":"state","value":"preparing"}',
            ], exit_code=0)
        self.assertIsInstance(completed, CompletedEvent)


if __name__ == "__main__":
    unittest.main()
