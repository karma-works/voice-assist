import unittest
import asyncio
import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pipecat.frames.frames import InterruptionFrame, LLMFullResponseEndFrame, TTSAudioRawFrame

from src.main import app
from src.booking_state import BookingSession
from src import calendar_service
from src.observability import TraceLogger, hash_token, redact_value
from src.processors import AudioInputProcessor, InterruptionProcessor, TextOutputProcessor
from src.session import BrowserWebSocketSerializer, _dispatch_tool


class ProcessorTests(unittest.TestCase):
    def test_audio_processor_rejects_odd_pcm_payloads(self):
        processor = AudioInputProcessor()

        self.assertTrue(processor.observe_pcm16(b"\x00\x00"))
        self.assertFalse(processor.observe_pcm16(b"\x00"))
        self.assertEqual(processor.metrics.frames, 2)
        self.assertEqual(processor.metrics.dropped_frames, 1)

    def test_interruption_classifier_distinguishes_backchannels(self):
        processor = InterruptionProcessor()

        self.assertEqual(processor.classify_text("uh-huh"), "backchannel")
        self.assertEqual(processor.classify_text("stop"), "interruption")
        self.assertEqual(processor.classify_text("Tuesday works"), "speech")

    def test_text_processor_strips_markdown_and_formats_phone(self):
        processor = TextOutputProcessor()

        self.assertEqual(processor.normalize("**Hi** [there](https://example.com)"), "Hi there")
        self.assertEqual(processor.format_phone_readback("+491711234567"), "plus 4 9, 1 7 1 1, 2 3 4, 5 6 7")
        self.assertEqual(processor.format_phone_readback("01711234567"), "0 1 7 1, 1 2 3 4, 5 6 7")


class BrowserWebSocketSerializerTests(unittest.TestCase):
    def test_binary_browser_audio_deserializes_to_pipecat_audio_frame(self):
        async def run():
            serializer = BrowserWebSocketSerializer().serializer

            frame = await serializer.deserialize(b"\x00\x00")

            self.assertEqual(type(frame).__name__, "InputAudioRawFrame")
            self.assertEqual(frame.sample_rate, 16000)
            self.assertEqual(frame.num_channels, 1)
            self.assertEqual(frame.audio, b"\x00\x00")

        asyncio.run(run())

    def test_interrupt_control_message_round_trips_to_browser_json(self):
        async def run():
            serializer = BrowserWebSocketSerializer().serializer

            frame = await serializer.deserialize(json.dumps({"type": "interrupt"}))
            payload = await serializer.serialize(frame)

            self.assertIsInstance(frame, InterruptionFrame)
            self.assertEqual(json.loads(payload), {"type": "interrupted"})

        asyncio.run(run())

    def test_output_audio_and_turn_complete_match_browser_protocol(self):
        async def run():
            serializer = BrowserWebSocketSerializer().serializer

            audio = await serializer.serialize(
                TTSAudioRawFrame(audio=b"\x01\x02", sample_rate=24000, num_channels=1)
            )
            complete = await serializer.serialize(LLMFullResponseEndFrame())

            self.assertEqual(audio, b"\x01\x02")
            self.assertEqual(json.loads(complete), {"type": "turn_complete"})

        asyncio.run(run())


class RouteTests(unittest.TestCase):
    def test_health_route(self):
        client = TestClient(app)

        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


