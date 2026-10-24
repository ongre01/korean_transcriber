from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import unittest


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from backend_protocol import (  # noqa: E402
    MULTI_INPUT_COMPLETED_FIELDS,
    CompletedEvent,
    ErrorEvent,
    ProgressEvent,
    ProtocolError,
    SegmentEvent,
    StateEvent,
    StateValue,
    WarningEvent,
    parse_event_line,
    validate_stream,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "backend_protocol"
PROTOCOL_DOCUMENT = ENGINE_DIR / "backend_protocol.md"
QT_EVENT_HEADER = ENGINE_DIR.parent / "gui" / "backend" / "BackendEvent.h"
MULTI_INPUT_STATES = {
    StateValue.ALIGNING_AUDIO,
    StateValue.ASSESSING_CHANNELS,
    StateValue.SELECTING_CHANNELS,
    StateValue.RENDERING_MERGED_AUDIO,
}
COMPLETED = b'{"type":"completed","text_file":"C:/a.txt","srt_file":"C:/a.srt"}'
OMIT = object()


def fixture_lines(name: str) -> list[bytes]:
    return [line for line in (FIXTURES / name).read_bytes().splitlines() if line]


def multi_input_completed(**overrides) -> bytes:
    """Encode a multi-input completed event; an ``OMIT`` override drops a field."""

    payload: dict[str, object] = {
        "type": "completed",
        "text_file": "C:/Results/meeting.txt",
        "srt_file": "C:/Results/meeting.srt",
        "merged_audio_file": "C:/Results/merged_16k_mono.wav",
        "alignment_file": "C:/Results/alignment.json",
        "selection_file": "C:/Results/channel_selection.json",
    }
    payload.update(overrides)
    payload = {name: value for name, value in payload.items() if value is not OMIT}
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class BackendProtocolFixtureTests(unittest.TestCase):
    def test_success_fixture(self) -> None:
        events = validate_stream(fixture_lines("success.jsonl"), exit_code=0)
        self.assertIsInstance(events[1], ProgressEvent)
        self.assertIsNone(events[1].value)

        segments = [event for event in events if isinstance(event, SegmentEvent)]
        self.assertEqual([segment.speaker for segment in segments], [1, None])
        self.assertEqual(segments[0].text, "안녕하세요.")
        self.assertIsInstance(events[-1], CompletedEvent)
        self.assertEqual(events[-1].text_file, "C:/Results/회의 결과.txt")
        self.assertFalse(events[-1].is_multi_input)
        self.assertIsNone(events[-1].merged_audio_file)
        self.assertIsNone(events[-1].alignment_file)
        self.assertIsNone(events[-1].selection_file)

    def test_error_fixture(self) -> None:
        events = validate_stream(fixture_lines("error.jsonl"), exit_code=1)
        self.assertIsInstance(events[-1], ErrorEvent)
        self.assertIn("model", events[-1].message)

    def test_empty_segment_fixture(self) -> None:
        events = validate_stream(fixture_lines("empty_segments.jsonl"), exit_code=0)
        self.assertFalse(any(isinstance(event, SegmentEvent) for event in events))

    def test_multi_input_success_fixture(self) -> None:
        events = validate_stream(fixture_lines("multi_input_success.jsonl"), exit_code=0)

        states = [event.value for event in events if isinstance(event, StateEvent)]
        self.assertTrue(MULTI_INPUT_STATES.issubset(states))
        for index, event in enumerate(events[:-1]):
            if isinstance(event, StateEvent) and event.value in MULTI_INPUT_STATES:
                with self.subTest(state=event.value):
                    self.assertEqual(events[index + 1], ProgressEvent(None))
        self.assertTrue(any(isinstance(event, WarningEvent) for event in events))

        completed = events[-1]
        self.assertIsInstance(completed, CompletedEvent)
        self.assertTrue(completed.is_multi_input)
        self.assertEqual(completed.text_file, "C:/Results/회의 다중.txt")
        self.assertEqual(completed.srt_file, "C:/Results/회의 다중.srt")
        self.assertEqual(completed.merged_audio_file, "C:/Results/merged_16k_mono.wav")
        self.assertEqual(completed.alignment_file, "C:/Results/alignment.json")
        self.assertEqual(completed.selection_file, "C:/Results/channel_selection.json")

    def test_multi_input_error_fixture(self) -> None:
        events = validate_stream(fixture_lines("multi_input_error.jsonl"), exit_code=1)
        self.assertIn(StateEvent(StateValue.ALIGNING_AUDIO), events)
        self.assertIsInstance(events[-1], ErrorEvent)
        self.assertEqual(sum(isinstance(event, ErrorEvent) for event in events), 1)

    def test_invalid_events_are_rejected(self) -> None:
        for line in fixture_lines("invalid_events.jsonl"):
            with self.subTest(line=line):
                with self.assertRaises(ProtocolError):
                    parse_event_line(line)
        with self.assertRaises(ProtocolError):
            parse_event_line(b'{"type":"error","message":"\xc3"}')
        with self.assertRaises(ProtocolError):
            parse_event_line(b'\xef\xbb\xbf{"type":"error","message":"failed"}')


class BackendProtocolMultiInputTests(unittest.TestCase):
    def test_multi_input_states_are_parsed(self) -> None:
        for state in MULTI_INPUT_STATES:
            with self.subTest(state=state):
                event = parse_event_line(f'{{"type":"state","value":"{state.value}"}}')
                self.assertEqual(event, StateEvent(state))

    def test_indeterminate_progress_is_allowed_in_every_state(self) -> None:
        for state in StateValue:
            with self.subTest(state=state):
                events = validate_stream([
                    f'{{"type":"state","value":"{state.value}"}}',
                    '{"type":"progress","value":null}',
                    '{"type":"error","message":"stopped"}',
                ], exit_code=1)
                self.assertEqual(events[1], ProgressEvent(None))

    def test_completion_progress_is_rejected_in_multi_input_stages(self) -> None:
        for state in MULTI_INPUT_STATES:
            with self.subTest(state=state):
                with self.assertRaises(ProtocolError):
                    validate_stream([
                        f'{{"type":"state","value":"{state.value}"}}',
                        '{"type":"progress","value":100}',
                        COMPLETED,
                    ], exit_code=0)

    def test_multi_input_completed_fields_must_appear_together(self) -> None:
        for missing in MULTI_INPUT_COMPLETED_FIELDS:
            with self.subTest(missing=missing):
                with self.assertRaises(ProtocolError) as raised:
                    parse_event_line(multi_input_completed(**{missing: OMIT}))
                self.assertIn("must appear together", str(raised.exception))

    def test_multi_input_completed_fields_must_be_non_empty_strings(self) -> None:
        for name in MULTI_INPUT_COMPLETED_FIELDS:
            for value in (None, "", "   ", 42, True, ["C:/a"]):
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ProtocolError):
                        parse_event_line(multi_input_completed(**{name: value}))

    def test_multi_input_completed_keeps_text_and_srt_required(self) -> None:
        for name in ("text_file", "srt_file"):
            with self.subTest(name=name):
                with self.assertRaises(ProtocolError):
                    parse_event_line(multi_input_completed(**{name: OMIT}))

    def test_unknown_completed_fields_remain_ignored(self) -> None:
        event = parse_event_line(
            b'{"type":"completed","text_file":"C:/a.txt","srt_file":"C:/a.srt",'
            b'"future_field":"ignored"}'
        )
        self.assertEqual(event, CompletedEvent("C:/a.txt", "C:/a.srt"))


class BackendProtocolStreamTests(unittest.TestCase):
    def test_terminal_and_exit_rules(self) -> None:
        with self.assertRaises(ProtocolError):
            validate_stream([], exit_code=0)
        with self.assertRaises(ProtocolError):
            validate_stream([COMPLETED], exit_code=1)
        with self.assertRaises(ProtocolError):
            validate_stream([b'{"type":"error","message":"failed"}'], exit_code=0)
        with self.assertRaises(ProtocolError):
            validate_stream([COMPLETED, b'{"type":"state","value":"preparing"}'], exit_code=0)
        with self.assertRaises(ProtocolError):
            validate_stream([COMPLETED, b'{"type":"error","message":"late"}'], exit_code=0)
        with self.assertRaises(ProtocolError):
            validate_stream([b'{"type":"error","message":"failed"}', COMPLETED], exit_code=1)
        with self.assertRaises(ProtocolError):
            validate_stream([
                multi_input_completed(),
                b'{"type":"warning","message":"late"}',
            ], exit_code=0)

    def test_completion_progress_requires_saving_result_stage(self) -> None:
        with self.assertRaises(ProtocolError):
            validate_stream([
                b'{"type":"state","value":"transcribing"}',
                b'{"type":"progress","value":100}',
                COMPLETED,
            ], exit_code=0)

        events = validate_stream([
            b'{"type":"state","value":"saving_result"}',
            b'{"type":"progress","value":100}',
            COMPLETED,
        ], exit_code=0)
        self.assertEqual(events[1].value, 100)