class CalendarServiceTests(unittest.TestCase):
    def test_create_event_returns_verified_success(self):
        class ExecuteResult:
            def __init__(self, payload):
                self.payload = payload

            def execute(self):
                return self.payload

        class Events:
            def insert(self, **_kwargs):
                return ExecuteResult({"id": "evt_123", "htmlLink": "https://calendar/event"})

            def get(self, **_kwargs):
                return ExecuteResult({"start": {"dateTime": "2026-06-08T09:00:00+02:00"}})

        class Service:
            def events(self):
                return Events()

        async def run():
            with patch.object(calendar_service, "_get_service", return_value=Service()):
                result = await calendar_service.create_event(
                    title="Meeting",
                    start_iso="2026-06-08T09:00:00+02:00",
                    end_iso="2026-06-08T09:30:00+02:00",
                    visitor_name="Ada Lovelace",
                    topic="Planning",
                )

            self.assertTrue(result["success"])
            self.assertEqual(result["event_id"], "evt_123")
            self.assertEqual(result["verified_start"], "2026-06-08T09:00:00+02:00")

        asyncio.run(run())

    def test_create_event_treats_utc_offset_as_local_berlin_wall_time(self):
        captured = {}

        class ExecuteResult:
            def __init__(self, payload):
                self.payload = payload

            def execute(self):
                return self.payload

        class Events:
            def insert(self, **kwargs):
                captured["body"] = kwargs["body"]
                return ExecuteResult({"id": "evt_123", "htmlLink": "https://calendar/event"})

            def get(self, **_kwargs):
                return ExecuteResult({"start": {"dateTime": captured["body"]["start"]["dateTime"]}})

        class Service:
            def events(self):
                return Events()

        async def run():
            with patch.object(calendar_service, "_get_service", return_value=Service()):
                result = await calendar_service.create_event(
                    title="Meeting",
                    start_iso="2026-01-12T16:00:00+00:00",
                    end_iso="2026-01-12T17:00:00+00:00",
                    visitor_name="Ada Lovelace",
                    topic="Planning",
                )

            self.assertEqual(captured["body"]["start"]["dateTime"], "2026-01-12T16:00:00+01:00")
            self.assertEqual(captured["body"]["end"]["dateTime"], "2026-01-12T17:00:00+01:00")
            self.assertEqual(result["verified_start"], "2026-01-12T16:00:00+01:00")

        asyncio.run(run())

    def test_available_slots_are_local_berlin_and_allow_1500_business_start(self):
        async def run():
            start = calendar_service.BERLIN_TZ.localize(datetime(2026, 1, 12, 14, 30))
            end = calendar_service.BERLIN_TZ.localize(datetime(2026, 1, 12, 16, 0))
            busy = [
                (
                    calendar_service.BERLIN_TZ.localize(datetime(2026, 1, 12, 13, 30)),
                    calendar_service.BERLIN_TZ.localize(datetime(2026, 1, 12, 14, 30)),
                )
            ]
            with patch.object(calendar_service, "get_busy_times", return_value=busy):
                slots = await calendar_service.get_available_slots(
                    start,
                    end,
                    duration_minutes=30,
                    slot_type="business",
                    buffer_minutes=15,
                )

            starts = [slot["start"] for slot in slots]
            self.assertIn("2026-01-12T15:00:00+01:00", starts)
            self.assertNotIn("2026-01-12T14:30:00+01:00", starts)

        asyncio.run(run())

    def test_dispatch_tool_errors_are_explicit_failures(self):
        async def run():
            result = await _dispatch_tool("unknown_tool", {})

            self.assertFalse(result["success"])
            self.assertEqual(result["error"], "Unknown tool: unknown_tool")

        asyncio.run(run())

    def test_book_meeting_rejects_unprepared_calendar_write(self):
        async def run():
            booking_state = BookingSession()

            result = await _dispatch_tool(
                "book_meeting",
                {
                    "booking_operation_id": "missing",
                    "explicit_confirmation": True,
                },
                booking_state=booking_state,
            )

            self.assertFalse(result["success"])
            self.assertEqual(result["state"], "failed_recoverable")
            self.assertIn("prepare_booking", result["error"])

        asyncio.run(run())

    def test_prepare_then_book_meeting_is_verified_and_idempotent(self):
        calls = []

        async def fake_create_event(**kwargs):
            calls.append(kwargs)
            return {
                "success": True,
                "event_id": "evt_123",
                "calendar_id": "primary",
                "verified_start": kwargs["start_iso"],
            }

        async def run():
            booking_state = BookingSession()
            prepare_result = await _dispatch_tool(
                "prepare_booking",
                {
                    "title": "Meeting with Ada Lovelace",
                    "start_iso": "2026-06-08T09:00:00+02:00",
                    "end_iso": "2026-06-08T09:30:00+02:00",
                    "visitor_name": "Ada Lovelace",
                    "topic": "Planning",
                    "phone_collection_declined": True,
                    "meeting_type": "business",
                },
                booking_state=booking_state,
            )

            self.assertTrue(prepare_result["success"])
            self.assertEqual(prepare_result["state"], "awaiting_explicit_confirmation")
            operation_id = prepare_result["booking_operation_id"]

            with patch.object(calendar_service, "create_event", side_effect=fake_create_event):
                first = await _dispatch_tool(
                    "book_meeting",
                    {
                        "booking_operation_id": operation_id,
                        "explicit_confirmation": True,
                    },
                    booking_state=booking_state,
                )
                second = await _dispatch_tool(
                    "book_meeting",
                    {
                        "booking_operation_id": operation_id,
                        "explicit_confirmation": True,
                    },
                    booking_state=booking_state,
                )

            self.assertTrue(first["success"])
            self.assertEqual(first["event_id"], "evt_123")
            self.assertTrue(second["success"])
            self.assertTrue(second["idempotent_replay"])
            self.assertEqual(len(calls), 1)

        asyncio.run(run())

    def test_prepare_booking_requires_phone_decision(self):
        async def run():
            result = await _dispatch_tool(
                "prepare_booking",
                {
                    "title": "Meeting with Ada Lovelace",
                    "start_iso": "2026-06-08T09:00:00+02:00",
                    "end_iso": "2026-06-08T09:30:00+02:00",
                    "visitor_name": "Ada Lovelace",
                    "topic": "Planning",
                    "meeting_type": "business",
                },
                booking_state=BookingSession(),
            )

            self.assertFalse(result["success"])
            self.assertIn("phone_collection_declined_or_confirmed_phone", result["missing"])

        asyncio.run(run())


class ObservabilityTests(unittest.TestCase):
    def test_redaction_removes_invites_and_phone_numbers(self):
        payload = redact_value({
            "invite": "12345678-1234-1234-1234-123456789abc",
            "text": "Meine Nummer ist +49 171 1234567",
            "nested": {"visitor_phone": "+491711234567"},
        })

        self.assertEqual(payload["invite"], "[redacted]")
        self.assertNotIn("+49 171 1234567", payload["text"])
        self.assertEqual(payload["nested"]["visitor_phone"], "[redacted_phone]")

    def test_trace_logger_writes_local_jsonl(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                trace = TraceLogger.for_invite("12345678-1234-1234-1234-123456789abc")
                trace.firestore_enabled = False
                trace.local_dir = Path(tmp)

                await trace.start_session({"visitor_phone": "+491711234567"})
                await trace.event("assistant_first_audio", {"bytes": 12})
                await trace.end_session("completed")

                path = Path(tmp) / f"{trace.session_id}.jsonl"
                records = [json.loads(line) for line in path.read_text().splitlines()]

            self.assertEqual(records[0]["invite_hash"], hash_token("12345678-1234-1234-1234-123456789abc"))
            self.assertIn("session_start", [record.get("type") for record in records])
            self.assertNotIn("+491711234567", json.dumps(records))

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