class BackendProtocolContractSyncTests(unittest.TestCase):
    """Keep the Python schema, the contract document and the Qt parser in step."""

    def test_document_state_table_matches_schema(self) -> None:
        rows = re.findall(
            r"^\| `([a-z_]+)` \| (공통|다중) \|",
            PROTOCOL_DOCUMENT.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
        self.assertEqual({value for value, _mode in rows}, {state.value for state in StateValue})
        self.assertEqual(len(rows), len(StateValue))
        self.assertEqual(
            {value for value, mode in rows if mode == "다중"},
            {state.value for state in MULTI_INPUT_STATES},
        )

    def test_document_lists_multi_input_completed_fields(self) -> None:
        document = PROTOCOL_DOCUMENT.read_text(encoding="utf-8")
        for name in MULTI_INPUT_COMPLETED_FIELDS:
            with self.subTest(name=name):
                self.assertRegex(document, rf"(?m)^\| `{name}` \|")
                self.assertIn(f'"{name}":', document)

    @unittest.skipUnless(QT_EVENT_HEADER.is_file(), "Qt sources are not available")
    def test_qt_parser_matches_schema(self) -> None:
        header = QT_EVENT_HEADER.read_text(encoding="utf-8")
        parse_state = re.search(r"inline bool parseState\(.*?\n}\n", header, flags=re.DOTALL)
        state_value = re.search(r"inline QString stateValue\(.*?\n}\n", header, flags=re.DOTALL)
        self.assertIsNotNone(parse_state)
        self.assertIsNotNone(state_value)
        expected = {state.value for state in StateValue}
        self.assertEqual(
            set(re.findall(r'value == QStringLiteral\("([a-z_]+)"\)', parse_state.group(0))),
            expected,
        )
        self.assertEqual(
            set(re.findall(r'return QStringLiteral\("([a-z_]+)"\)', state_value.group(0))),
            expected,
        )
        for name in MULTI_INPUT_COMPLETED_FIELDS:
            with self.subTest(name=name):
                self.assertIn(f'QStringLiteral("{name}")', header)


class FailureClassificationContractTests(unittest.TestCase):
    """The documented error prefixes, the bridge and the Qt classifier agree."""

    DOCUMENT_KINDS = {
        "동기화 실패": "Synchronization",
        "디코드 실패": "Decode",
        "변환 실패(다중 전처리)": "Conversion",
        "모델 오류": "Model",
        "결과 저장 실패": "ResultSave",
    }

    def documented_prefixes(self) -> set[tuple[str, str]]:
        document = PROTOCOL_DOCUMENT.read_text(encoding="utf-8")
        section = document.split("## 실패 분류", 1)[1].split("\n## ", 1)[0]
        prefixes = set()
        for category, cells in re.findall(r"(?m)^\| ([^|`]+?) \| (`.+`) \|$", section):
            kind = self.DOCUMENT_KINDS[category]
            prefixes.update((kind, prefix) for prefix in re.findall(r"`([^`]+)`", cells))
        return prefixes

    def test_document_covers_every_failure_kind(self) -> None:
        kinds = {kind for kind, _prefix in self.documented_prefixes()}
        self.assertEqual(kinds, set(self.DOCUMENT_KINDS.values()))

    def test_bridge_produces_every_documented_prefix(self) -> None:
        import backend_bridge

        source = (ENGINE_DIR / "backend_bridge.py").read_text(encoding="utf-8")
        stage_prefixes = set(backend_bridge.MULTI_INPUT_STAGE_FAILURES.values())
        for _kind, prefix in self.documented_prefixes():
            with self.subTest(prefix=prefix):
                self.assertTrue(prefix in stage_prefixes or f'"{prefix}' in source)

    @unittest.skipUnless(
        (QT_EVENT_HEADER.parent / "BackendFailure.h").is_file(),
        "Qt sources are not available",
    )
    def test_qt_classifier_uses_the_documented_prefixes(self) -> None:
        header = (QT_EVENT_HEADER.parent / "BackendFailure.h").read_text(encoding="utf-8")
        qt_prefixes = set(
            re.findall(
                r'\{BackendFailureKind::(\w+),\s*QLatin1String\("([^"]+)"\)\}',
                header,
            )
        )
        self.assertEqual(qt_prefixes, self.documented_prefixes())


if __name__ == "__main__":
    unittest.main()
